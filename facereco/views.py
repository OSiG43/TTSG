import os
import zipfile

from celery.result import AsyncResult, GroupResult
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render, get_object_or_404

from .forms import DemandForm
from .models import Directory, Demand
from .tasks import task_indexing_directory, encode_demand_photo, task_search_photos


def index(request):
    if request.method == "POST":
        form = DemandForm(request.POST, request.FILES)
        if form.is_valid():
            form.save()

            encode_demand_photo.apply_async(args=[form.instance.id])

            return render(request, "facereco/index.html", {"success": True})
    else:
        form = DemandForm()
    return render(request, "facereco/index.html", {"form": form})


def download(request, request_token):

    demand = get_object_or_404(Demand, request_token=request_token)
    if not demand.is_processed:
        return HttpResponse("La demande n'est pas encore traitée") #Todo remplacer avec une jolie template

    photos = demand.photos.all()

    if len(photos) == 0:
        return HttpResponse("Aucune photo trouvée pour cette demande") #Todo remplacer avec une jolie template

    response = HttpResponse(content_type='application/zip')

    try:

        with zipfile.ZipFile(response, 'w') as zf:
            for photo in photos:
                zf.write(photo.path, os.path.basename(photo.path))

    except FileNotFoundError:
        return HttpResponse("Les photos ne sont plus disponibles")  # TODO remplacer avec une jolie template

    response['Content-Disposition'] = 'attachment; filename="photos_{}"'.format(demand.name + ".zip")

    return response


@staff_member_required
def indexingDirectory(request, directory_id):
    directory = get_object_or_404(Directory, pk=directory_id)
    if directory.indexing_status == "INDEXING":
        return HttpResponse("Indexing in progress", status=409)

    task_indexing_directory.apply_async(args=[directory_id])

    return HttpResponse("Indexing started")


@staff_member_required
def indexingDirectoryProgress(request, directory_id):
    directory = get_object_or_404(Directory, pk=directory_id)

    return JsonResponse({
        "status": directory.indexing_status,
        "total_photos": directory.total_photos,
        "indexed_photos": directory.indexed_photos
    })

@staff_member_required
def forceSearching(request, directory_id):
    directory = get_object_or_404(Directory, pk=directory_id)
    if directory.indexing_status != "INDEXED":
        return JsonResponse({'status': 'ERROR', 'message': 'Directory not indexed'})

    demands_waiting_for_search = Demand.objects.filter(Q(processing_status=Demand.WAITING_FOR_SEARCH)
                                                       & Q(face_encoding__isnull=False)
                                                       & Q(directory__pk=directory_id))
    if len(demands_waiting_for_search) == 0:
        return JsonResponse({'status': 'NOTHING_TO_SEARCH', 'message': 'No demand to search'})

    task_search_photos.apply_async(args=[directory_id])

    return JsonResponse({'status': 'STARTED', 'message': 'Search started'})


@staff_member_required
def rerunFaceEncoding(request, demand_id):
    demand = get_object_or_404(Demand, pk=demand_id)
    if demand.photo is None:
        return JsonResponse({'status': 'ERROR', 'message': 'Demand has no photo'})

    encode_demand_photo.apply_async(args=[demand_id])

    return JsonResponse({'status': 'STARTED', 'message': 'Encoding started'})



