"""Screening job endpoints.

``POST /jobs`` validates, stores the uploads, writes ``queued`` rows and dispatches a
Celery task — it never waits for processing. Progress is polled from
``GET /jobs/{id}``; the worker commits after each resume.

``POST /jobs/{id}/rerank`` is the exception that stays inline: it re-scores existing
candidates from their cached pgvector embeddings, so the only new work is embedding
the JD once. No parsing, no storage reads, and no per-resume embedding — which is why
it is fast enough to answer in the request rather than going through the queue.
"""

import asyncio
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.core.security import require_api_key
from app.db.session import get_session
from app.models import Candidate, CandidateStatus, Job, JobStatus, Score, ScoringRun
from app.schemas.job import (
    CandidateResult,
    FailedResumeInfo,
    JobCreatedResponse,
    JobResultsResponse,
    JobStatusResponse,
    RerankRequest,
)
from app.services.embeddings import embed_text
from app.services.extraction import ExtractionError, extract_text
from app.services.screening import extract_required_skills, score_resumes
from app.services.storage import StorageError, get_storage
from app.worker.tasks import screen_job

logger = get_logger(__name__)
# Auth is applied to the whole router, so a new endpoint is protected by default
# rather than by remembering to decorate it.
router = APIRouter(prefix="/jobs", tags=["jobs"], dependencies=[Depends(require_api_key)])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def _validate_upload(filename: str, data: bytes, settings: Settings) -> None:
    suffix = Path(filename).suffix.lower()
    if suffix not in settings.allowed_upload_extensions:
        allowed = ", ".join(sorted(settings.allowed_upload_extensions))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file type '{suffix or filename}'. Allowed: {allowed}",
        )
    if not data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File '{filename}' is empty",
        )
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"File '{filename}' exceeds the {settings.max_upload_mb}MB limit",
        )


async def _resolve_jd_text(
    jd_text: str | None, jd_file: UploadFile | None, settings: Settings
) -> str:
    if jd_text and jd_text.strip():
        return jd_text.strip()

    if jd_file is not None and jd_file.filename:
        data = await jd_file.read()
        suffix = Path(jd_file.filename).suffix.lower()
        if suffix in settings.allowed_upload_extensions:
            try:
                return extract_text(jd_file.filename, data)
            except ExtractionError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Could not read the job description: {exc}",
                ) from exc
        try:
            decoded = data.decode("utf-8").strip()
        except UnicodeDecodeError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Job description file is not valid UTF-8 text",
            ) from exc
        if decoded:
            return decoded

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="A job description is required: provide jd_text or jd_file",
    )


def _store_files(job_id: uuid.UUID, files: list[tuple[str, bytes]]) -> dict[str, str | None]:
    """Upload raw files, returning filename -> key.

    A failure on one file leaves that entry ``None`` and the worker marks just that
    candidate failed. A completely unreachable store raises instead — see below.
    """
    keys: dict[str, str | None] = {}
    storage = get_storage()
    # Raises: with processing moved into the worker, the uploaded bytes live ONLY in
    # object storage. Accepting a job we know cannot be read back would queue work
    # that is guaranteed to fail, so the caller turns this into a 503 instead.
    storage.ensure_bucket()

    for filename, data in files:
        try:
            key = storage.build_key(job_id, filename)
            keys[filename] = storage.put(key, data)
        except StorageError as exc:
            logger.error("storage.put_failed", filename=filename, error=str(exc))
            keys[filename] = None
    return keys


def _persist_scores(
    session: AsyncSession,
    run: ScoringRun,
    job_id: uuid.UUID,
    candidates: list[Candidate],
) -> None:
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


@router.post("", response_model=JobCreatedResponse, status_code=status.HTTP_201_CREATED)
async def create_job(
    session: SessionDep,
    settings: SettingsDep,
    resumes: Annotated[list[UploadFile], File(description="Resume files (.pdf or .docx)")],
    jd_text: Annotated[str | None, Form()] = None,
    jd_file: Annotated[UploadFile | None, File()] = None,
    title: Annotated[str | None, Form()] = None,
) -> JobCreatedResponse:
    """Accept a screening job and hand it to a worker.

    Everything expensive — parsing, embedding, scoring — happens in the Celery task.
    This handler only validates, stores the raw files, writes ``queued`` rows and
    dispatches, so a 100-resume upload returns in about the time it takes to store
    the files rather than timing out.
    """
    resolved_jd = await _resolve_jd_text(jd_text, jd_file, settings)

    if not resumes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="At least one resume is required"
        )

    files: list[tuple[str, bytes]] = []
    for upload in resumes:
        filename = upload.filename or "unnamed"
        data = await upload.read()
        _validate_upload(filename, data, settings)
        files.append((filename, data))

    job = Job(
        title=title,
        jd_text=resolved_jd,
        status=JobStatus.QUEUED,
        total_resumes=len(files),
    )
    session.add(job)
    await session.flush()

    # Storage is the only I/O the request pays for; boto3 is sync, so keep it off
    # the event loop.
    try:
        keys = await asyncio.to_thread(_store_files, job.id, files)
    except StorageError as exc:
        logger.error("storage.unavailable", job_id=str(job.id), error=str(exc))
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Object storage is unavailable; the upload was not accepted.",
        ) from exc

    for filename, _data in files:
        session.add(
            Candidate(
                job_id=job.id,
                filename=filename,
                raw_file_key=keys.get(filename),
                status=CandidateStatus.PENDING,
                parsed_skills=[],
                education=[],
                experience=[],
            )
        )

    # Commit before dispatching: the worker may pick the task up immediately, and it
    # must be able to see the rows it is about to process.
    await session.commit()

    try:
        screen_job.delay(str(job.id))
    except Exception as exc:
        logger.exception("job.dispatch_failed", job_id=str(job.id))
        job.status = JobStatus.FAILED
        job.error = f"Could not queue the screening task: {exc}"
        job.completed_at = datetime.now(UTC)
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not queue the screening task; is the broker reachable?",
        ) from exc

    logger.info("job.queued", job_id=str(job.id), total_resumes=len(files))
    return JobCreatedResponse(job_id=job.id, status=JobStatus.QUEUED)


async def _get_job(session: AsyncSession, job_id: uuid.UUID) -> Job:
    job = await session.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Job {job_id} not found")
    return job


@router.get("/{job_id}", response_model=JobStatusResponse)
async def get_job_status(job_id: uuid.UUID, session: SessionDep) -> JobStatusResponse:
    job = await _get_job(session, job_id)
    done = job.processed_resumes + job.failed_resumes
    return JobStatusResponse(
        job_id=job.id,
        status=job.status,
        title=job.title,
        total_resumes=job.total_resumes,
        processed_resumes=job.processed_resumes,
        failed_resumes=job.failed_resumes,
        progress=round(done / job.total_resumes, 4) if job.total_resumes else 0.0,
        error=job.error,
        created_at=job.created_at,
        completed_at=job.completed_at,
    )


async def _latest_run(session: AsyncSession, job_id: uuid.UUID) -> ScoringRun | None:
    result = await session.execute(
        select(ScoringRun)
        .where(ScoringRun.job_id == job_id)
        .order_by(ScoringRun.created_at.desc(), ScoringRun.is_initial.asc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _build_results(session: AsyncSession, job: Job, run: ScoringRun) -> JobResultsResponse:
    rows = await session.execute(
        select(Score, Candidate)
        .join(Candidate, Score.candidate_id == Candidate.id)
        .where(Score.run_id == run.id)
        .order_by(Score.rank)
    )

    results = [
        CandidateResult(
            candidate_id=candidate.id,
            name=candidate.name,
            filename=candidate.filename,
            rank=score.rank,
            final_score=score.final_score,
            skill_score=score.skill_score,
            semantic_score=score.semantic_score,
            matched_skills=score.matched_skills,
            missing_skills=score.missing_skills,
            education=candidate.education,
            experience=candidate.experience,
        )
        for score, candidate in rows.all()
    ]

    failed = await session.execute(
        select(Candidate).where(
            Candidate.job_id == job.id, Candidate.status == CandidateStatus.FAILED
        )
    )
    failures = [
        FailedResumeInfo(filename=c.filename, error=c.error or "Unknown error")
        for c in failed.scalars().all()
    ]

    return JobResultsResponse(
        job_id=job.id,
        status=job.status,
        run_id=run.id,
        jd_text=run.jd_text,
        required_skills=run.required_skills,
        w_skill=run.w_skill,
        w_semantic=run.w_semantic,
        total_resumes=job.total_resumes,
        processed_resumes=job.processed_resumes,
        failed_resumes=job.failed_resumes,
        failures=failures,
        results=results,
    )


@router.get("/{job_id}/results", response_model=JobResultsResponse)
async def get_job_results(job_id: uuid.UUID, session: SessionDep) -> JobResultsResponse:
    job = await _get_job(session, job_id)
    run = await _latest_run(session, job_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Job {job_id} has no results yet (status: {job.status.value})",
        )
    return await _build_results(session, job, run)


@router.post("/{job_id}/rerank", response_model=JobResultsResponse)
async def rerank_job(
    job_id: uuid.UUID,
    payload: RerankRequest,
    session: SessionDep,
    settings: SettingsDep,
) -> JobResultsResponse:
    """Re-score existing candidates against a new JD using their cached embeddings.

    No re-parsing and no re-embedding of resumes — that is the whole point of storing
    the vectors. A new ScoringRun is created, so previous rankings remain queryable.
    """
    job = await _get_job(session, job_id)

    result = await session.execute(
        select(Candidate).where(
            Candidate.job_id == job_id, Candidate.status == CandidateStatus.PROCESSED
        )
    )
    candidates = list(result.scalars().all())
    if not candidates:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Job {job_id} has no successfully processed candidates to re-rank",
        )

    w_skill = settings.w_skill if payload.w_skill is None else payload.w_skill
    w_semantic = settings.w_semantic if payload.w_semantic is None else payload.w_semantic
    if abs((w_skill + w_semantic) - 1.0) > 1e-6:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"w_skill + w_semantic must sum to 1.0, got {w_skill + w_semantic}",
        )

    jd_text = payload.jd_text.strip()
    required_skills, jd_embedding = await asyncio.to_thread(
        lambda: (extract_required_skills(jd_text), embed_text(jd_text))
    )

    run = ScoringRun(
        job_id=job.id,
        jd_text=jd_text,
        jd_embedding=jd_embedding,
        required_skills=required_skills,
        w_skill=w_skill,
        w_semantic=w_semantic,
        is_initial=False,
    )
    session.add(run)
    await session.flush()

    _persist_scores(session, run, job.id, candidates)
    await session.commit()
    logger.info("job.reranked", job_id=str(job.id), run_id=str(run.id))

    await session.refresh(job)
    return await _build_results(session, job, run)
