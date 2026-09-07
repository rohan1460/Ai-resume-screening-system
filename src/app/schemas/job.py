import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.job import JobStatus


class JobCreatedResponse(BaseModel):
    job_id: uuid.UUID
    status: JobStatus


class FailedResumeInfo(BaseModel):
    filename: str
    error: str


class JobStatusResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    job_id: uuid.UUID
    status: JobStatus
    title: str | None = None
    total_resumes: int
    processed_resumes: int
    failed_resumes: int
    progress: float = Field(description="processed_resumes / total_resumes, 0..1")
    error: str | None = None
    created_at: datetime
    completed_at: datetime | None = None


class CandidateResult(BaseModel):
    candidate_id: uuid.UUID
    name: str | None
    filename: str
    rank: int
    final_score: float
    skill_score: float
    semantic_score: float
    matched_skills: list[str]
    missing_skills: list[str]
    education: list[dict[str, Any]] = Field(default_factory=list)
    experience: list[dict[str, Any]] = Field(default_factory=list)


class JobResultsResponse(BaseModel):
    job_id: uuid.UUID
    status: JobStatus
    run_id: uuid.UUID
    jd_text: str
    required_skills: list[str]
    w_skill: float
    w_semantic: float
    total_resumes: int
    processed_resumes: int
    failed_resumes: int
    failures: list[FailedResumeInfo] = Field(default_factory=list)
    results: list[CandidateResult] = Field(default_factory=list)


class RerankRequest(BaseModel):
    jd_text: str = Field(min_length=1, description="The new job description to score against")
    w_skill: float | None = Field(default=None, ge=0.0, le=1.0)
    w_semantic: float | None = Field(default=None, ge=0.0, le=1.0)

    @field_validator("jd_text")
    @classmethod
    def _reject_blank_jd(cls, value: str) -> str:
        """A whitespace-only JD passes ``min_length`` but scores everyone on nothing.

        Strip here so the endpoint always receives usable text.
        """
        stripped = value.strip()
        if not stripped:
            raise ValueError("jd_text must contain a job description, not just whitespace")
        return stripped
