"""Object storage for recordings/attachments: filesystem locally, MinIO/S3 in compose."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from app.core.config import get_settings


class LocalStorage:
    """Dev/test driver. Keys are namespaced under STORAGE_DIR; traversal is refused."""

    name = "local"

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root or get_settings().STORAGE_DIR)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        """Resolve `key` under the root, refusing anything that escapes it.

        `str(target).startswith(str(root))` was the first guard and it is not one: `/srv/objects_evil`
        starts with `/srv/objects`, so a sibling directory passed. `Path.is_relative_to` compares path
        components, which is the property we actually need. Keys are generated server-side and signed,
        so this was a latent hole rather than an open door — closed while it is cheap.
        """
        root = self.root.resolve()
        target = (root / key).resolve()
        if target != root and not target.is_relative_to(root):
            raise ValueError("refusing to write outside the storage root")
        return target

    def put_object(self, key: str, data: bytes, content_type: str) -> dict[str, Any]:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return {"key": key, "size": len(data), "checksum": hashlib.sha256(data).hexdigest(), "content_type": content_type}

    def get_object(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def delete_object(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)


class S3Storage:
    """MinIO / AWS S3 driver (bucket-level encryption is a deployment setting, spec §6)."""

    name = "s3"

    def __init__(self) -> None:
        import boto3

        settings = get_settings()
        self.bucket = settings.S3_BUCKET
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.S3_ENDPOINT_URL,
            aws_access_key_id=settings.S3_ACCESS_KEY,
            aws_secret_access_key=settings.S3_SECRET_KEY,
        )
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except Exception:
            self.client.create_bucket(Bucket=self.bucket)

    def put_object(self, key: str, data: bytes, content_type: str) -> dict[str, Any]:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type, ServerSideEncryption="AES256")
        return {"key": key, "size": len(data), "checksum": hashlib.sha256(data).hexdigest(), "content_type": content_type}

    def get_object(self, key: str) -> bytes:
        body = self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        return bytes(body)

    def delete_object(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)


def storage() -> Any:
    settings = get_settings()
    if settings.STORAGE_BACKEND == "s3":
        try:
            return S3Storage()
        except Exception:  # pragma: no cover - falls back so `make dev` works without MinIO
            return LocalStorage()
    return LocalStorage()
