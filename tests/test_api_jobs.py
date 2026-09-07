"""API integration tests against a real Postgres test database."""

import asyncio
import uuid

import pytest
from httpx import AsyncClient

from tests.factories import SAMPLE_RESUME, make_docx_bytes, make_pdf_bytes

JD_TEXT = (
    "We are hiring a Senior Backend Engineer. You will build APIs in Python using "
    "FastAPI, work with PostgreSQL and Redis, and deploy services on Kubernetes "
    "with Docker."
)

OTHER_RESUME = """Ravi Kumar
Frontend Engineer
ravi.kumar@example.com

SKILLS
JavaScript, React, CSS, HTML

EXPERIENCE
Frontend Engineer, Widgets Inc
Feb 2020 - Present
Built component libraries.

EDUCATION
B.Sc in Design, Pune University, 2019
"""


def _resume_files(*items: tuple[str, bytes]) -> list[tuple[str, tuple[str, bytes, str]]]:
    return [("resumes", (name, data, "application/octet-stream")) for name, data in items]


async def _create_job(client: AsyncClient, **extra) -> dict:
    files = _resume_files(
        ("priya_sharma.pdf", make_pdf_bytes(SAMPLE_RESUME)),
        ("ravi_kumar.docx", make_docx_bytes(OTHER_RESUME)),
    )
    data = {"jd_text": JD_TEXT, **extra}
    response = await client.post("/jobs", data=data, files=files)
    assert response.status_code == 201, response.text
    return response.json()


async def _await_job(client: AsyncClient, job_id: str) -> dict:
    """Poll until the job settles. Eager Celery finishes on the first poll."""
    for _ in range(60):
        status = (await client.get(f"/jobs/{job_id}")).json()
        if status["status"] in {"completed", "failed"}:
            return status
        await asyncio.sleep(0.1)
    raise AssertionError(f"job {job_id} did not finish")


async def _create_and_wait(client: AsyncClient, **extra) -> dict:
    created = await _create_job(client, **extra)
    await _await_job(client, created["job_id"])
    return created


class TestCreateJob:
    async def test_returns_queued_immediately(self, client):
        """POST hands off to the worker; it must not wait for processing."""
        body = await _create_job(client)

        assert uuid.UUID(body["job_id"])
        assert body["status"] == "queued"

    async def test_job_reaches_completed_via_the_worker(self, client):
        body = await _create_job(client)

        final = await _await_job(client, body["job_id"])

        assert final["status"] == "completed"
        assert final["processed_resumes"] == 2

    async def test_accepts_a_title(self, client):
        body = await _create_and_wait(client, title="Backend Hiring Q3")

        status_body = (await client.get(f"/jobs/{body['job_id']}")).json()
        assert status_body["title"] == "Backend Hiring Q3"

    async def test_accepts_jd_as_a_file(self, client):
        files = _resume_files(("priya_sharma.pdf", make_pdf_bytes(SAMPLE_RESUME)))
        files.append(("jd_file", ("jd.txt", JD_TEXT.encode(), "text/plain")))

        response = await client.post("/jobs", files=files)

        assert response.status_code == 201, response.text

    async def test_rejects_missing_jd(self, client):
        files = _resume_files(("priya_sharma.pdf", make_pdf_bytes(SAMPLE_RESUME)))

        response = await client.post("/jobs", files=files)

        assert response.status_code == 400
        assert "job description is required" in response.json()["detail"].lower()

    async def test_rejects_unsupported_file_type(self, client):
        files = _resume_files(("resume.txt", b"just some text content here"))

        response = await client.post("/jobs", data={"jd_text": JD_TEXT}, files=files)

        assert response.status_code == 400
        assert "unsupported file type" in response.json()["detail"].lower()

    async def test_rejects_empty_file(self, client):
        files = _resume_files(("resume.pdf", b""))

        response = await client.post("/jobs", data={"jd_text": JD_TEXT}, files=files)

        assert response.status_code == 400
        assert "empty" in response.json()["detail"].lower()

    async def test_rejects_oversized_file(self, client):
        from app.core.config import get_settings

        oversized = b"x" * (get_settings().max_upload_bytes + 1)
        files = _resume_files(("resume.pdf", oversized))

        response = await client.post("/jobs", data={"jd_text": JD_TEXT}, files=files)

        assert response.status_code == 413

    async def test_requires_at_least_one_resume(self, client):
        response = await client.post("/jobs", data={"jd_text": JD_TEXT})

        assert response.status_code == 422


class TestFailureIsolation:
    async def test_one_corrupt_resume_does_not_sink_the_batch(self, client):
        files = _resume_files(
            ("priya_sharma.pdf", make_pdf_bytes(SAMPLE_RESUME)),
            ("corrupt.pdf", b"definitely not a pdf file at all, just bytes"),
        )

        created = await client.post("/jobs", data={"jd_text": JD_TEXT}, files=files)
        assert created.status_code == 201

        job_id = created.json()["job_id"]
        results = (await client.get(f"/jobs/{job_id}/results")).json()

        assert results["processed_resumes"] == 1
        assert results["failed_resumes"] == 1
        assert len(results["results"]) == 1
        assert results["failures"][0]["filename"] == "corrupt.pdf"
        assert results["failures"][0]["error"]


class TestJobStatus:
    async def test_reports_progress(self, client):
        body = await _create_and_wait(client)

        status_body = (await client.get(f"/jobs/{body['job_id']}")).json()

        assert status_body["total_resumes"] == 2
        assert status_body["processed_resumes"] == 2
        assert status_body["progress"] == 1.0
        assert status_body["completed_at"] is not None

    async def test_unknown_job_is_404(self, client):
        response = await client.get(f"/jobs/{uuid.uuid4()}")

        assert response.status_code == 404

    async def test_malformed_uuid_is_422(self, client):
        response = await client.get("/jobs/not-a-uuid")

        assert response.status_code == 422


class TestJobResults:
    async def test_returns_ranked_explainable_results(self, client):
        body = await _create_and_wait(client)

        results = (await client.get(f"/jobs/{body['job_id']}/results")).json()

        assert len(results["results"]) == 2
        assert [r["rank"] for r in results["results"]] == [1, 2]
        for row in results["results"]:
            assert 0 <= row["final_score"] <= 100
            assert isinstance(row["matched_skills"], list)
            assert isinstance(row["missing_skills"], list)

    async def test_sorted_by_rank_descending_score(self, client):
        body = await _create_and_wait(client)

        results = (await client.get(f"/jobs/{body['job_id']}/results")).json()
        scores = [r["final_score"] for r in results["results"]]

        assert scores == sorted(scores, reverse=True)

    async def test_reports_required_skills_from_the_jd(self, client):
        body = await _create_and_wait(client)

        results = (await client.get(f"/jobs/{body['job_id']}/results")).json()

        assert {"Python", "FastAPI", "PostgreSQL", "Kubernetes", "Docker"} <= set(
            results["required_skills"]
        )

    async def test_matched_and_missing_partition_required_skills(self, client):
        body = await _create_and_wait(client)

        results = (await client.get(f"/jobs/{body['job_id']}/results")).json()
        required = set(results["required_skills"])

        for row in results["results"]:
            assert set(row["matched_skills"]) | set(row["missing_skills"]) == required
            assert not set(row["matched_skills"]) & set(row["missing_skills"])

    async def test_backend_candidate_matches_more_skills_than_frontend_one(self, client):
        body = await _create_and_wait(client)

        results = (await client.get(f"/jobs/{body['job_id']}/results")).json()
        by_file = {r["filename"]: r for r in results["results"]}

        assert len(by_file["priya_sharma.pdf"]["matched_skills"]) > len(
            by_file["ravi_kumar.docx"]["matched_skills"]
        )

    async def test_extracts_candidate_names(self, client):
        body = await _create_and_wait(client)

        results = (await client.get(f"/jobs/{body['job_id']}/results")).json()
        names = {r["name"] for r in results["results"]}

        assert "Priya Sharma" in names

    async def test_includes_education_and_experience(self, client):
        body = await _create_and_wait(client)

        results = (await client.get(f"/jobs/{body['job_id']}/results")).json()
        priya = next(r for r in results["results"] if r["filename"] == "priya_sharma.pdf")

        assert priya["education"]
        assert priya["experience"]

    async def test_unknown_job_is_404(self, client):
        response = await client.get(f"/jobs/{uuid.uuid4()}/results")

        assert response.status_code == 404


class TestRerank:
    async def test_creates_a_new_run_and_rescores(self, client):
        body = await _create_and_wait(client)
        job_id = body["job_id"]
        original = (await client.get(f"/jobs/{job_id}/results")).json()

        response = await client.post(
            f"/jobs/{job_id}/rerank",
            json={"jd_text": "Frontend engineer skilled in React, JavaScript, CSS and HTML."},
        )

        assert response.status_code == 200, response.text
        reranked = response.json()
        assert reranked["run_id"] != original["run_id"]
        assert "React" in reranked["required_skills"]

    async def test_rerank_changes_the_winner(self, client):
        body = await _create_and_wait(client)
        job_id = body["job_id"]

        reranked = (
            await client.post(
                f"/jobs/{job_id}/rerank",
                json={"jd_text": "Frontend engineer skilled in React, JavaScript, CSS and HTML."},
            )
        ).json()

        top = next(r for r in reranked["results"] if r["rank"] == 1)
        assert top["filename"] == "ravi_kumar.docx"

    async def test_results_endpoint_returns_the_latest_run(self, client):
        body = await _create_and_wait(client)
        job_id = body["job_id"]

        reranked = (
            await client.post(
                f"/jobs/{job_id}/rerank", json={"jd_text": "React and JavaScript developer."}
            )
        ).json()
        latest = (await client.get(f"/jobs/{job_id}/results")).json()

        assert latest["run_id"] == reranked["run_id"]

    async def test_accepts_custom_weights(self, client):
        body = await _create_and_wait(client)

        response = await client.post(
            f"/jobs/{body['job_id']}/rerank",
            json={"jd_text": JD_TEXT, "w_skill": 0.9, "w_semantic": 0.1},
        )

        assert response.status_code == 200
        assert response.json()["w_skill"] == 0.9

    async def test_rejects_weights_that_do_not_sum_to_one(self, client):
        body = await _create_and_wait(client)

        response = await client.post(
            f"/jobs/{body['job_id']}/rerank",
            json={"jd_text": JD_TEXT, "w_skill": 0.9, "w_semantic": 0.9},
        )

        assert response.status_code == 400
        assert "sum to 1.0" in response.json()["detail"]

    async def test_rejects_empty_jd(self, client):
        body = await _create_and_wait(client)

        response = await client.post(f"/jobs/{body['job_id']}/rerank", json={"jd_text": ""})

        assert response.status_code == 422

    async def test_unknown_job_is_404(self, client):
        response = await client.post(f"/jobs/{uuid.uuid4()}/rerank", json={"jd_text": JD_TEXT})

        assert response.status_code == 404


class TestPersistence:
    async def test_candidates_and_scores_are_stored(self, client, db_session):
        from sqlalchemy import func, select

        from app.models import Candidate, Score, ScoringRun

        await _create_and_wait(client)

        assert (await db_session.execute(select(func.count(Candidate.id)))).scalar() == 2
        assert (await db_session.execute(select(func.count(Score.id)))).scalar() == 2
        assert (await db_session.execute(select(func.count(ScoringRun.id)))).scalar() == 1

    async def test_embeddings_are_cached_for_rerank(self, client, db_session):
        from sqlalchemy import select

        from app.models import Candidate

        body = await _create_and_wait(client)
        await client.post(f"/jobs/{body['job_id']}/rerank", json={"jd_text": JD_TEXT})

        candidates = (await db_session.execute(select(Candidate))).scalars().all()
        processed = [c for c in candidates if c.embedding is not None]

        assert len(processed) == 2
        assert all(len(c.embedding) == 384 for c in processed)

    async def test_rerank_preserves_the_earlier_run(self, client, db_session):
        from sqlalchemy import func, select

        from app.models import Score, ScoringRun

        body = await _create_and_wait(client)
        await client.post(f"/jobs/{body['job_id']}/rerank", json={"jd_text": JD_TEXT})

        runs = (await db_session.execute(select(func.count(ScoringRun.id)))).scalar()
        scores = (await db_session.execute(select(func.count(Score.id)))).scalar()

        assert runs == 2
        assert scores == 4  # both runs' scores retained


class TestRawFileStorage:
    """The stub records what was stored; these assert the wiring around it."""

    async def test_every_processed_candidate_gets_a_storage_key(
        self, client, db_session, stub_heavy_dependencies
    ):
        from sqlalchemy import select

        from app.models import Candidate

        await _create_and_wait(client)

        candidates = (await db_session.execute(select(Candidate))).scalars().all()

        assert all(c.raw_file_key for c in candidates)

    async def test_key_is_namespaced_by_job(self, client, db_session):
        from sqlalchemy import select

        from app.models import Candidate

        body = await _create_and_wait(client)

        candidates = (await db_session.execute(select(Candidate))).scalars().all()

        assert all(c.raw_file_key.startswith(f"jobs/{body['job_id']}/") for c in candidates)

    async def test_uploaded_bytes_are_stored_verbatim(
        self, client, db_session, stub_heavy_dependencies
    ):
        from sqlalchemy import select

        from app.models import Candidate

        payload = make_pdf_bytes(SAMPLE_RESUME)
        storage = stub_heavy_dependencies
        response = await client.post(
            "/jobs",
            data={"jd_text": JD_TEXT},
            files=[("resumes", ("priya.pdf", payload, "application/pdf"))],
        )
        assert response.status_code == 201

        candidate = (await db_session.execute(select(Candidate))).scalars().one()

        assert storage.objects[candidate.raw_file_key] == payload

    async def test_a_failed_resume_still_keeps_its_raw_file(self, client, db_session):
        from sqlalchemy import select

        from app.models import Candidate, CandidateStatus

        files = _resume_files(("corrupt.pdf", b"not a pdf at all, but worth keeping"))
        await client.post("/jobs", data={"jd_text": JD_TEXT}, files=files)

        candidate = (await db_session.execute(select(Candidate))).scalars().one()

        assert candidate.status == CandidateStatus.FAILED
        assert candidate.raw_file_key, "the raw upload should be retained for debugging"


class TestStorageOutage:
    """Once processing moved to the worker, the uploaded bytes live only in object
    storage — so an outage is fatal to the upload and must be reported as such,
    not swallowed into a job that is guaranteed to fail."""

    async def test_upload_is_rejected_with_503(self, client, monkeypatch):
        class DeadStorage:
            def ensure_bucket(self):
                from app.services.storage import StorageError

                raise StorageError("simulated MinIO outage")

        monkeypatch.setattr("app.api.routes.jobs.get_storage", lambda: DeadStorage())

        files = _resume_files(("priya_sharma.pdf", make_pdf_bytes(SAMPLE_RESUME)))
        response = await client.post("/jobs", data={"jd_text": JD_TEXT}, files=files)

        assert response.status_code == 503
        assert "storage is unavailable" in response.json()["detail"].lower()

    async def test_no_orphan_job_row_is_left_behind(self, client, db_session, monkeypatch):
        from sqlalchemy import func, select

        from app.models import Job

        class DeadStorage:
            def ensure_bucket(self):
                from app.services.storage import StorageError

                raise StorageError("simulated MinIO outage")

        monkeypatch.setattr("app.api.routes.jobs.get_storage", lambda: DeadStorage())

        files = _resume_files(("priya_sharma.pdf", make_pdf_bytes(SAMPLE_RESUME)))
        await client.post("/jobs", data={"jd_text": JD_TEXT}, files=files)

        assert (await db_session.execute(select(func.count(Job.id)))).scalar() == 0

    async def test_one_unstorable_file_fails_only_that_candidate(
        self, client, db_session, monkeypatch
    ):
        """A per-file put failure must not sink the batch."""
        from sqlalchemy import select

        from app.models import Candidate, CandidateStatus
        from app.services.storage import StorageError
        from tests.conftest import FakeStorage

        class FlakyStorage(FakeStorage):
            def put(self, key, data, content_type=None):
                if key.endswith(".docx"):
                    raise StorageError("simulated per-object failure")
                return super().put(key, data, content_type)

        storage = FlakyStorage()
        monkeypatch.setattr("app.api.routes.jobs.get_storage", lambda: storage)
        monkeypatch.setattr("app.worker.tasks.get_storage", lambda: storage)

        await _create_and_wait(client)

        candidates = (await db_session.execute(select(Candidate))).scalars().all()
        by_name = {c.filename: c for c in candidates}

        assert by_name["priya_sharma.pdf"].status == CandidateStatus.PROCESSED
        assert by_name["ravi_kumar.docx"].status == CandidateStatus.FAILED
        assert "not stored" in by_name["ravi_kumar.docx"].error.lower()


@pytest.mark.real_storage
class TestAgainstLiveMinio:
    """End-to-end against a running MinIO, skipped when it is not up."""

    async def test_files_are_readable_back_from_minio(self, client, db_session):
        from sqlalchemy import select

        from app.models import Candidate
        from app.services.storage import StorageError, get_storage

        try:
            storage = get_storage()
            storage.ensure_bucket()
        except StorageError as exc:  # pragma: no cover - environment dependent
            pytest.skip(f"MinIO not reachable: {exc}")

        payload = make_pdf_bytes(SAMPLE_RESUME)
        response = await client.post(
            "/jobs",
            data={"jd_text": JD_TEXT},
            files=[("resumes", ("priya.pdf", payload, "application/pdf"))],
        )
        assert response.status_code == 201, response.text

        candidate = (await db_session.execute(select(Candidate))).scalars().one()

        assert candidate.raw_file_key
        assert storage.get(candidate.raw_file_key) == payload


class TestOpenApiContract:
    async def test_documents_every_section_5_endpoint(self, client):
        schema = (await client.get("/openapi.json")).json()

        assert set(schema["paths"]) == {
            "/health",
            "/jobs",
            "/jobs/{job_id}",
            "/jobs/{job_id}/results",
            "/jobs/{job_id}/rerank",
        }

    async def test_create_job_is_multipart(self, client):
        schema = (await client.get("/openapi.json")).json()

        assert "multipart/form-data" in schema["paths"]["/jobs"]["post"]["requestBody"]["content"]

    async def test_results_schema_exposes_the_score_breakdown(self, client):
        schema = (await client.get("/openapi.json")).json()
        candidate = schema["components"]["schemas"]["CandidateResult"]["properties"]

        assert {
            "rank",
            "final_score",
            "skill_score",
            "semantic_score",
            "matched_skills",
            "missing_skills",
        } <= set(candidate)


@pytest.mark.parametrize("endpoint", ["/health", "/docs", "/openapi.json"])
async def test_service_endpoints_available(client, endpoint):
    response = await client.get(endpoint)

    assert response.status_code == 200
