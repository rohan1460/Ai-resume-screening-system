import enum
import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, Enum, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config import get_settings
from app.db.base import Base

settings = get_settings()


class JobStatus(enum.StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, name="job_status", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        default=JobStatus.QUEUED,
        index=True,
    )
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    jd_text: Mapped[str] = mapped_column(Text, nullable=False)
    jd_embedding: Mapped[list[float] | None] = mapped_column(
        Vector(settings.embedding_dim), nullable=True
    )
    total_resumes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    processed_resumes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_resumes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    candidates: Mapped[list["Candidate"]] = relationship(  # noqa: F821
        back_populates="job", cascade="all, delete-orphan", lazy="selectin"
    )
    scoring_runs: Mapped[list["ScoringRun"]] = relationship(  # noqa: F821
        back_populates="job", cascade="all, delete-orphan", lazy="selectin"
    )
