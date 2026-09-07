"""Celery wiring and task-level behaviour."""

import uuid

import pytest

from app.core.config import get_settings
from app.worker.celery_app import celery_app
from app.worker.tasks import JobNotFoundError, screen_job


class TestCeleryConfiguration:
    def test_broker_and_backend_are_redis(self):
        settings = get_settings()

        assert celery_app.conf.broker_url == settings.broker_url
        assert celery_app.conf.result_backend == settings.result_backend
        assert celery_app.conf.broker_url.startswith("redis://")

    def test_screening_task_is_registered(self):
        assert "app.worker.tasks.screen_job" in celery_app.tasks

    def test_task_is_bound_to_this_app_not_celery_default(self):
        """A @shared_task resolves to Celery's default app in the API process, whose
        broker is amqp:// — dispatch then fails with a RabbitMQ connection error."""
        assert screen_job.app is celery_app
        assert screen_job.app.conf.broker_url.startswith("redis://")

    def test_prefetch_is_one(self):
        """Screening tasks are long; a worker should hold one, not a queueful."""
        assert celery_app.conf.worker_prefetch_multiplier == 1

    def test_time_limits_are_configured(self):
        settings = get_settings()

        assert celery_app.conf.task_time_limit == settings.celery_task_time_limit
        assert celery_app.conf.task_soft_time_limit < celery_app.conf.task_time_limit

    def test_json_only_serialization(self):
        assert celery_app.conf.task_serializer == "json"
        assert celery_app.conf.accept_content == ["json"]


class TestWorkerProcessInit:
    """Spec section 2: the model loads once per worker process, not per task."""

    def test_init_hook_warms_every_singleton(self, monkeypatch):
        from app.worker import celery_app as celery_module

        calls: list[str] = []
        monkeypatch.setattr(
            "app.services.embeddings.get_embedding_model",
            lambda: calls.append("model"),
        )
        monkeypatch.setattr("app.services.resume_parser.get_nlp", lambda: calls.append("nlp"))
        monkeypatch.setattr(
            "app.services.skills.get_skill_extractor", lambda: calls.append("skills")
        )
        monkeypatch.setattr("app.db.sync_session.reset_sync_engine", lambda: calls.append("engine"))

        celery_module.init_worker_process()

        assert calls == ["engine", "model", "nlp", "skills"]

    def test_engine_is_reset_before_models_load(self, monkeypatch):
        """The pool must be rebuilt after the fork, before anything uses it."""
        from app.worker import celery_app as celery_module

        order: list[str] = []
        monkeypatch.setattr("app.db.sync_session.reset_sync_engine", lambda: order.append("reset"))
        monkeypatch.setattr(
            "app.services.embeddings.get_embedding_model", lambda: order.append("model")
        )
        monkeypatch.setattr("app.services.resume_parser.get_nlp", lambda: None)
        monkeypatch.setattr("app.services.skills.get_skill_extractor", lambda: None)

        celery_module.init_worker_process()

        assert order.index("reset") < order.index("model")


class TestSyncEngine:
    def test_engine_is_a_singleton(self):
        from app.db.sync_session import get_sync_engine

        assert get_sync_engine() is get_sync_engine()

    def test_reset_clears_the_cache(self):
        from app.db import sync_session

        first = sync_session.get_sync_engine()
        sync_session.reset_sync_engine()
        second = sync_session.get_sync_engine()

        assert first is not second
        sync_session.reset_sync_engine()


class TestScreenJobTask:
    async def test_missing_job_raises(self, celery_eager):
        with pytest.raises(JobNotFoundError):
            screen_job.apply(args=[str(uuid.uuid4())]).get()

    async def test_returns_a_processing_summary(self, client, celery_eager):
        from tests.factories import SAMPLE_RESUME, make_pdf_bytes

        response = await client.post(
            "/jobs",
            data={"jd_text": "Python and FastAPI engineer."},
            files=[("resumes", ("priya.pdf", make_pdf_bytes(SAMPLE_RESUME), "application/pdf"))],
        )
        job_id = response.json()["job_id"]

        status = (await client.get(f"/jobs/{job_id}")).json()

        assert status["processed_resumes"] == 1
        assert status["failed_resumes"] == 0


class TestDispatchFailure:
    async def test_broker_down_returns_503(self, client, monkeypatch):
        """If the task cannot be queued, say so rather than accepting silently."""
        from tests.factories import SAMPLE_RESUME, make_pdf_bytes

        def dead_delay(*_args, **_kwargs):
            raise OSError("simulated broker outage")

        monkeypatch.setattr("app.api.routes.jobs.screen_job.delay", dead_delay)

        response = await client.post(
            "/jobs",
            data={"jd_text": "Python engineer."},
            files=[("resumes", ("priya.pdf", make_pdf_bytes(SAMPLE_RESUME), "application/pdf"))],
        )

        assert response.status_code == 503
        assert "broker" in response.json()["detail"].lower()

    async def test_job_is_marked_failed_when_dispatch_fails(self, client, db_session, monkeypatch):
        from sqlalchemy import select

        from app.models import Job, JobStatus
        from tests.factories import SAMPLE_RESUME, make_pdf_bytes

        def dead_delay(*_args, **_kwargs):
            raise OSError("simulated broker outage")

        monkeypatch.setattr("app.api.routes.jobs.screen_job.delay", dead_delay)

        await client.post(
            "/jobs",
            data={"jd_text": "Python engineer."},
            files=[("resumes", ("priya.pdf", make_pdf_bytes(SAMPLE_RESUME), "application/pdf"))],
        )

        job = (await db_session.execute(select(Job))).scalars().one()

        assert job.status == JobStatus.FAILED
        assert "queue" in (job.error or "").lower()
