import logging.config
import os

from celery import Celery
from celery.signals import setup_logging
from django.conf import settings

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.prod")

app = Celery("dokkimashora")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()


@setup_logging.connect
def configure_worker_logging(**kwargs) -> None:
    """Use the project's stdout formatter/filter in Celery workers too."""
    logging.config.dictConfig(settings.LOGGING)
