"""The explainable scoring engine.

    skill_score    = |matched_skills| / |required_skills|          (keyword layer)
    semantic_score = cosine_similarity(jd_embedding, resume_embedding)  (context layer)
    final_score    = w_skill * skill_score + w_semantic * semantic_score

Weights come from settings, never from a literal inside a function. All three scores
are reported on a 0-100 scale for display; the maths above runs in 0-1.

Pure functions only — no DB, no model loading — so the engine is deterministic and
directly unit-testable.
"""

import math
from dataclasses import dataclass, field

from app.core.config import get_settings

SCORE_SCALE = 100.0


@dataclass(frozen=True)
class ScoreBreakdown:
    """One candidate's explainable score. Every field answers "why this rank?"."""

    skill_score: float  # 0-100
    semantic_score: float  # 0-100
    final_score: float  # 0-100
    matched_skills: list[str] = field(default_factory=list)
    missing_skills: list[str] = field(default_factory=list)
    rank: int = 0


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity in [-1, 1]. Returns 0.0 if either vector is null/zero."""
    if not a or not b:
        return 0.0
    if len(a) != len(b):
        raise ValueError(f"Vector dimension mismatch: {len(a)} != {len(b)}")

    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def match_skills(
    required_skills: list[str], candidate_skills: list[str]
) -> tuple[list[str], list[str]]:
    """Split the JD's required skills into those the candidate has and lacks.

    Comparison is case-insensitive, but the JD's original spelling is what gets
    reported back to the recruiter.
    """
    candidate_lookup = {skill.casefold() for skill in candidate_skills}
    matched = [s for s in required_skills if s.casefold() in candidate_lookup]
    missing = [s for s in required_skills if s.casefold() not in candidate_lookup]
    return matched, missing


def compute_skill_score(matched_count: int, required_count: int) -> float:
    """Fraction of required skills present, in [0, 1].

    A JD with no recognizable skills gives every candidate 0.0 — the keyword layer
    has no signal, so it contributes nothing and ranking falls to the semantic layer.
    """
    if required_count <= 0:
        return 0.0
    return matched_count / required_count


def normalize_semantic_score(similarity: float) -> float:
    """Clamp cosine similarity into [0, 1].

    Clamping rather than rescaling (sim + 1) / 2: a resume unrelated to the JD should
    score ~0, not 0.5.
    """
    return min(1.0, max(0.0, similarity))


def score_candidate(
    required_skills: list[str],
    candidate_skills: list[str],
    jd_embedding: list[float] | None,
    candidate_embedding: list[float] | None,
    w_skill: float | None = None,
    w_semantic: float | None = None,
) -> ScoreBreakdown:
    settings = get_settings()
    weight_skill = settings.w_skill if w_skill is None else w_skill
    weight_semantic = settings.w_semantic if w_semantic is None else w_semantic

    matched, missing = match_skills(required_skills, candidate_skills)
    skill_component = compute_skill_score(len(matched), len(required_skills))

    similarity = cosine_similarity(jd_embedding or [], candidate_embedding or [])
    semantic_component = normalize_semantic_score(similarity)

    final = weight_skill * skill_component + weight_semantic * semantic_component

    return ScoreBreakdown(
        skill_score=round(skill_component * SCORE_SCALE, 2),
        semantic_score=round(semantic_component * SCORE_SCALE, 2),
        final_score=round(final * SCORE_SCALE, 2),
        matched_skills=matched,
        missing_skills=missing,
    )


def rank_breakdowns(
    breakdowns: dict[str, ScoreBreakdown],
) -> dict[str, ScoreBreakdown]:
    """Assign 1-based ranks by descending final score.

    Ties break on the key so ranking is deterministic across runs; equal scores keep
    distinct sequential ranks rather than sharing one.
    """
    ordered = sorted(breakdowns.items(), key=lambda item: (-item[1].final_score, str(item[0])))
    return {
        key: ScoreBreakdown(
            skill_score=b.skill_score,
            semantic_score=b.semantic_score,
            final_score=b.final_score,
            matched_skills=b.matched_skills,
            missing_skills=b.missing_skills,
            rank=position,
        )
        for position, (key, b) in enumerate(ordered, start=1)
    }
