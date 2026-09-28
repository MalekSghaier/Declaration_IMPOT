from app.celery_app import celery_app


@celery_app.task(name="ping")
def ping(name: str = "monde") -> str:
    return f"pong {name}"