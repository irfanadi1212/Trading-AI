"""
app/workers/celery_app.py
=============================
Konfigurasi Celery dengan Redis sebagai broker & result backend.
Reliability settings: task tidak hilang jika worker crash, timeout
keras agar task macet tidak menyandera worker selamanya.
"""

from celery import Celery
from kombu import Queue

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "alchemist_analysis",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
)

celery_app.conf.update(
    # --- Reliability ---
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_track_started=True,
    broker_connection_retry_on_startup=True,

    # --- Timeout (task analisa berat via CrewAI) ---
    task_time_limit=600,       # hard kill 10 menit
    task_soft_time_limit=540,  # soft warning 1 menit sebelum hard kill

    # --- Retry default ---
    task_default_retry_delay=10,
    task_max_retries=3,

    # --- Result backend ---
    result_expires=3600,

    # --- Serialization ---
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Jakarta",
    enable_utc=True,

    # --- Queue terpisah: analisa berat vs notifikasi ringan ---
    task_queues=(
        Queue("analysis_heavy"),
        Queue("notifications_light"),
    ),
    task_default_queue="analysis_heavy",
)

celery_app.autodiscover_tasks(["app.workers"])