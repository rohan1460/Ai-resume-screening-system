"""Worker entrypoint.

Placeholder. The queue is **Celery + Redis** (spec section 2), wired up in Phase 2
(build order items 8-11). Phase 1 processes resumes synchronously inside the API, so
nothing is dispatched here yet.

When built, this package holds ``celery_app.py`` (app instance, broker/backend config)
and ``tasks.py`` (the screening task). The sentence-transformers model must be loaded
**once per worker process** and reused, never per task.
"""

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger


def main() -> None:
    settings = get_settings()
    configure_logging(settings)
    get_logger(__name__).info("worker.placeholder", redis=settings.redis_url)


if __name__ == "__main__":
    main()
