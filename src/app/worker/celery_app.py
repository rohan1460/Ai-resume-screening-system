"""Celery application, with Redis as broker and result backend.

The expensive singletons — the sentence-transformers model and the spaCy pipeline —
are loaded **once per worker process** in the ``worker_process_init`` hook rather than
lazily inside a task. Two reasons: the first task of each child would otherwise pay
several seconds of model load, and preloading makes the "loaded once" guarantee
explicit instead of an accident of caching.
"""

from celery import Celery
from celery.signals import worker_process_init, worker_process_shutdown

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger

logger = get_logger(__name__)
settings = get_settings()

celery_app = Celery(
    "resume_screening",
    broker=settings.broker_url,
    backend=settings.result_backend,
    include=["app.worker.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=settings.celery_task_time_limit,
    task_soft_time_limit=settings.celery_task_soft_time_limit,
    # Screening is long and not idempotent mid-flight: only hand a task to a worker
    # that is ready for it, and do not silently re-run it if a worker dies.
    worker_prefetch_multiplier=1,
    task_acks_late=False,
    result_expires=settings.celery_result_expires,
)


@worker_process_init.connect
def init_worker_process(**_kwargs: object) -> None:
    """Warm the per-process singletons and rebuild the DB pool after the fork."""
    configure_logging(settings)

    # A pool created before fork would be shared across children; start fresh.
    from app.db.sync_session import reset_sync_engine

    reset_sync_engine()

    from app.services.embeddings import get_embedding_model
    from app.services.resume_parser import get_nlp
    from app.services.skills import get_skill_extractor

    logger.info("worker.warming_up")
    get_embedding_model()
    get_nlp()
    get_skill_extractor()
    logger.info("worker.ready", model=settings.embedding_model)


@worker_process_shutdown.connect
def shutdown_worker_process(**_kwargs: object) -> None:
    from app.db.sync_session import reset_sync_engine

    reset_sync_engine()
    logger.info("worker.shutdown")
