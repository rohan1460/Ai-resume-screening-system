"""S3-compatible object storage for raw resume files.

Talks to MinIO locally and to real AWS S3 when ``S3_ENDPOINT`` is empty — the calling
code is identical either way.
"""

import uuid
from functools import lru_cache
from pathlib import Path

import boto3
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import Settings, get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class StorageError(Exception):
    pass


class ObjectStorage:
    def __init__(self, settings: Settings) -> None:
        self._bucket = settings.s3_bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
            config=Config(signature_version="s3v4", retries={"max_attempts": 3}),
        )

    @property
    def bucket(self) -> str:
        return self._bucket

    def ensure_bucket(self) -> None:
        """Create the bucket if it does not exist. Safe to call repeatedly."""
        try:
            self._client.head_bucket(Bucket=self._bucket)
        except ClientError:
            try:
                self._client.create_bucket(Bucket=self._bucket)
                logger.info("storage.bucket_created", bucket=self._bucket)
            except ClientError as exc:
                # A concurrent creator winning the race is fine.
                if exc.response.get("Error", {}).get("Code") not in {
                    "BucketAlreadyOwnedByYou",
                    "BucketAlreadyExists",
                }:
                    raise StorageError(f"Could not create bucket {self._bucket}: {exc}") from exc
        except BotoCoreError as exc:
            raise StorageError(f"Object storage unreachable: {exc}") from exc

    def build_key(self, job_id: uuid.UUID, filename: str) -> str:
        suffix = Path(filename).suffix.lower()
        return f"jobs/{job_id}/{uuid.uuid4().hex}{suffix}"

    def put(self, key: str, data: bytes, content_type: str | None = None) -> str:
        try:
            self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=data,
                **({"ContentType": content_type} if content_type else {}),
            )
        except (BotoCoreError, ClientError) as exc:
            raise StorageError(f"Failed to store object {key}: {exc}") from exc
        return key

    def get(self, key: str) -> bytes:
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
            body: bytes = response["Body"].read()
            return body
        except (BotoCoreError, ClientError) as exc:
            raise StorageError(f"Failed to read object {key}: {exc}") from exc


@lru_cache(maxsize=1)
def get_storage() -> ObjectStorage:
    return ObjectStorage(get_settings())
