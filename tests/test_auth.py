"""Token auth on the job endpoints.

The default configuration has no API key, so the rest of the suite runs unauthenticated
(as local development does). These tests enable auth explicitly.
"""

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.core.security import extract_bearer_token
from app.db.session import get_session
from app.main import create_app

API_KEY = "test-key-that-is-long-enough"
AUTH = {"Authorization": f"Bearer {API_KEY}"}


@pytest.fixture
async def secured_client(engine, db_session, celery_eager, monkeypatch):
    """A client whose app requires the API key."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    monkeypatch.setenv("API_KEY", API_KEY)
    get_settings.cache_clear()

    app = create_app()
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def override_get_session():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()
    get_settings.cache_clear()


PROTECTED = [
    ("get", f"/jobs/{uuid.uuid4()}"),
    ("get", f"/jobs/{uuid.uuid4()}/results"),
    ("post", f"/jobs/{uuid.uuid4()}/rerank"),
]


class TestUnauthorizedIsRejected:
    async def test_creating_a_job_without_a_token_is_401(self, secured_client):
        response = await secured_client.post("/jobs", data={"jd_text": "Python engineer."})

        assert response.status_code == 401
        assert "api key" in response.json()["detail"].lower()

    @pytest.mark.parametrize(("method", "path"), PROTECTED)
    async def test_every_job_endpoint_requires_a_token(self, secured_client, method, path):
        response = await getattr(secured_client, method)(path)

        assert response.status_code == 401

    async def test_auth_runs_before_validation(self, secured_client):
        """An unauthenticated caller must not learn which requests are well-formed."""
        response = await secured_client.post("/jobs")  # missing every required field

        assert response.status_code == 401  # not 422

    async def test_unknown_job_is_still_401_not_404(self, secured_client):
        """Otherwise the endpoint is an oracle for which job ids exist."""
        response = await secured_client.get(f"/jobs/{uuid.uuid4()}")

        assert response.status_code == 401


class TestRejectedTokenShapes:
    @pytest.mark.parametrize(
        "header",
        [
            "",
            "Bearer",
            "Bearer ",
            "wrong-key",
            f"Basic {API_KEY}",
            f"Token {API_KEY}",
            "Bearer wrong-key-entirely",
            f"Bearer {API_KEY}x",
            f"Bearer {API_KEY[:-1]}",
        ],
    )
    async def test_malformed_or_wrong_tokens_are_rejected(self, secured_client, header):
        response = await secured_client.get(
            f"/jobs/{uuid.uuid4()}", headers={"Authorization": header}
        )

        assert response.status_code == 401

    async def test_response_advertises_the_scheme(self, secured_client):
        response = await secured_client.get(f"/jobs/{uuid.uuid4()}")

        assert response.headers.get("WWW-Authenticate") == "Bearer"

    async def test_the_error_never_echoes_the_expected_key(self, secured_client):
        response = await secured_client.get(
            f"/jobs/{uuid.uuid4()}", headers={"Authorization": "Bearer nope"}
        )

        assert API_KEY not in response.text


class TestAuthorizedRequestsWork:
    async def test_a_valid_token_reaches_the_endpoint(self, secured_client):
        response = await secured_client.get(f"/jobs/{uuid.uuid4()}", headers=AUTH)

        assert response.status_code == 404  # got past auth, job genuinely absent

    async def test_the_scheme_is_case_insensitive(self, secured_client):
        response = await secured_client.get(
            f"/jobs/{uuid.uuid4()}", headers={"Authorization": f"bearer {API_KEY}"}
        )

        assert response.status_code == 404

    async def test_full_flow_with_a_token(self, secured_client):
        from tests.factories import SAMPLE_RESUME, make_pdf_bytes

        created = await secured_client.post(
            "/jobs",
            data={"jd_text": "Backend engineer with Python and FastAPI."},
            files=[("resumes", ("a.pdf", make_pdf_bytes(SAMPLE_RESUME), "application/pdf"))],
            headers=AUTH,
        )
        assert created.status_code == 201

        job_id = created.json()["job_id"]
        for _ in range(100):
            status = (await secured_client.get(f"/jobs/{job_id}", headers=AUTH)).json()
            if status["status"] in {"completed", "failed"}:
                break

        results = await secured_client.get(f"/jobs/{job_id}/results", headers=AUTH)

        assert results.status_code == 200
        assert len(results.json()["results"]) == 1


class TestHealthStaysPublic:
    async def test_health_needs_no_token(self, secured_client):
        response = await secured_client.get("/health")

        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    @pytest.mark.parametrize("path", ["/docs", "/openapi.json"])
    async def test_docs_stay_public(self, secured_client, path):
        assert (await secured_client.get(path)).status_code == 200


class TestAuthDisabledByDefault:
    """Local development runs without a key; the suite depends on it."""

    async def test_no_key_means_no_auth(self, client):
        response = await client.get(f"/jobs/{uuid.uuid4()}")

        assert response.status_code == 404  # reached the handler, not 401

    def test_auth_enabled_reflects_the_key(self):
        assert Settings(api_key=None).auth_enabled is False
        assert Settings(api_key=API_KEY).auth_enabled is True


class TestExtractBearerToken:
    @pytest.mark.parametrize(
        ("header", "expected"),
        [
            ("Bearer abc123", "abc123"),
            ("bearer abc123", "abc123"),
            ("BEARER abc123", "abc123"),
            ("Bearer   abc123  ", "abc123"),
            (None, None),
            ("", None),
            ("Bearer", None),
            ("Bearer   ", None),
            ("Basic abc123", None),
            ("abc123", None),
        ],
    )
    def test_parsing(self, header, expected):
        assert extract_bearer_token(header) == expected


class TestDeployedEnvironmentsRequireAKey:
    @pytest.mark.parametrize("env", ["staging", "production"])
    def test_missing_key_is_rejected(self, env):
        with pytest.raises(ValidationError, match="requires API_KEY"):
            Settings(
                app_env=env,
                postgres_password="real",
                s3_access_key="real",
                s3_secret_key="real",
            )

    def test_short_keys_are_rejected(self):
        with pytest.raises(ValidationError, match="at least"):
            Settings(api_key="tooshort")

    def test_a_long_key_is_accepted_in_production(self):
        settings = Settings(
            app_env="production",
            postgres_password="real",
            s3_access_key="real",
            s3_secret_key="real",
            api_key=API_KEY,
        )

        assert settings.auth_enabled is True
