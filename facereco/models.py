import os.path
import uuid

from celery.result import GroupResult
from django.db import models


#Modele qui contient les demandes de reconnaissance faciale
from django.db.models import CASCADE

from TTSG.settings import BASE_PATH_FOR_DIRECTORIES


class Demand(models.Model):
    NOT_PROCESSED = "NOT_PROCESSED"
    FACE_ENCODING = "FACE_ENCODING"
    NO_FACE_FOUND = "NO_FACE_FOUND"
    WAITING_FOR_SEARCH = "WAITING_FOR_SEARCH"
    PROCESSING = "PROCESSING"
    PROCESSED = "PROCESSED"
    STATES = [
        (NOT_PROCESSED, "NOT_PROCESSED"),
        (FACE_ENCODING, "FACE_ENCODING"),
        (NO_FACE_FOUND, "NO_FACE_FOUND"),
        (WAITING_FOR_SEARCH, "WAITING_FOR_SEARCH"),
        (PROCESSING, "PROCESSING"),
        (PROCESSED, "PROCESSED"),
    ]

    name = models.CharField(max_length=200)
    first_name = models.CharField(max_length=200)
    email = models.EmailField(max_length=200)
    photo = models.ImageField(upload_to='images/', blank=True)
    date = models.DateTimeField(auto_now_add=True)
    processing_status = models.CharField(max_length=200, default=NOT_PROCESSED, choices=STATES)
    directory = models.ForeignKey('directory', on_delete=CASCADE)
    photos = models.ManyToManyField('photo', blank=True)
    request_token = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    face_encoding = models.JSONField(blank=True, null=True)
    search_task_id = models.CharField(max_length=200, blank=True, null=True, default=None)

    def delete(self, using=None, keep_parents=False):

        self.photo.delete()
        super().delete()

    @property
    def is_processed(self):
        return self.processing_status == "PROCESSED"



class Directory(models.Model):
    name = models.CharField(max_length=200)
    path = models.FilePathField(path=BASE_PATH_FOR_DIRECTORIES, allow_files=False, allow_folders=True)
    is_visible = models.BooleanField(default=True)
    indexing_task_id = models.CharField(max_length=200, blank=True, null=True, default=None)
    total_photos = models.IntegerField(default=0)
    #date d'indexation
    last_indexing_date = models.DateTimeField(default=None, blank=True, null=True)

    @property
    def indexed_photos(self):
        return len(Photo.objects.filter(directory=self))

    @property
    def indexing_status(self):
        if self.total_photos == 0:
            return "NOT_INDEXED"
        elif self.total_photos == self.indexed_photos:
            return "INDEXED"
        else:
            res = GroupResult.restore(self.indexing_task_id)
            if res is not None:
                for child in res.children:
                    if child.status == "FAILURE" or child.status == "RETRY":
                        return "ERROR"

                return "INDEXING"
            return "ERROR"

    def __str__(self):
        return self.name


class Photo(models.Model):
    directory = models.ForeignKey('directory', on_delete=CASCADE)
    path = models.CharField(max_length=200)
    face_encodings = models.JSONField()

    def __str__(self):
        if self.path is not None and self.path != "" and self.directory is not None:
            return self.directory.name + ": " + os.path.basename(str(self.path))
        else:
            return "Photo id="+str(self.pk)

