"""Test fixtures.

Integration tests run against a **real Postgres** (a separate ``*_test`` database on
the same server as dev), because pgvector columns and JSONB have no faithful SQLite
equivalent.

Embeddings are stubbed with a deterministic fake: the real model adds ~90MB of
download and seconds per call for no extra coverage, since the scoring maths is
already unit-tested against exact vectors. The real model is exercised by the seed
script and the manual demo.
"""

import hashlib
from collections.abc import AsyncIterator

import psycopg
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.db.base import Base
from app.db.session import get_session
from app.main import create_app

TEST_DB_SUFFIX = "_test"


def _test_database_name() -> str:
    return get_settings().postgres_db + TEST_DB_SUFFIX


def _admin_dsn() -> str:
    s = get_settings()
    return f"postgresql://{s.postgres_user}:{s.postgres_password}@{s.postgres_host}:{s.postgres_port}/postgres"


def _test_database_url() -> str:
    s = get_settings()
    return (
        f"postgresql+psycopg://{s.postgres_user}:{s.postgres_password}"
        f"@{s.postgres_host}:{s.postgres_port}/{_test_database_name()}"
    )


def _ensure_test_database() -> None:
    name = _test_database_name()
    with psycopg.connect(_admin_dsn(), autocommit=True) as conn:
        exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone()
        if not exists:
            conn.execute(f'CREATE DATABASE "{name}"')


@pytest.fixture(scope="session", autouse=True)
def isolate_settings_from_local_env():
    """Pin the settings the suite runs under, whatever the developer's .env holds.

    Settings reads .env, so without this a local ``API_KEY=…`` (or any other override)
    silently changes what the tests exercise — a key set for a manual demo turned the
    whole suite red once. Tests that need a different value set it explicitly.
    """
    import os

    from app.core.config import get_settings

    overrides = {"API_KEY": "", "APP_ENV": "local"}
    previous = {name: os.environ.get(name) for name in overrides}
    os.environ.update(overrides)
    get_settings.cache_clear()

    yield

    for name, value in previous.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value
    get_settings.cache_clear()


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="session")
async def engine():
    pytest.importorskip("psycopg")
    try:
        _ensure_test_database()
    except psycopg.OperationalError as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"Postgres not reachable for integration tests: {exc}")

    engine = create_async_engine(_test_database_url(), poolclass=None)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def db_session(engine) -> AsyncIterator:
    """A session per test, with every table emptied first."""
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        tables = ", ".join(f'"{t.name}"' for t in reversed(Base.metadata.sorted_tables))
        await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    async with factory() as session:
        yield session


class FakeStorage:
    """In-memory stand-in for MinIO/S3."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def ensure_bucket(self) -> None:
        return None

    def build_key(self, job_id, filename: str) -> str:
        return f"jobs/{job_id}/{filename}"

    def put(self, key: str, data: bytes, content_type: str | None = None) -> str:
        self.objects[key] = data
        return key

    def get(self, key: str) -> bytes:
        return self.objects[key]


def fake_embed_texts(texts: list[str]) -> list[list[float]]:
    """Deterministic unit-length vectors derived from the text.

    Similar strings do NOT get similar vectors, so tests must not assert on semantic
    ranking — only on plumbing, persistence and explainability fields.
    """
    dim = get_settings().embedding_dim
    vectors = []
    for value in texts:
        digest = hashlib.sha256(value.encode("utf-8")).digest()
        raw = [(digest[i % len(digest)] / 255.0) - 0.5 for i in range(dim)]
        norm = sum(x * x for x in raw) ** 0.5 or 1.0
        vectors.append([x / norm for x in raw])
    return vectors


@pytest.fixture(autouse=True)
def stub_heavy_dependencies(monkeypatch, request):
    """Swap the embedding model and object storage for fast deterministic doubles.

    ``@pytest.mark.no_stubs`` opts out entirely; ``@pytest.mark.real_storage`` keeps
    embeddings stubbed but lets the test hit a live MinIO, so the storage path gets
    exercised for real without paying for the model.
    """
    if "no_stubs" in request.keywords:
        return None

    fake_embed_text = lambda text: fake_embed_texts([text])[0]  # noqa: E731
    # Patch every module that imported these by name, the worker included — the task
    # body is what the API tests now exercise.
    monkeypatch.setattr("app.services.embeddings.embed_texts", fake_embed_texts)
    monkeypatch.setattr("app.services.screening.embed_texts", fake_embed_texts)
    monkeypatch.setattr("app.api.routes.jobs.embed_text", fake_embed_text)
    monkeypatch.setattr("app.worker.tasks.embed_texts", fake_embed_texts)
    monkeypatch.setattr("app.worker.tasks.embed_text", fake_embed_text)

    if "real_storage" in request.keywords:
        return None

    storage = FakeStorage()
    monkeypatch.setattr("app.api.routes.jobs.get_storage", lambda: storage)
    monkeypatch.setattr("app.worker.tasks.get_storage", lambda: storage)
    return storage


@pytest.fixture
def celery_eager(engine, monkeypatch):
    """Run Celery tasks inline, against the test database.

    ``task_always_eager`` executes the task during ``.delay()``, so the API tests
    exercise the real task body — parsing, embedding, scoring, progress updates —
    without needing a broker or a worker process. The task's sync session factory is
    pointed at the test database and told to reuse the test connection, so the API's
    async session and the task's sync session see each other's rows.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db import sync_session
    from app.worker.celery_app import celery_app

    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True

    sync_engine = create_engine(_test_database_url().replace("+psycopg", "+psycopg"), future=True)
    factory = sessionmaker(bind=sync_engine, expire_on_commit=False)
    monkeypatch.setattr(sync_session, "get_sync_sessionmaker", lambda: factory)

    yield

    celery_app.conf.task_always_eager = False
    sync_engine.dispose()


@pytest.fixture
async def client(engine, db_session, celery_eager) -> AsyncIterator[AsyncClient]:
    """HTTP client with a fresh DB session per request.

    A session per request (rather than one shared with the test) matters now that the
    Celery task writes on its own connection: reusing one long-lived session would
    serve the test stale rows the worker has already updated.
    """
    app = create_app()
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def override_get_session():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()
