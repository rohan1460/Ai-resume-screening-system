"""Structured logging at lifecycle points, and Celery retry behaviour.

Logs are asserted through structlog's capture facility rather than by scraping text,
so the assertions are about the event name and its fields — the things an operator
actually queries on.
"""

import uuid

import pytest
import structlog
from sqlalchemy.exc import OperationalError

from app.services.storage import StorageError
from app.worker import tasks
from tests.factories import SAMPLE_RESUME, make_pdf_bytes

JD_TEXT = "Backend engineer with Python, FastAPI, PostgreSQL and Docker."
CORRUPT = b"%PDF-1.4 not a real document"


@pytest.fixture
def captured_logs():
    """Collect structlog events emitted during the test."""
    original = structlog.get_config()
    capture = structlog.testing.LogCapture()
    structlog.configure(processors=[capture])
    yield capture.entries
    structlog.configure(**original)


def events(entries: list[dict], name: str) -> list[dict]:
    return [e for e in entries if e.get("event") == name]


async def _run_job(client, *files, jd: str = JD_TEXT) -> str:
    payload = [("resumes", (n, d, "application/octet-stream")) for n, d in files]
    response = await client.post("/jobs", data={"jd_text": jd}, files=payload)
    assert response.status_code == 201, response.text
    job_id = response.json()["job_id"]
    for _ in range(100):
        if (await client.get(f"/jobs/{job_id}")).json()["status"] in {"completed", "failed"}:
            break
    return job_id


class TestLifecycleLogging:
    async def test_job_queued_is_logged_with_context(self, client, captured_logs):
        job_id = await _run_job(client, ("a.pdf", make_pdf_bytes(SAMPLE_RESUME)))

        queued = events(captured_logs, "job.queued")

        assert len(queued) == 1
        assert queued[0]["job_id"] == job_id
        assert queued[0]["total_resumes"] == 1

    async def test_task_started_is_logged(self, client, captured_logs):
        job_id = await _run_job(client, ("a.pdf", make_pdf_bytes(SAMPLE_RESUME)))

        started = events(captured_logs, "job.started")

        assert len(started) == 1
        assert started[0]["job_id"] == job_id
        assert started[0]["total_resumes"] == 1

    async def test_every_processed_resume_is_logged(self, client, captured_logs):
        good = make_pdf_bytes(SAMPLE_RESUME)
        await _run_job(client, ("a.pdf", good), ("b.pdf", good), ("c.pdf", good))

        processed = events(captured_logs, "resume.processed")

        assert len(processed) == 3
        assert {e["filename"] for e in processed} == {"a.pdf", "b.pdf", "c.pdf"}
        assert [e["position"] for e in processed] == [1, 2, 3]

    async def test_processed_log_carries_useful_fields(self, client, captured_logs):
        await _run_job(client, ("a.pdf", make_pdf_bytes(SAMPLE_RESUME)))

        entry = events(captured_logs, "resume.processed")[0]

        assert entry["skills_found"] > 0
        assert entry["chars_extracted"] > 0
        assert entry["duration_ms"] >= 0
        assert entry["candidate_id"]

    async def test_failed_resume_is_logged_with_its_reason(self, client, captured_logs):
        await _run_job(client, ("good.pdf", make_pdf_bytes(SAMPLE_RESUME)), ("bad.pdf", CORRUPT))

        failures = events(captured_logs, "resume.failed")

        assert len(failures) == 1
        assert failures[0]["filename"] == "bad.pdf"
        assert "could not read pdf" in failures[0]["error"].lower()

    async def test_job_completed_reports_the_tally(self, client, captured_logs):
        await _run_job(client, ("good.pdf", make_pdf_bytes(SAMPLE_RESUME)), ("bad.pdf", CORRUPT))

        completed = events(captured_logs, "job.completed")

        assert len(completed) == 1
        assert completed[0]["processed"] == 1
        assert completed[0]["failed"] == 1
        assert completed[0]["total"] == 2
        assert completed[0]["duration_ms"] >= 0

    async def test_every_task_log_carries_the_job_id(self, client, captured_logs):
        job_id = await _run_job(client, ("a.pdf", make_pdf_bytes(SAMPLE_RESUME)))

        for name in ("job.started", "resume.processed", "job.completed"):
            entries = events(captured_logs, name)
            assert entries, name
            assert all(e["job_id"] == job_id for e in entries), name

    async def test_rerank_is_logged(self, client, captured_logs):
        job_id = await _run_job(client, ("a.pdf", make_pdf_bytes(SAMPLE_RESUME)))

        await client.post(f"/jobs/{job_id}/rerank", json={"jd_text": "React developer."})

        reranked = events(captured_logs, "job.reranked")
        assert len(reranked) == 1
        assert reranked[0]["job_id"] == job_id
        assert reranked[0]["run_id"]


class TestRetryClassification:
    """Only infrastructure faults retry. A bad resume must never be retried —
    it will fail identically every time and burn the whole backoff budget."""

    @pytest.mark.parametrize(
        "exc",
        [
            OperationalError("stmt", {}, Exception("db restarting")),
            StorageError("minio blip"),
        ],
    )
    def test_transient_infrastructure_errors_are_retryable(self, exc):
        assert isinstance(exc, tasks.RETRYABLE)

    @pytest.mark.parametrize(
        "exc",
        [
            ValueError("bad input"),
            tasks.ExtractionError("corrupt resume"),
            tasks.JobNotFoundError("gone"),
        ],
    )
    def test_application_errors_are_not_retryable(self, exc):
        assert not isinstance(exc, tasks.RETRYABLE)


class TestRetryBehaviour:
    def _job_id(self) -> str:
        return str(uuid.uuid4())

    def test_transient_failure_schedules_a_retry_with_backoff(self, celery_eager, monkeypatch):
        """Celery's own Retry carries the countdown, so assert on the real thing."""
        from celery.exceptions import Retry

        from app.core.config import get_settings

        def boom(*_args, **_kwargs):
            raise StorageError("simulated storage outage")

        monkeypatch.setattr(tasks, "_run", boom)

        with pytest.raises(Retry) as exc:
            tasks.screen_job.apply(args=[self._job_id()], throw=True).get()

        assert exc.value.when == get_settings().celery_retry_backoff  # backoff * 2**0

    def test_backoff_grows_exponentially(self, celery_eager, monkeypatch):
        from celery.exceptions import Retry

        from app.core.config import get_settings

        base = get_settings().celery_retry_backoff

        def boom(*_args, **_kwargs):
            raise StorageError("still down")

        monkeypatch.setattr(tasks, "_run", boom)

        countdowns = []
        for attempt in range(3):
            with pytest.raises(Retry) as exc:
                tasks.screen_job.apply(args=[self._job_id()], retries=attempt, throw=True).get()
            countdowns.append(exc.value.when)

        assert countdowns == [base * 1, base * 2, base * 4]

    def test_a_non_retryable_error_marks_the_job_failed(self, client, celery_eager, monkeypatch):
        marked: list[tuple] = []

        def boom(*_args, **_kwargs):
            raise ValueError("permanent problem")

        monkeypatch.setattr(tasks, "_run", boom)
        monkeypatch.setattr(tasks, "_mark_failed", lambda jid, err: marked.append((jid, err)))

        with pytest.raises(ValueError):
            tasks.screen_job.apply(args=[self._job_id()], throw=True).get()

        assert len(marked) == 1
        assert "permanent problem" in marked[0][1]

    def test_exhausted_retries_mark_the_job_failed(self, celery_eager, monkeypatch):
        from app.core.config import get_settings

        marked: list[tuple] = []

        def boom(*_args, **_kwargs):
            raise StorageError("permanently down")

        monkeypatch.setattr(tasks, "_run", boom)
        monkeypatch.setattr(tasks, "_mark_failed", lambda jid, err: marked.append((jid, err)))
        exhausted = get_settings().celery_max_retries

        with pytest.raises(StorageError):
            tasks.screen_job.apply(args=[self._job_id()], retries=exhausted, throw=True).get()

        assert len(marked) == 1
        assert "retries exhausted" in marked[0][1].lower()

    def test_retry_and_exhaustion_are_logged(self, celery_eager, monkeypatch, captured_logs):
        from celery.exceptions import Retry

        from app.core.config import get_settings

        def boom(*_args, **_kwargs):
            raise StorageError("down")

        monkeypatch.setattr(tasks, "_run", boom)
        monkeypatch.setattr(tasks, "_mark_failed", lambda *_a: None)

        with pytest.raises(Retry):
            tasks.screen_job.apply(args=[str(uuid.uuid4())], retries=0, throw=True).get()

        with pytest.raises(StorageError):
            tasks.screen_job.apply(
                args=[str(uuid.uuid4())],
                retries=get_settings().celery_max_retries,
                throw=True,
            ).get()

        assert events(captured_logs, "job.retrying")
        assert events(captured_logs, "job.retries_exhausted")

    async def test_a_bad_resume_never_triggers_a_retry(self, client, captured_logs):
        """The point of the classification: corrupt input fails once, not four times."""
        await _run_job(client, ("good.pdf", make_pdf_bytes(SAMPLE_RESUME)), ("bad.pdf", CORRUPT))

        assert not events(captured_logs, "job.retrying")
        assert not events(captured_logs, "job.retries_exhausted")
        assert len(events(captured_logs, "resume.failed")) == 1
        assert len(events(captured_logs, "job.completed")) == 1
