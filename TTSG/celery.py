import os

from celery import Celery, chord
from kombu import uuid

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'TTSG.settings')

app = Celery('TTSG')


# - namespace='CELERY' implique que tout les paramètres liés à celery
#   doivent avoir le préfixe `CELERY_`.
app.config_from_object('django.conf:settings', namespace='CELERY')

# Chargement des taches automatiquement depuis les apps django enregitrées.
app.autodiscover_tasks()


class ProgressChord(chord):

    def __call__(self, body=None, **kwargs):
        _chord = self.type
        body = (body or self.kwargs['body']).clone()
        kwargs = dict(self.kwargs, body=body, **kwargs)
        if _chord.app.conf.CELERY_ALWAYS_EAGER:
            return self.apply((), kwargs)
        callback_id = body.options.setdefault('task_id', uuid())
        r = _chord(**kwargs)
        return _chord.AsyncResult(callback_id), r
