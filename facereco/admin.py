from django.contrib import admin

# Register your models here.
from django.shortcuts import render
from django.template.loader import render_to_string

from .models import Demand, Directory, Photo

@admin.action(description='Reset demand search')
def reset_demand_search(self, request, queryset):
    queryset.update(processing_status=Demand.WAITING_FOR_SEARCH, search_task_id=None)


@admin.register(Demand)
class DemandAdmin(admin.ModelAdmin):
    def rerunEncoding_button(self, obj):

        if obj.id is None:
            return ""
        if obj.photo is None:
            return "No photo to encode"
        if obj.processing_status != Demand.NOT_PROCESSED and obj.processing_status != Demand.NO_FACE_FOUND:
            return "Photo already processed"

        return render_to_string('facereco/force_face_encode_button.html', {'demand': obj})

    rerunEncoding_button.short_description = 'Rerun face encoding'

    search_fields = ('id', 'name', 'first_name', 'email', 'directory__name')

    list_display = ('name', 'first_name', 'email', 'directory', 'processing_status', 'date')
    readonly_fields = ('id', 'date', 'request_token', 'face_encoding', 'search_task_id', 'rerunEncoding_button')
    list_filter = ('processing_status', 'directory')

    actions = [reset_demand_search]


@admin.action(description='Reset directory')
def reset_directory(self, request, queryset):
    photos = Photo.objects.filter(directory__in=queryset)
    photos.delete()
    queryset.update(indexing_task_id=None, total_photos=0, last_indexing_date=None)


@admin.register(Directory)
class DirectoryAdmin(admin.ModelAdmin):

    def index_button(self, obj):
        return render_to_string('facereco/indexing_button.html', {'directory': obj})

    def force_search_button(self, obj):
        if obj.id is None:
            return ""

        already_searching = Demand.objects.filter(directory=obj, processing_status=Demand.PROCESSING).exists()

        return render_to_string('facereco/force_search_button.html',
                                {'already_searching': already_searching, 'directory': obj})

    index_button.short_description = 'indexing'

    list_display = ('name', 'path', 'is_visible', 'total_photos', 'index_button', 'indexing_status',
                    'last_indexing_date')

    readonly_fields = ('indexing_task_id', 'total_photos', 'indexed_photos', 'indexing_status', 'last_indexing_date',
                       'index_button', 'force_search_button',)

    actions = [reset_directory]
    search_fields = ('name', 'path')


@admin.register(Photo)
class PhotoAdmin(admin.ModelAdmin):
    list_display = ('directory', 'path')
    readonly_fields = ('face_encodings', 'path', 'directory')
    search_fields = ('id', 'directory__name', 'path')

    list_filter = ('directory',)






