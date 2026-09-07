import uuid

from sqlalchemy import Float, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Score(Base):
    """A candidate's explainable score within one scoring run."""

    __tablename__ = "scores"
    __table_args__ = (UniqueConstraint("run_id", "candidate_id", name="uq_scores_run_candidate"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("scoring_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("candidates.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # All three are 0..100 for display; the 0..1 maths lives in the scoring engine.
    skill_score: Mapped[float] = mapped_column(Float, nullable=False)
    semantic_score: Mapped[float] = mapped_column(Float, nullable=False)
    final_score: Mapped[float] = mapped_column(Float, nullable=False)

    matched_skills: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    missing_skills: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    rank: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    run: Mapped["ScoringRun"] = relationship(back_populates="scores")  # noqa: F821
    candidate: Mapped["Candidate"] = relationship(back_populates="scores")  # noqa: F821
