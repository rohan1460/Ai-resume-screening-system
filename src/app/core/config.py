from functools import lru_cache
from typing import Annotated, Any, Literal

from pydantic import Field, computed_field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# Local-dev defaults, named so the production guard below can reject them by identity
# rather than by a string repeated in two places.
DEV_POSTGRES_PASSWORD = "resume"
DEV_S3_ACCESS_KEY = "minioadmin"
DEV_S3_SECRET_KEY = "minioadmin"

# Short enough to type, long enough that guessing it is not worth attempting.
MIN_API_KEY_LENGTH = 16


def _split_csv(value: Any) -> Any:
    """Accept comma-separated env values for list fields, e.g. ``A=x,y,z``."""
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Application ---
    app_name: str = "AI Resume Screening System"
    app_env: Literal["local", "staging", "production"] = "local"
    debug: bool = False
    log_level: str = "INFO"
    log_json: bool = False
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:5173"]
    )

    # --- Auth ---
    # Static bearer token for the job endpoints; /health stays public. Unset means
    # auth is off, which the validator below permits only when APP_ENV=local.
    api_key: str | None = None

    # --- Postgres ---
    postgres_user: str = "resume"
    postgres_password: str = DEV_POSTGRES_PASSWORD
    postgres_db: str = "resume_screening"
    postgres_host: str = "localhost"
    postgres_port: int = 5432

    # --- Redis ---
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0

    # --- Celery ---
    # Both default to the Redis URL above; override to split broker from backend.
    celery_broker_url: str | None = None
    celery_result_backend: str | None = None
    # Hard/soft ceilings on one screening task. A 100-resume batch is the design
    # target; the soft limit lets the task mark the job failed before it is killed.
    celery_task_time_limit: int = 3600
    celery_task_soft_time_limit: int = 3300
    # Retries for transient failures (storage blips, DB restarts) — not for bad input.
    celery_max_retries: int = 3
    celery_retry_backoff: int = 5
    # How long task results stay in the Redis backend.
    celery_result_expires: int = 86400
    # A worker child handles one task at a time, so it needs very few connections.
    worker_db_pool_size: int = 2
    worker_db_max_overflow: int = 2
    # Job.error is a text column, but an unbounded driver traceback helps nobody.
    max_error_length: int = 2000

    # --- Object storage (MinIO locally, AWS S3 in deployed envs) ---
    # Leave s3_endpoint empty to talk to real AWS S3.
    s3_endpoint: str | None = "http://localhost:9000"
    s3_access_key: str = DEV_S3_ACCESS_KEY
    s3_secret_key: str = DEV_S3_SECRET_KEY
    s3_bucket: str = "resumes"
    s3_region: str = "us-east-1"

    # --- ML / NLP ---
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_dim: int = 384
    spacy_model: str = "en_core_web_sm"
    # Pinned to CPU deliberately. sentence-transformers otherwise picks MPS on macOS,
    # and a torch model initialised on MPS crashes the moment Celery's prefork pool
    # forks a child — the worker then respawns children forever. MiniLM inference on
    # CPU is fast enough that the accelerator buys little here. Set to "mps"/"cuda"
    # only for a non-forking pool (e.g. --pool=solo or --pool=threads).
    embedding_device: str = "cpu"
    # Load the embedding model at API startup rather than on the first /rerank.
    # Turn off for a fast-booting API that never reranks.
    warm_models_on_startup: bool = True

    # --- Scoring ---
    # final_score = w_skill * skill_score + w_semantic * semantic_score
    w_skill: float = 0.6
    w_semantic: float = 0.4
    skills_dictionary_path: str = "config/skills.txt"
    # Optional overlay: adds skills and aliases on top of the curated list above,
    # so local additions survive an update to the base dictionary.
    extra_skills_path: str | None = None

    # --- Uploads ---
    max_upload_mb: int = 10
    allowed_upload_extensions: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: [".pdf", ".docx"]
    )

    _split_lists = field_validator("cors_origins", "allowed_upload_extensions", mode="before")(
        _split_csv
    )

    @field_validator("s3_endpoint", mode="before")
    @classmethod
    def _blank_endpoint_is_none(cls, value: Any) -> Any:
        """An empty S3_ENDPOINT means "use real AWS S3", not "use an empty URL"."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("api_key", mode="before")
    @classmethod
    def _blank_api_key_is_none(cls, value: Any) -> Any:
        """``API_KEY=`` means unset, not a zero-length key."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def _check_weights(self) -> "Settings":
        total = self.w_skill + self.w_semantic
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"W_SKILL + W_SEMANTIC must sum to 1.0, got {total}")
        return self

    @model_validator(mode="after")
    def _reject_dev_credentials_outside_local(self) -> "Settings":
        """Fail fast if a deployed environment is still on the local-dev credentials.

        The defaults exist so ``docker compose up`` works with no setup. That
        convenience becomes a security hole the moment it reaches staging or
        production silently — an unset ``S3_SECRET_KEY`` would otherwise look like a
        working deploy while the store is wide open on a known password.
        """
        if self.app_env == "local":
            return self

        offenders = [
            name
            for name, dev_default in (
                ("POSTGRES_PASSWORD", DEV_POSTGRES_PASSWORD),
                ("S3_ACCESS_KEY", DEV_S3_ACCESS_KEY),
                ("S3_SECRET_KEY", DEV_S3_SECRET_KEY),
            )
            if getattr(self, name.lower()) == dev_default
        ]
        if offenders:
            raise ValueError(
                f"APP_ENV={self.app_env} but these still hold their local-dev default: "
                f"{', '.join(offenders)}. Set them explicitly."
            )
        return self

    @model_validator(mode="after")
    def _require_api_key_outside_local(self) -> "Settings":
        """A deployed environment must not run with the job endpoints unauthenticated."""
        if self.app_env != "local" and not self.api_key:
            raise ValueError(
                f"APP_ENV={self.app_env} requires API_KEY to be set; "
                "the job endpoints would otherwise be open to anyone."
            )
        if self.api_key is not None and len(self.api_key) < MIN_API_KEY_LENGTH:
            raise ValueError(
                f"API_KEY must be at least {MIN_API_KEY_LENGTH} characters "
                '(generate one with: python -c "import secrets; print(secrets.token_urlsafe(32))")'
            )
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def auth_enabled(self) -> bool:
        return bool(self.api_key)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def database_url(self) -> str:
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def redis_url(self) -> str:
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @computed_field  # type: ignore[prop-decorator]
    @property
    def broker_url(self) -> str:
        return self.celery_broker_url or self.redis_url

    @computed_field  # type: ignore[prop-decorator]
    @property
    def result_backend(self) -> str:
        return self.celery_result_backend or self.redis_url


@lru_cache
def get_settings() -> Settings:
    return Settings()
