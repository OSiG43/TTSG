from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="index"),
    path("download/<uuid:request_token>", views.download, name="download"),
    path("indexing/<int:directory_id>/", views.indexingDirectory, name="indexingDirectory"),
    path("indexing/<int:directory_id>/progress/", views.indexingDirectoryProgress, name="indexingDirectoryProgress"),
    path("forceSearching/<int:directory_id>/", views.forceSearching, name="forceSearching"),
]
