import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config import get_settings
from app.db.base import Base

settings = get_settings()


class ScoringRun(Base):
    """One scoring pass of a job's candidates against a given JD.

    The initial screening creates the first run; every ``/rerank`` creates another,
    so earlier rankings stay queryable instead of being overwritten.
    """

    __tablename__ = "scoring_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    jd_text: Mapped[str] = mapped_column(Text, nullable=False)
    jd_embedding: Mapped[list[float] | None] = mapped_column(
        Vector(settings.embedding_dim), nullable=True
    )
    required_skills: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    w_skill: Mapped[float] = mapped_column(Float, nullable=False)
    w_semantic: Mapped[float] = mapped_column(Float, nullable=False)
    is_initial: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    job: Mapped["Job"] = relationship(back_populates="scoring_runs")  # noqa: F821
    scores: Mapped[list["Score"]] = relationship(  # noqa: F821
        back_populates="run", cascade="all, delete-orphan", lazy="selectin"
    )
