"""Per-resume failure isolation and live progress.

Spec section 7: a corrupt, empty, unsupported or text-free resume must mark only that
candidate failed, let the rest of the batch finish, and show up in the job results.
"""

import pytest
from sqlalchemy import select

from app.models import Candidate, CandidateStatus, Job, JobStatus
from tests.factories import SAMPLE_RESUME, make_docx_bytes, make_pdf_bytes

JD_TEXT = "Backend engineer with Python, FastAPI, PostgreSQL, Docker and Kubernetes."

# Valid PDF/DOCX containers whose text is unusable — these pass upload validation and
# must be caught by the worker, not the API.
BLANK_PDF = make_pdf_bytes("")
SCANNED_LOOKALIKE_PDF = make_pdf_bytes("Hi")  # parses, but under the meaningful floor
WHITESPACE_DOCX = make_docx_bytes("   \n   \n  ")
CORRUPT_PDF = b"%PDF-1.4 truncated and invalid, not a real document"


def _files(*items):
    return [("resumes", (name, data, "application/octet-stream")) for name, data in items]


async def _run(client, *items, jd: str = JD_TEXT) -> dict:
    response = await client.post("/jobs", data={"jd_text": jd}, files=_files(*items))
    assert response.status_code == 201, response.text
    job_id = response.json()["job_id"]

    for _ in range(100):
        status = (await client.get(f"/jobs/{job_id}")).json()
        if status["status"] in {"completed", "failed"}:
            break
    return (await client.get(f"/jobs/{job_id}/results")).json()


class TestFailureModesReachingTheWorker:
    """Empty and unsupported files are rejected at upload (400). These four are
    accepted there and can only be caught once the worker reads them."""

    @pytest.mark.parametrize(
        ("filename", "payload", "expected"),
        [
            ("corrupt.pdf", CORRUPT_PDF, "could not read pdf"),
            ("blank.pdf", BLANK_PDF, "no meaningful text"),
            ("scanned.pdf", SCANNED_LOOKALIKE_PDF, "scanned images"),
            ("whitespace.docx", WHITESPACE_DOCX, "no meaningful text"),
        ],
    )
    async def test_bad_resume_fails_alone(self, client, filename, payload, expected):
        results = await _run(
            client, ("good.pdf", make_pdf_bytes(SAMPLE_RESUME)), (filename, payload)
        )

        assert results["processed_resumes"] == 1
        assert results["failed_resumes"] == 1
        assert len(results["results"]) == 1
        assert results["results"][0]["filename"] == "good.pdf"

    @pytest.mark.parametrize(
        ("filename", "payload", "expected"),
        [
            ("corrupt.pdf", CORRUPT_PDF, "could not read pdf"),
            ("scanned.pdf", SCANNED_LOOKALIKE_PDF, "scanned images"),
        ],
    )
    async def test_failure_reason_is_specific(self, client, filename, payload, expected):
        """A recruiter asking "why was this rejected?" needs more than "it failed"."""
        results = await _run(
            client, ("good.pdf", make_pdf_bytes(SAMPLE_RESUME)), (filename, payload)
        )

        failure = next(f for f in results["failures"] if f["filename"] == filename)
        assert expected in failure["error"].lower()


class TestBatchSurvives:
    async def test_several_failures_among_several_successes(self, client):
        results = await _run(
            client,
            ("a_good.pdf", make_pdf_bytes(SAMPLE_RESUME)),
            ("b_corrupt.pdf", CORRUPT_PDF),
            ("c_good.docx", make_docx_bytes(SAMPLE_RESUME)),
            ("d_blank.pdf", BLANK_PDF),
            ("e_good.pdf", make_pdf_bytes(SAMPLE_RESUME)),
        )

        assert results["processed_resumes"] == 3
        assert results["failed_resumes"] == 2
        assert len(results["results"]) == 3
        assert {f["filename"] for f in results["failures"]} == {"b_corrupt.pdf", "d_blank.pdf"}

    async def test_failed_candidates_are_not_ranked(self, client):
        results = await _run(
            client, ("good.pdf", make_pdf_bytes(SAMPLE_RESUME)), ("corrupt.pdf", CORRUPT_PDF)
        )

        ranked = {r["filename"] for r in results["results"]}
        assert "corrupt.pdf" not in ranked

    async def test_ranks_stay_contiguous_when_some_fail(self, client):
        results = await _run(
            client,
            ("a_good.pdf", make_pdf_bytes(SAMPLE_RESUME)),
            ("b_corrupt.pdf", CORRUPT_PDF),
            ("c_good.docx", make_docx_bytes(SAMPLE_RESUME)),
        )

        assert sorted(r["rank"] for r in results["results"]) == [1, 2]

    async def test_a_failure_first_does_not_stop_the_rest(self, client):
        """Ordering matters: the failure must not short-circuit the loop."""
        results = await _run(
            client, ("a_corrupt.pdf", CORRUPT_PDF), ("b_good.pdf", make_pdf_bytes(SAMPLE_RESUME))
        )

        assert results["processed_resumes"] == 1
        assert len(results["results"]) == 1


class TestEveryResumeFails:
    async def test_job_completes_rather_than_erroring(self, client):
        results = await _run(client, ("a.pdf", CORRUPT_PDF), ("b.pdf", BLANK_PDF))

        assert results["status"] == "completed"
        assert results["processed_resumes"] == 0
        assert results["failed_resumes"] == 2

    async def test_results_are_empty_but_failures_are_reported(self, client):
        results = await _run(client, ("a.pdf", CORRUPT_PDF), ("b.pdf", BLANK_PDF))

        assert results["results"] == []
        assert len(results["failures"]) == 2

    async def test_required_skills_are_still_reported(self, client):
        """The JD was still analysed, so a recruiter can see what was being asked for."""
        results = await _run(client, ("a.pdf", CORRUPT_PDF))

        assert {"Python", "FastAPI"} <= set(results["required_skills"])


class TestProgressAccounting:
    async def test_progress_counts_failures_towards_completion(self, client):
        response = await client.post(
            "/jobs",
            data={"jd_text": JD_TEXT},
            files=_files(("good.pdf", make_pdf_bytes(SAMPLE_RESUME)), ("bad.pdf", CORRUPT_PDF)),
        )
        job_id = response.json()["job_id"]

        for _ in range(100):
            status = (await client.get(f"/jobs/{job_id}")).json()
            if status["status"] in {"completed", "failed"}:
                break

        assert status["processed_resumes"] == 1
        assert status["failed_resumes"] == 1
        # 1 + 1 of 2: a job with failures still reaches 100%, it does not stall at 50%.
        assert status["progress"] == 1.0

    async def test_total_matches_what_was_uploaded(self, client):
        response = await client.post(
            "/jobs",
            data={"jd_text": JD_TEXT},
            files=_files(
                ("a.pdf", make_pdf_bytes(SAMPLE_RESUME)),
                ("b.pdf", CORRUPT_PDF),
                ("c.docx", make_docx_bytes(SAMPLE_RESUME)),
            ),
        )
        job_id = response.json()["job_id"]

        status = (await client.get(f"/jobs/{job_id}")).json()

        assert status["total_resumes"] == 3

    async def test_completed_at_is_set_even_when_resumes_failed(self, client):
        response = await client.post(
            "/jobs", data={"jd_text": JD_TEXT}, files=_files(("bad.pdf", CORRUPT_PDF))
        )
        job_id = response.json()["job_id"]
        for _ in range(100):
            status = (await client.get(f"/jobs/{job_id}")).json()
            if status["status"] in {"completed", "failed"}:
                break

        assert status["completed_at"] is not None


class TestLiveProgress:
    """Progress must be committed *during* the run, not written once at the end.

    Without this, moving the per-resume commit outside the loop would still pass every
    other test while making the progress endpoint useless for a long batch.
    """

    async def test_progress_is_visible_while_the_batch_runs(self, client, engine, monkeypatch):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        from app.services import extraction
        from app.worker import tasks
        from tests.conftest import _test_database_url

        # A separate connection: it can only see progress that has been committed.
        observer_engine = create_engine(_test_database_url())
        observer = sessionmaker(bind=observer_engine)
        seen: list[tuple[int, int]] = []

        real_extract = extraction.extract_text

        def spying_extract(filename: str, data: bytes) -> str:
            with observer() as s:
                job = s.execute(select(Job)).scalars().first()
                if job is not None:
                    seen.append((job.processed_resumes, job.failed_resumes))
            return real_extract(filename, data)

        monkeypatch.setattr(tasks, "extract_text", spying_extract)

        good = make_pdf_bytes(SAMPLE_RESUME)
        await _run(
            client,
            ("a.pdf", good),
            ("b.pdf", good),
            ("c.pdf", good),
            ("d.pdf", good),
        )
        observer_engine.dispose()

        # Observed before each of the 4 resumes: 0, 1, 2, 3 already done.
        assert seen == [(0, 0), (1, 0), (2, 0), (3, 0)], seen

    async def test_status_is_processing_while_work_remains(self, client, monkeypatch):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        from app.services import extraction
        from app.worker import tasks
        from tests.conftest import _test_database_url

        observer_engine = create_engine(_test_database_url())
        observer = sessionmaker(bind=observer_engine)
        statuses: list[JobStatus] = []

        real_extract = extraction.extract_text

        def spying_extract(filename: str, data: bytes) -> str:
            with observer() as s:
                job = s.execute(select(Job)).scalars().first()
                if job is not None:
                    statuses.append(job.status)
            return real_extract(filename, data)

        monkeypatch.setattr(tasks, "extract_text", spying_extract)

        good = make_pdf_bytes(SAMPLE_RESUME)
        await _run(client, ("a.pdf", good), ("b.pdf", good))
        observer_engine.dispose()

        assert statuses == [JobStatus.PROCESSING, JobStatus.PROCESSING]


class TestCandidateRowsAfterFailure:
    async def test_failed_candidate_is_persisted_with_a_reason(self, client, db_session):
        await _run(client, ("good.pdf", make_pdf_bytes(SAMPLE_RESUME)), ("bad.pdf", CORRUPT_PDF))

        candidates = (await db_session.execute(select(Candidate))).scalars().all()
        failed = next(c for c in candidates if c.filename == "bad.pdf")

        assert failed.status == CandidateStatus.FAILED
        assert failed.error
        assert failed.embedding is None
        assert failed.parsed_skills == []

    async def test_successful_candidate_is_fully_populated(self, client, db_session):
        await _run(client, ("good.pdf", make_pdf_bytes(SAMPLE_RESUME)), ("bad.pdf", CORRUPT_PDF))

        candidates = (await db_session.execute(select(Candidate))).scalars().all()
        good = next(c for c in candidates if c.filename == "good.pdf")

        assert good.status == CandidateStatus.PROCESSED
        assert good.error is None
        assert good.extracted_text
        assert good.embedding is not None
        assert good.parsed_skills

    async def test_failed_candidate_keeps_its_raw_file(self, client, db_session):
        """Kept so a recruiter can inspect what was actually uploaded."""
        await _run(client, ("bad.pdf", CORRUPT_PDF))

        candidate = (await db_session.execute(select(Candidate))).scalars().one()

        assert candidate.raw_file_key
