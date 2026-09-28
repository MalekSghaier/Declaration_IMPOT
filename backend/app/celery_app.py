import os

from celery import Celery

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery(
    "declaration",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=["app.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    result_expires=3600,
    # Robustesse pour de gros volumes (milliers de fichiers) :
    task_acks_late=True,              # la tâche n'est confirmée qu'une fois terminée
    task_reject_on_worker_lost=True,  # si le worker meurt, la tâche est remise en file
    worker_prefetch_multiplier=1,     # chaque worker ne réserve qu'une tâche à la fois
)