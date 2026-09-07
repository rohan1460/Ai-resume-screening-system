"""Object-storage tests against a live MinIO.

These are skipped when MinIO is not running, so the suite still passes without
docker-compose up. They exist because the API tests stub storage out entirely — with
only those, a broken MinIO path would ship silently.
"""

import uuid

import pytest

from app.core.config import Settings, get_settings
from app.services.storage import ObjectStorage, StorageError

pytestmark = pytest.mark.no_stubs


@pytest.fixture(scope="module")
def storage() -> ObjectStorage:
    settings = get_settings()
    store = ObjectStorage(settings)
    try:
        store.ensure_bucket()
    except StorageError as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"MinIO not reachable: {exc}")
    return store


class TestBucket:
    def test_ensure_bucket_is_idempotent(self, storage):
        storage.ensure_bucket()
        storage.ensure_bucket()

        assert storage.bucket == get_settings().s3_bucket


class TestBuildKey:
    def test_key_is_namespaced_by_job(self, storage):
        job_id = uuid.uuid4()

        key = storage.build_key(job_id, "priya_sharma.pdf")

        assert key.startswith(f"jobs/{job_id}/")

    def test_key_preserves_the_extension(self, storage):
        assert storage.build_key(uuid.uuid4(), "resume.PDF").endswith(".pdf")
        assert storage.build_key(uuid.uuid4(), "resume.docx").endswith(".docx")

    def test_keys_are_unique_for_identical_filenames(self, storage):
        job_id = uuid.uuid4()

        first = storage.build_key(job_id, "resume.pdf")
        second = storage.build_key(job_id, "resume.pdf")

        assert first != second

    def test_key_does_not_leak_the_original_filename(self, storage):
        """Candidate names in filenames should not become guessable object URLs."""
        key = storage.build_key(uuid.uuid4(), "priya_sharma_salary_expectations.pdf")

        assert "priya" not in key.lower()


class TestRoundTrip:
    def test_stored_bytes_come_back_unchanged(self, storage):
        payload = b"%PDF-1.4 pretend resume bytes \x00\x01\x02"
        key = storage.build_key(uuid.uuid4(), "resume.pdf")

        storage.put(key, payload)

        assert storage.get(key) == payload

    def test_stores_a_realistic_pdf(self, storage):
        from tests.factories import SAMPLE_RESUME, make_pdf_bytes

        payload = make_pdf_bytes(SAMPLE_RESUME)
        key = storage.build_key(uuid.uuid4(), "resume.pdf")

        storage.put(key, payload)

        assert storage.get(key) == payload

    def test_content_type_is_accepted(self, storage):
        key = storage.build_key(uuid.uuid4(), "resume.pdf")

        storage.put(key, b"data", content_type="application/pdf")

        assert storage.get(key) == b"data"


class TestFailures:
    def test_reading_a_missing_key_raises(self, storage):
        with pytest.raises(StorageError, match="Failed to read object"):
            storage.get(f"jobs/{uuid.uuid4()}/does-not-exist.pdf")

    def test_unreachable_endpoint_raises_storage_error(self):
        settings = Settings(s3_endpoint="http://127.0.0.1:1", s3_bucket="nope")
        store = ObjectStorage(settings)

        with pytest.raises(StorageError):
            store.ensure_bucket()
