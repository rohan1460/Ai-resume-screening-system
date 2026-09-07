"""The background screening task.

The API has already stored the uploads and written ``queued`` Job and ``pending``
Candidate rows. This task reads the files back out of object storage, processes each
one, and updates the job as it goes so ``GET /jobs/{id}`` shows real progress.

Failure isolation is per resume: a corrupt file marks that one candidate ``failed``
and the batch continues. Only an error affecting the whole job (the job row is gone,
the database is unreachable) fails the job.
"""

import time
import uuid
from datetime import UTC, datetime

from botocore.exceptions import BotoCoreError, ClientError
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.sync_session import session_scope
from app.models import Candidate, CandidateStatus, Job, JobStatus, Score, ScoringRun
from app.services.embeddings import embed_text, embed_texts
from app.services.extraction import ExtractionError, extract_text
from app.services.resume_parser import parse_resume_fields
from app.services.scoring import ScoreBreakdown
from app.services.screening import score_resumes
from app.services.skills import extract_skills, get_required_skills
from app.services.storage import StorageError, get_storage
from app.worker.celery_app import celery_app

logger = get_logger(__name__)

# Transient infrastructure faults worth retrying; bad resumes are not among them.
RETRYABLE = (OperationalError, StorageError, BotoCoreError, ClientError)


class JobNotFoundError(Exception):
    """The job row vanished between dispatch and execution."""


def _persist_scores(
    session: Session, run: ScoringRun, job_id: uuid.UUID, candidates: list[Candidate]
) -> dict[str, ScoreBreakdown]:
    scored = score_resumes(
        jd_embedding=run.jd_embedding,
        required_skills=run.required_skills,
        candidates={str(c.id): (c.parsed_skills, c.embedding) for c in candidates},
        w_skill=run.w_skill,
        w_semantic=run.w_semantic,
    )
    for candidate_id, breakdown in scored.items():
        session.add(
            Score(
                run_id=run.id,
                job_id=job_id,
                candidate_id=uuid.UUID(candidate_id),
                skill_score=breakdown.skill_score,
                semantic_score=breakdown.semantic_score,
                final_score=breakdown.final_score,
                matched_skills=breakdown.matched_skills,
                missing_skills=breakdown.missing_skills,
                rank=breakdown.rank,
            )
        )
    return scored


def _process_one(candidate: Candidate, data: bytes) -> None:
    """Parse a single resume into its candidate row. Raises ExtractionError."""
    text = extract_text(candidate.filename, data)
    fields = parse_resume_fields(text, candidate.filename)

    candidate.extracted_text = text
    candidate.name = fields["name"]
    candidate.education = fields["education"]
    candidate.experience = fields["experience"]
    candidate.parsed_skills = sorted(extract_skills(text))
    candidate.status = CandidateStatus.PROCESSED
    candidate.error = None


# Bound to our app explicitly, not via @shared_task: a shared task resolves against
# whatever "current app" happens to exist when it is called, and from the API process
# that is Celery's default app — which points at amqp://, not our Redis broker.
@celery_app.task(
    bind=True,
    name="app.worker.tasks.screen_job",
    max_retries=None,  # retries are driven from settings inside the task
)
def screen_job(self, job_id: str) -> dict[str, object]:
    """Process every resume in a job, then score and rank them."""
    settings = get_settings()
    job_uuid = uuid.UUID(job_id)
    log = logger.bind(job_id=job_id, task_id=self.request.id)

    try:
        return _run(job_uuid, log)
    except JobNotFoundError:
        log.error("job.missing")
        raise
    except RETRYABLE as exc:
        if self.request.retries >= settings.celery_max_retries:
            log.error("job.retries_exhausted", error=str(exc))
            _mark_failed(job_uuid, f"Retries exhausted: {exc}")
            raise
        delay = settings.celery_retry_backoff * (2**self.request.retries)
        log.warning("job.retrying", error=str(exc), retry_in=delay)
        raise self.retry(exc=exc, countdown=delay) from exc
    except Exception as exc:
        log.exception("job.failed")
        _mark_failed(job_uuid, str(exc))
        raise


def _mark_failed(job_id: uuid.UUID, error: str) -> None:
    try:
        with session_scope() as session:
            job = session.get(Job, job_id)
            if job is not None:
                job.status = JobStatus.FAILED
                job.error = error[: get_settings().max_error_length]
                job.completed_at = datetime.now(UTC)
    except Exception:  # the job row is best-effort at this point
        logger.exception("job.mark_failed_failed", job_id=str(job_id))


def _run(job_id: uuid.UUID, log) -> dict[str, object]:
    settings = get_settings()
    storage = get_storage()
    job_started = time.monotonic()

    with session_scope() as session:
        job = session.get(Job, job_id)
        if job is None:
            raise JobNotFoundError(job_id)
        job.status = JobStatus.PROCESSING
        jd_text = job.jd_text
        total = job.total_resumes
    log.info("job.started", total_resumes=total)

    # --- parse each resume, updating progress as we go ---
    processed = 0
    failed = 0
    with session_scope() as session:
        candidates = list(
            session.execute(
                select(Candidate).where(Candidate.job_id == job_id).order_by(Candidate.created_at)
            )
            .scalars()
            .all()
        )

        for index, candidate in enumerate(candidates, start=1):
            started = time.monotonic()
            try:
                if not candidate.raw_file_key:
                    raise ExtractionError("Raw file was not stored; cannot process")
                data = storage.get(candidate.raw_file_key)
                _process_one(candidate, data)
                processed += 1
                log.info(
                    "resume.processed",
                    filename=candidate.filename,
                    candidate_id=str(candidate.id),
                    position=index,
                    of=total,
                    skills_found=len(candidate.parsed_skills),
                    chars_extracted=len(candidate.extracted_text or ""),
                    duration_ms=round((time.monotonic() - started) * 1000, 1),
                )
            except ExtractionError as exc:
                candidate.status = CandidateStatus.FAILED
                candidate.error = str(exc)
                failed += 1
                log.warning(
                    "resume.failed",
                    filename=candidate.filename,
                    candidate_id=str(candidate.id),
                    position=index,
                    of=total,
                    error=str(exc),
                    duration_ms=round((time.monotonic() - started) * 1000, 1),
                )

            job = session.get(Job, job_id)
            job.processed_resumes = processed
            job.failed_resumes = failed
            # Commit per resume so polling sees progress rather than a jump at the end.
            session.commit()

    # --- embed the successful ones in one batch, then score ---
    with session_scope() as session:
        succeeded = list(
            session.execute(
                select(Candidate).where(
                    Candidate.job_id == job_id,
                    Candidate.status == CandidateStatus.PROCESSED,
                )
            )
            .scalars()
            .all()
        )

        if succeeded:
            vectors = embed_texts([c.extracted_text or "" for c in succeeded])
            for candidate, vector in zip(succeeded, vectors, strict=True):
                candidate.embedding = vector

        jd_embedding = embed_text(jd_text)
        required_skills = sorted(get_required_skills(jd_text))

        job = session.get(Job, job_id)
        job.jd_embedding = jd_embedding

        run = ScoringRun(
            job_id=job_id,
            jd_text=jd_text,
            jd_embedding=jd_embedding,
            required_skills=required_skills,
            w_skill=settings.w_skill,
            w_semantic=settings.w_semantic,
            is_initial=True,
        )
        session.add(run)
        session.flush()
        _persist_scores(session, run, job_id, succeeded)

        job.status = JobStatus.COMPLETED
        job.completed_at = datetime.now(UTC)

    log.info(
        "job.completed",
        processed=processed,
        failed=failed,
        total=total,
        duration_ms=round((time.monotonic() - job_started) * 1000, 1),
    )
    return {"job_id": str(job_id), "processed": processed, "failed": failed}
