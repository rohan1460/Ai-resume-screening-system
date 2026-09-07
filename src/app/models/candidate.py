import enum
import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, Enum, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config import get_settings
from app.db.base import Base

settings = get_settings()


class CandidateStatus(enum.StrEnum):
    PENDING = "pending"
    PROCESSED = "processed"
    FAILED = "failed"


class Candidate(Base):
    """One uploaded resume within a screening job.

    A candidate that fails to parse is kept with status FAILED and an ``error``
    message, so one bad file never sinks the rest of the batch.
    """

    __tablename__ = "candidates"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    raw_file_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    status: Mapped[CandidateStatus] = mapped_column(
        Enum(
            CandidateStatus,
            name="candidate_status",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=CandidateStatus.PENDING,
        index=True,
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    parsed_skills: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    education: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    experience: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(settings.embedding_dim), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    job: Mapped["Job"] = relationship(back_populates="candidates")  # noqa: F821
    scores: Mapped[list["Score"]] = relationship(  # noqa: F821
        back_populates="candidate", cascade="all, delete-orphan"
    )
