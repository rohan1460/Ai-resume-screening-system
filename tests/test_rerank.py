"""Rerank: re-score existing candidates against a new JD using cached embeddings.

The point of storing vectors in pgvector is that a second JD costs one embedding call,
not N. These tests prove that by counting the work actually done, rather than checking
that the result merely looks right — a rerank that quietly re-embedded every resume
would return identical output while being unusably slow on a large batch.
"""

import pytest
from sqlalchemy import select

from app.models import Candidate, ScoringRun
from tests.factories import SAMPLE_RESUME, make_docx_bytes, make_pdf_bytes

BACKEND_JD = "Backend engineer with Python, FastAPI, PostgreSQL, Redis and Docker."
FRONTEND_JD = "Frontend engineer skilled in React, TypeScript, JavaScript, CSS and HTML."

FRONTEND_RESUME = """Ravi Kumar
ravi.kumar@example.com

SKILLS
JavaScript, TypeScript, React, CSS, HTML, Redux

EXPERIENCE
Frontend Engineer, Widgets Inc
Feb 2020 - Present
Built component libraries.
"""


class CallCounter:
    """Wraps a function and records every call."""

    def __init__(self, func):
        self._func = func
        self.calls: list[tuple] = []

    def __call__(self, *args, **kwargs):
        self.calls.append(args)
        return self._func(*args, **kwargs)

    @property
    def count(self) -> int:
        return len(self.calls)


@pytest.fixture
async def screened_job(client) -> str:
    """A finished job with two processed candidates."""
    files = [
        ("resumes", ("priya.pdf", make_pdf_bytes(SAMPLE_RESUME), "application/octet-stream")),
        ("resumes", ("ravi.docx", make_docx_bytes(FRONTEND_RESUME), "application/octet-stream")),
    ]
    response = await client.post("/jobs", data={"jd_text": BACKEND_JD}, files=files)
    assert response.status_code == 201, response.text
    job_id = response.json()["job_id"]

    for _ in range(100):
        status = (await client.get(f"/jobs/{job_id}")).json()
        if status["status"] in {"completed", "failed"}:
            break
    assert status["status"] == "completed", status
    assert status["processed_resumes"] == 2
    return job_id


class TestResumesAreNotReEmbedded:
    """The core guarantee."""

    async def test_batch_embedding_is_never_called_on_rerank(
        self, client, screened_job, monkeypatch
    ):
        """``embed_texts`` is the resume path; a rerank must not touch it."""
        from app.worker import tasks

        counter = CallCounter(tasks.embed_texts)
        monkeypatch.setattr(tasks, "embed_texts", counter)

        response = await client.post(f"/jobs/{screened_job}/rerank", json={"jd_text": FRONTEND_JD})

        assert response.status_code == 200, response.text
        assert counter.count == 0, f"resumes were re-embedded {counter.count} time(s)"

    async def test_exactly_one_embedding_call_and_it_is_the_jd(
        self, client, screened_job, monkeypatch
    ):
        import app.api.routes.jobs as jobs_module

        counter = CallCounter(jobs_module.embed_text)
        monkeypatch.setattr(jobs_module, "embed_text", counter)

        await client.post(f"/jobs/{screened_job}/rerank", json={"jd_text": FRONTEND_JD})

        assert counter.count == 1
        assert counter.calls[0][0] == FRONTEND_JD

    async def test_embedding_cost_does_not_grow_with_candidate_count(self, client, monkeypatch):
        """Five candidates must still cost one embedding call, not five."""
        import app.api.routes.jobs as jobs_module

        files = [
            (
                "resumes",
                (f"c{i}.pdf", make_pdf_bytes(SAMPLE_RESUME), "application/octet-stream"),
            )
            for i in range(5)
        ]
        created = await client.post("/jobs", data={"jd_text": BACKEND_JD}, files=files)
        job_id = created.json()["job_id"]
        for _ in range(100):
            if (await client.get(f"/jobs/{job_id}")).json()["status"] == "completed":
                break

        counter = CallCounter(jobs_module.embed_text)
        monkeypatch.setattr(jobs_module, "embed_text", counter)

        await client.post(f"/jobs/{job_id}/rerank", json={"jd_text": FRONTEND_JD})

        assert counter.count == 1


class TestResumesAreNotReParsed:
    async def test_text_extraction_is_never_called(self, client, screened_job, monkeypatch):
        from app.worker import tasks

        counter = CallCounter(tasks.extract_text)
        monkeypatch.setattr(tasks, "extract_text", counter)

        await client.post(f"/jobs/{screened_job}/rerank", json={"jd_text": FRONTEND_JD})

        assert counter.count == 0, "rerank re-parsed the resume files"

    async def test_object_storage_is_never_read(self, client, screened_job, monkeypatch):
        """Rerank works from the database alone; the raw files are not needed."""
        from app.worker import tasks

        class ExplodingStorage:
            def get(self, key):
                raise AssertionError("rerank must not read raw files from storage")

            def ensure_bucket(self):
                raise AssertionError("rerank must not touch storage")

        monkeypatch.setattr(tasks, "get_storage", lambda: ExplodingStorage())
        monkeypatch.setattr("app.api.routes.jobs.get_storage", lambda: ExplodingStorage())

        response = await client.post(f"/jobs/{screened_job}/rerank", json={"jd_text": FRONTEND_JD})

        assert response.status_code == 200


class TestCachedVectorsAreReused:
    async def test_candidate_embeddings_are_byte_identical_after_rerank(
        self, client, screened_job, db_session
    ):
        before = {
            c.filename: list(c.embedding)
            for c in (await db_session.execute(select(Candidate))).scalars().all()
        }

        await client.post(f"/jobs/{screened_job}/rerank", json={"jd_text": FRONTEND_JD})

        db_session.expire_all()
        after = {
            c.filename: list(c.embedding)
            for c in (await db_session.execute(select(Candidate))).scalars().all()
        }

        assert before == after

    async def test_parsed_fields_are_untouched(self, client, screened_job, db_session):
        before = {
            c.filename: (c.extracted_text, c.parsed_skills, c.name)
            for c in (await db_session.execute(select(Candidate))).scalars().all()
        }

        await client.post(f"/jobs/{screened_job}/rerank", json={"jd_text": FRONTEND_JD})

        db_session.expire_all()
        after = {
            c.filename: (c.extracted_text, c.parsed_skills, c.name)
            for c in (await db_session.execute(select(Candidate))).scalars().all()
        }

        assert before == after

    async def test_no_new_candidate_rows_are_created(self, client, screened_job, db_session):
        from sqlalchemy import func

        await client.post(f"/jobs/{screened_job}/rerank", json={"jd_text": FRONTEND_JD})

        count = (await db_session.execute(select(func.count(Candidate.id)))).scalar()

        assert count == 2

    async def test_vectors_survive_the_pgvector_round_trip(self, client, screened_job, db_session):
        from app.core.config import get_settings

        candidates = (await db_session.execute(select(Candidate))).scalars().all()

        for candidate in candidates:
            assert candidate.embedding is not None
            assert len(candidate.embedding) == get_settings().embedding_dim


class TestOnlyTheJdIsEmbeddedFresh:
    async def test_new_run_gets_its_own_jd_embedding(self, client, screened_job, db_session):
        await client.post(f"/jobs/{screened_job}/rerank", json={"jd_text": FRONTEND_JD})

        runs = (
            (await db_session.execute(select(ScoringRun).order_by(ScoringRun.created_at)))
            .scalars()
            .all()
        )

        assert len(runs) == 2
        assert list(runs[0].jd_embedding) != list(runs[1].jd_embedding)

    async def test_new_run_records_the_new_jd_text(self, client, screened_job, db_session):
        await client.post(f"/jobs/{screened_job}/rerank", json={"jd_text": FRONTEND_JD})

        runs = (
            (await db_session.execute(select(ScoringRun).order_by(ScoringRun.created_at)))
            .scalars()
            .all()
        )

        assert runs[0].jd_text == BACKEND_JD
        assert runs[1].jd_text == FRONTEND_JD
        assert runs[0].is_initial is True
        assert runs[1].is_initial is False


class TestRerankResult:
    async def test_returns_the_new_ranking(self, client, screened_job):
        original = (await client.get(f"/jobs/{screened_job}/results")).json()

        response = await client.post(f"/jobs/{screened_job}/rerank", json={"jd_text": FRONTEND_JD})
        reranked = response.json()

        assert reranked["run_id"] != original["run_id"]
        assert reranked["jd_text"] == FRONTEND_JD

    async def test_ranking_actually_changes_for_a_different_jd(self, client, screened_job):
        backend_top = next(
            r
            for r in (await client.get(f"/jobs/{screened_job}/results")).json()["results"]
            if r["rank"] == 1
        )

        reranked = (
            await client.post(f"/jobs/{screened_job}/rerank", json={"jd_text": FRONTEND_JD})
        ).json()
        frontend_top = next(r for r in reranked["results"] if r["rank"] == 1)

        assert backend_top["filename"] == "priya.pdf"
        assert frontend_top["filename"] == "ravi.docx"

    async def test_required_skills_come_from_the_new_jd(self, client, screened_job):
        reranked = (
            await client.post(f"/jobs/{screened_job}/rerank", json={"jd_text": FRONTEND_JD})
        ).json()

        assert {"React", "TypeScript", "CSS"} <= set(reranked["required_skills"])
        assert "FastAPI" not in reranked["required_skills"]

    async def test_matched_and_missing_reflect_the_new_jd(self, client, screened_job):
        reranked = (
            await client.post(f"/jobs/{screened_job}/rerank", json={"jd_text": FRONTEND_JD})
        ).json()
        ravi = next(r for r in reranked["results"] if r["filename"] == "ravi.docx")

        assert {"React", "CSS", "HTML"} <= set(ravi["matched_skills"])

    async def test_every_candidate_is_rescored(self, client, screened_job):
        reranked = (
            await client.post(f"/jobs/{screened_job}/rerank", json={"jd_text": FRONTEND_JD})
        ).json()

        assert len(reranked["results"]) == 2
        assert sorted(r["rank"] for r in reranked["results"]) == [1, 2]

    async def test_history_is_preserved(self, client, screened_job, db_session):
        from sqlalchemy import func

        from app.models import Score

        await client.post(f"/jobs/{screened_job}/rerank", json={"jd_text": FRONTEND_JD})

        runs = (await db_session.execute(select(func.count(ScoringRun.id)))).scalar()
        scores = (await db_session.execute(select(func.count(Score.id)))).scalar()

        assert runs == 2
        assert scores == 4  # two candidates in each of the two runs

    async def test_repeated_reranks_stack_up(self, client, screened_job, db_session):
        from sqlalchemy import func

        for jd in (FRONTEND_JD, BACKEND_JD, "Data engineer with Python, Spark and Airflow."):
            response = await client.post(f"/jobs/{screened_job}/rerank", json={"jd_text": jd})
            assert response.status_code == 200

        runs = (await db_session.execute(select(func.count(ScoringRun.id)))).scalar()

        assert runs == 4  # the initial run plus three reranks


class TestRerankValidation:
    async def test_unknown_job_is_404(self, client):
        import uuid

        response = await client.post(f"/jobs/{uuid.uuid4()}/rerank", json={"jd_text": BACKEND_JD})

        assert response.status_code == 404

    async def test_job_with_no_processed_candidates_is_409(self, client):
        files = [("resumes", ("bad.pdf", b"%PDF-1.4 invalid", "application/octet-stream"))]
        created = await client.post("/jobs", data={"jd_text": BACKEND_JD}, files=files)
        job_id = created.json()["job_id"]
        for _ in range(100):
            if (await client.get(f"/jobs/{job_id}")).json()["status"] == "completed":
                break

        response = await client.post(f"/jobs/{job_id}/rerank", json={"jd_text": FRONTEND_JD})

        assert response.status_code == 409
        assert "no successfully processed candidates" in response.json()["detail"]

    async def test_blank_jd_is_rejected(self, client, screened_job):
        response = await client.post(f"/jobs/{screened_job}/rerank", json={"jd_text": "   "})

        assert response.status_code in {400, 422}
