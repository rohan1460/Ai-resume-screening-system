"""Orchestrates the CPU-bound screening pipeline.

    parse -> extract skills -> extract fields -> embed -> score

Everything here is synchronous and free of I/O so it can be handed to a worker thread
in Phase 1 and to a Celery task in Phase 2 without change. A failure on one resume is
captured as a ``FailedResume`` rather than raised, so one bad file never sinks the batch.
"""

from dataclasses import dataclass, field
from typing import Any

from app.core.logging import get_logger
from app.services.embeddings import embed_texts
from app.services.extraction import ExtractionError, extract_text
from app.services.resume_parser import parse_resume_fields
from app.services.scoring import ScoreBreakdown, rank_breakdowns, score_candidate
from app.services.skills import get_required_skills, get_skill_extractor

logger = get_logger(__name__)


@dataclass
class ParsedResume:
    filename: str
    text: str
    name: str | None = None
    skills: list[str] = field(default_factory=list)
    education: list[dict[str, Any]] = field(default_factory=list)
    experience: list[dict[str, Any]] = field(default_factory=list)
    embedding: list[float] | None = None


@dataclass
class FailedResume:
    filename: str
    error: str


@dataclass
class ParseOutcome:
    parsed: list[ParsedResume] = field(default_factory=list)
    failed: list[FailedResume] = field(default_factory=list)


def parse_resumes(files: list[tuple[str, bytes]]) -> ParseOutcome:
    """Extract text, skills and fields for each file, then embed them in one batch.

    Batching the embedding call is materially faster than embedding per resume.
    """
    outcome = ParseOutcome()
    extractor = get_skill_extractor()

    for filename, data in files:
        try:
            text = extract_text(filename, data)
        except ExtractionError as exc:
            logger.warning("resume.failed", filename=filename, error=str(exc))
            outcome.failed.append(FailedResume(filename=filename, error=str(exc)))
            continue

        fields = parse_resume_fields(text, filename)
        outcome.parsed.append(
            ParsedResume(
                filename=filename,
                text=text,
                name=fields["name"],
                skills=extractor.extract(text),
                education=fields["education"],
                experience=fields["experience"],
            )
        )

    if outcome.parsed:
        vectors = embed_texts([r.text for r in outcome.parsed])
        for resume, vector in zip(outcome.parsed, vectors, strict=True):
            resume.embedding = vector

    return outcome


def extract_required_skills(jd_text: str) -> list[str]:
    """Sorted list form of :func:`get_required_skills`, for JSONB storage."""
    return sorted(get_required_skills(jd_text))


def score_resumes(
    jd_embedding: list[float] | None,
    required_skills: list[str],
    candidates: dict[str, tuple[list[str], list[float] | None]],
    w_skill: float | None = None,
    w_semantic: float | None = None,
) -> dict[str, ScoreBreakdown]:
    """Score and rank candidates.

    ``candidates`` maps an opaque key (a candidate id) to that candidate's
    ``(skills, embedding)``. Keys flow straight back out, so the caller decides what
    identity means.
    """
    breakdowns = {
        key: score_candidate(
            required_skills=required_skills,
            candidate_skills=skills,
            jd_embedding=jd_embedding,
            candidate_embedding=embedding,
            w_skill=w_skill,
            w_semantic=w_semantic,
        )
        for key, (skills, embedding) in candidates.items()
    }
    return rank_breakdowns(breakdowns)
