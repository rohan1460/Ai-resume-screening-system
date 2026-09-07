"""Synchronous database access for Celery tasks.

Celery tasks are ordinary sync callables, so they get a sync engine rather than the
API's async one. The URL is identical — the psycopg dialect drives both — but the two
engines keep separate connection pools, which is what we want across processes.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings


@lru_cache(maxsize=1)
def get_sync_engine() -> Engine:
    settings = get_settings()
    return create_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=settings.worker_db_pool_size,
        max_overflow=settings.worker_db_max_overflow,
        echo=False,
    )


@lru_cache(maxsize=1)
def get_sync_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_sync_engine(), expire_on_commit=False)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope: commit on success, roll back on failure."""
    session = get_sync_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def reset_sync_engine() -> None:
    """Drop the cached engine.

    Celery's prefork children must not inherit a connection pool created before the
    fork — shared sockets across processes corrupt each other.
    """
    if get_sync_engine.cache_info().currsize:
        get_sync_engine().dispose()
    get_sync_engine.cache_clear()
    get_sync_sessionmaker.cache_clear()
