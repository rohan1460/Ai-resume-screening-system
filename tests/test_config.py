"""Configuration: everything from env, nothing dev-only reaching production."""

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import (
    DEV_POSTGRES_PASSWORD,
    DEV_S3_ACCESS_KEY,
    DEV_S3_SECRET_KEY,
    Settings,
    get_settings,
)

ROOT = Path(__file__).resolve().parents[1]
ENV_EXAMPLE = ROOT / ".env.example"

PROD_CREDS = {
    "postgres_password": "a-real-password",
    "s3_access_key": "AKIAREAL",
    "s3_secret_key": "a-real-secret",
    "api_key": "a-sufficiently-long-api-key",
}


class TestEnvExampleIsComplete:
    """`.env.example` is the deployment contract; a missing var is a broken deploy."""

    def test_every_setting_is_documented(self):
        documented = set(re.findall(r"^#?\s*([A-Z][A-Z0-9_]+)=", ENV_EXAMPLE.read_text(), re.M))
        fields = {name.upper() for name in Settings.model_fields}

        assert not (fields - documented), f"undocumented settings: {sorted(fields - documented)}"

    def test_no_stale_entries(self):
        """A var that no longer exists is worse than none: it silently does nothing."""
        documented = set(re.findall(r"^([A-Z][A-Z0-9_]+)=", ENV_EXAMPLE.read_text(), re.M))
        fields = {name.upper() for name in Settings.model_fields}
        # Vars consumed by docker-compose itself rather than by Settings.
        compose_only = {
            "MINIO_ROOT_USER",
            "MINIO_ROOT_PASSWORD",
            "MINIO_PORT",
            "MINIO_CONSOLE_PORT",
            "WORKER_CONCURRENCY",
            "FRONTEND_PORT",
        }

        assert not (documented - fields - compose_only)

    def test_contains_no_real_looking_secrets(self):
        """Placeholders only — this file is committed."""
        text = ENV_EXAMPLE.read_text()

        assert not re.search(r"AKIA[0-9A-Z]{16}", text)  # AWS access key ids
        assert "BEGIN RSA PRIVATE KEY" not in text
        assert "BEGIN PRIVATE KEY" not in text


class TestProductionCredentialGuard:
    def test_local_may_keep_the_dev_defaults(self):
        settings = Settings(app_env="local")

        assert settings.postgres_password == DEV_POSTGRES_PASSWORD

    @pytest.mark.parametrize("env", ["staging", "production"])
    def test_dev_defaults_are_rejected_outside_local(self, env):
        with pytest.raises(ValidationError, match="local-dev default"):
            Settings(app_env=env)

    @pytest.mark.parametrize(
        ("field", "dev_value"),
        [
            ("postgres_password", DEV_POSTGRES_PASSWORD),
            ("s3_access_key", DEV_S3_ACCESS_KEY),
            ("s3_secret_key", DEV_S3_SECRET_KEY),
        ],
    )
    def test_each_credential_is_checked_individually(self, field, dev_value):
        overrides = dict(PROD_CREDS)
        overrides[field] = dev_value

        with pytest.raises(ValidationError, match=field.upper()):
            Settings(app_env="production", **overrides)

    def test_production_with_real_credentials_is_accepted(self):
        settings = Settings(app_env="production", **PROD_CREDS)

        assert settings.app_env == "production"

    def test_the_error_names_every_offender(self):
        with pytest.raises(ValidationError) as exc:
            Settings(app_env="production")

        message = str(exc.value)
        for name in ("POSTGRES_PASSWORD", "S3_ACCESS_KEY", "S3_SECRET_KEY"):
            assert name in message


class TestNoMagicNumbers:
    """Tuning knobs live in Settings, not inline in the code that uses them."""

    def test_operational_limits_are_configurable(self):
        settings = get_settings()

        assert settings.celery_result_expires > 0
        assert settings.worker_db_pool_size > 0
        assert settings.max_error_length > 0
        assert settings.celery_max_retries >= 0
        assert settings.celery_retry_backoff > 0

    def test_they_are_overridable_from_env(self, monkeypatch):
        monkeypatch.setenv("CELERY_MAX_RETRIES", "7")
        monkeypatch.setenv("MAX_ERROR_LENGTH", "50")

        settings = Settings()

        assert settings.celery_max_retries == 7
        assert settings.max_error_length == 50

    def test_version_has_a_single_source(self):
        from app import __version__
        from app.main import create_app

        assert create_app().version == __version__
        assert __version__ != "0.0.0+unknown"


class TestWeightValidation:
    def test_weights_must_sum_to_one(self):
        with pytest.raises(ValidationError, match="sum to 1.0"):
            Settings(w_skill=0.7, w_semantic=0.5)

    def test_valid_weights_are_accepted(self):
        assert Settings(w_skill=0.8, w_semantic=0.2).w_skill == 0.8
