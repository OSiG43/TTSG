import os
import time
from builtins import type

import numpy
import numpy as np

from django.conf import settings
from django.core.mail import EmailMultiAlternatives, get_connection
from django.db.models import Q
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.html import strip_tags

if settings.WITH_FACE_RECOGNITION:
    import face_recognition

from celery import shared_task, chord

from .models import Directory, Photo, Demand


@shared_task()
def task_error_mail(error, demand_id):
    try:
        demand = Demand.objects.get(pk=demand_id)
    except Demand.DoesNotExist:
        raise Exception("Demand not found", {"demand_id": demand_id})

    text_content = render_to_string("facereco/mails/mail_error.txt", {"error": error, "demand": demand})
    html_content = render_to_string("facereco/mails/mail_error.html", {"error": error, "demand": demand})
    msg = EmailMultiAlternatives(demand.directory.name+" - Erreur de reconnaissance faciale", text_content, settings.EMAIL_HOST_USER, [demand.email])
    msg.attach_alternative(html_content, "text/html")
    msg.send()


@shared_task()
def encode_demand_photo(demand_id):

    queryset = Demand.objects.filter(pk=demand_id)
    if len(queryset) != 1:
        raise Exception("Demand not found", {"demand_id": demand_id})
    demand = queryset[0]

    demand.processing_status = Demand.FACE_ENCODING
    demand.save()

    if demand.photo is None:
        raise Exception("No photo found", {"demand_id": demand_id})

    if settings.WITH_FACE_RECOGNITION:
        photo = face_recognition.api.load_image_file(demand.photo.path)
        face_locations = face_recognition.face_locations(photo)
        face_encodings = face_recognition.face_encodings(photo, face_locations)
        if len(face_encodings) == 0:
            demand.processing_status = Demand.NO_FACE_FOUND
            demand.save()
            task_error_mail.delay("No face found", demand_id)
            return {"demand_id": demand_id, "error": "No face found in photo"}

        face_encoding = face_encodings[0]  # On ne prend que le premier visage trouvé
        demand.face_encoding = face_encoding.tolist() # On convertit le tableau numpy en liste python pour pouvoir le serialiser
        demand.processing_status = Demand.WAITING_FOR_SEARCH
        demand.save()

        demand.photo.delete()  # On supprime la photo de la demande pour libérer de l'espace disque



    # On vérifie si on a atteint le nombre de demandes en attente de recherche dans le répertoire pour lancer la recherche
    demands_waiting_for_search = Demand.objects.filter(Q(processing_status=Demand.WAITING_FOR_SEARCH)
                                                       & Q(face_encoding__isnull=False)
                                                       & Q(directory__pk=demand.directory.pk))

    if len(demands_waiting_for_search) > settings.FACE_SEARCHING_BATCH_SIZE:
        task_search_photos.delay(demand.directory.pk)


# Cette tâche est appelée toutes les 5 minutes pour vérifier si il y a des demandes en attente de recherche
@shared_task()
def task_check_if_search_photos_needed():
    # On vérifie si il y a des demandes en attente de recherche plus vieilles que le temps d'attente autorisé
    max_date = timezone.now() - timezone.timedelta(minutes=settings.MAX_WAITING_TIME_BEFORE_SEARCH)
    demands_waiting_for_search = Demand.objects.filter(Q(processing_status=Demand.WAITING_FOR_SEARCH)
                                                       & Q(face_encoding__isnull=False)
                                                       & Q(date__lte=max_date))
    dirs_ids = demands_waiting_for_search.values_list('directory__pk', flat=True).distinct()
    for dir_id in dirs_ids:
        task_search_photos.delay(dir_id)



@shared_task()
def task_search_photos(dir_id):
    demands_waiting_for_search = Demand.objects.filter(Q(processing_status=Demand.WAITING_FOR_SEARCH)
                                                       & Q(face_encoding__isnull=False)
                                                       & Q(directory__pk=dir_id))

    if len(demands_waiting_for_search) == 0:
        return "No demand to search"

    demands_ids = []
    demands_face_encodings_list = []

    # On rempli la liste des visages encodés des demandes en attente de recherche
    for demand_id, face_encoding in demands_waiting_for_search.values_list('id', 'face_encoding'):
        demands_ids.append(demand_id)
        demands_face_encodings_list.append(numpy.array(face_encoding))

    # On a désormais les visages encodés des demandes en attente de recherche dans la liste demands_face_encodings_list

    # On récupère les photos encodées
    photos = Photo.objects.filter(directory__pk=dir_id)

    parameters_list = [(demands_ids, demands_face_encodings_list, photo.id) for photo in photos]
    # On lance la recherche des visages dans les photos encodées en parallèle avec des chunks de
    # taille FACE_SEARCHING_CHUNK_SIZE
    find_faces_tasks = [task_find_faces.chunks(parameters_list, settings.FACE_SEARCHING_CHUNK_SIZE).group()]
    callback = task_search_ending.si(demands_ids)
    chord(find_faces_tasks)(callback) # Une fois toutes les taches terminées, on lance la tache de fin de recherche
    # (pour mettre un chunks dans un chord, astuce trouvé ici :
    # https://stackoverflow.com/questions/65789241/celery-how-to-use-chunks-in-chord)

    demands_waiting_for_search.update(processing_status=Demand.PROCESSING)

    return f"Search photos in directory n° {dir_id} started."


"""
    Cette fonction permet de trouver les visages présents dans la photo dont l'id est passé en paramètre.

    Elle est appelée par la fonction search_photos qui va lancer la recherche sur toutes les photos du répertoire.
    demand_ids : liste des ids des demandes de reconnaissance faciale
    demands_face_encodings_list : liste des visages encodés des demandes de reconnaissance faciale
    photo_id : id de la photo sur laquelle on va effectuer la recherche
"""
@shared_task()
def task_find_faces(demands_ids, demands_face_encodings_list, photo_id):
    try:
        photo = Photo.objects.get(pk=photo_id)
    except Photo.DoesNotExist:
        return "Photo not found : id = {}".format(photo_id)

    # On transforme les list python en tableaux numpy pour pouvoir les comparer avec les visages encodés.
    photo_face_encodings_list = []
    for encoding_list in photo.face_encodings:
        photo_face_encodings_list.append(numpy.array(encoding_list))

    if settings.WITH_FACE_RECOGNITION:

        # Pour chaque visage présent sur la photo, on appelle la fonction de comparaison qui va indiquer si le visage
        # correspond à un visage recherché ou non.
        # La fonction renvoie une liste de booléens, un booléen par visage recherché.

        # On initialise la liste des booléens à False, a chaque visage, on fera une opération OU avec le résultat de la
        # fonction match de manière a savoir si au moins un visage sur la photo correspond à un visage recherché.
        matches = numpy.full(len(demands_face_encodings_list), False, dtype=bool)

        for photo_face_encoding in photo_face_encodings_list:
            matches |= numpy.array(
                face_recognition.compare_faces(demands_face_encodings_list, photo_face_encoding,
                                               tolerance=settings.FACE_MATCHING_THRESHOLD)
                , dtype=bool)

        # Une fois toutes les comparaisons effectuées, on parcourt la liste des booléens pour savoir si un visage
        # correspondant à une demande a été trouvé.
        # Si oui, on ajoute la photo à la demande.
        for i in range(len(matches)):
            if matches[i]:
                try:
                    demand = Demand.objects.get(pk=demands_ids[i])
                    demand.photos.add(photo)
                    demand.save()
                except Demand.DoesNotExist:
                    # Si la demande n'existe plus, on passe à la suivante
                    continue

    return "ok"

@shared_task()
def task_search_ending(demands_ids):
    emails_list = []

    for demand_id in demands_ids:
        try:
            demand = Demand.objects.get(pk=demand_id)
            demand.processing_status = Demand.PROCESSED
            demand.save()

            if demand.photos.count() == 0:
                html_content = render_to_string('facereco/mails/mail_no_photo_found.html',
                                                {'base_url': settings.BASE_URL, 'demand': demand})
                txt_content = strip_tags(html_content)
                email = EmailMultiAlternatives(demand.directory.name + " - Aucune photo trouvée",
                                               txt_content,
                                               settings.EMAIL_HOST_USER, [demand.email])
                email.attach_alternative(html_content, "text/html")
                emails_list.append(email)

            else:
                html_content = render_to_string('facereco/mails/mail_demand_processed.html',
                                                {'base_url': settings.BASE_URL, 'demand': demand})
                txt_content = strip_tags(html_content)
                email = EmailMultiAlternatives(demand.directory.name+" - Vos photos sont disponibles !",
                                               txt_content,
                                               settings.EMAIL_HOST_USER, [demand.email])
                email.attach_alternative(html_content, "text/html")
                emails_list.append(email)

        except Demand.DoesNotExist:
            # Si la demande n'existe plus, on passe à la suivante
            continue

    # On envoie les mails
    connection = get_connection()
    connection.send_messages(emails_list)

    return "Search ended"




@shared_task(bind=True, max_retries=3)
def task_face_encoding(self, dir_id, img_path):
    try:
        if settings.WITH_FACE_RECOGNITION:
            # On récupère l'image dont on veut encoder les visages
            frame = face_recognition.api.load_image_file(img_path)

            # On encode les visages
            face_locations = face_recognition.face_locations(frame)
            face_encodings = face_recognition.face_encodings(frame, face_locations)

            # On transforme les tableaux numpy en list python pour pouvoir les encoder en JSON.
            face_encodings_list = []
            for face_encoding in face_encodings:
                face_encodings_list.append(face_encoding.tolist())
        else:
            time.sleep(5)
            face_encodings_list = []
        # On sauvegarde les visages encodés dans la base de données
        photo = Photo.objects.create(directory_id=dir_id, path=img_path, face_encodings=face_encodings_list)

        return "ok"
    except Exception as e:
        self.retry()


@shared_task(bind=True)
def task_indexing_directory(self, dir_id):
    # On récupère le répertoire à indexer
    queryset = Directory.objects.filter(pk=dir_id)
    if len(queryset) != 1:
        raise Exception("Directory not found", {"id": dir_id})
    directory = queryset[0]
    path = directory.path

    # On supprime toutes les entrées dans la base photo qui correspondent à la directory (Cas si on réindexe le répertoire)
    indexed_photos = Photo.objects.filter(directory=directory)
    indexed_photos.delete()

    raw_imgs_paths = []
    for (dirpath, dirnames, filenames) in os.walk(path):
        raw_imgs_paths.extend([dirpath + '/' + filename for filename in filenames])

    params_list = [(dir_id, img_path) for img_path in raw_imgs_paths]

    chunk = task_face_encoding.chunks(params_list, settings.INDEXING_CHUNK_SIZE)
    res = chunk.apply_async()
    res.save()

    directory.last_indexing_date = timezone.now()
    directory.total_photos = len(raw_imgs_paths)
    directory.indexing_task_id = res.id
    directory.save()

    return len(raw_imgs_paths)
