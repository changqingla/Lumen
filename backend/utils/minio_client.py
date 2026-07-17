"""MinIO client for object storage operations."""
import asyncio
import logging
import os
import tempfile
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse

from minio import Minio
from minio.error import S3Error
from config.settings import settings

logger = logging.getLogger(__name__)

DEFAULT_DOWNLOAD_CHUNK_SIZE = 64 * 1024


@dataclass(frozen=True, slots=True)
class ObjectMetadata:
    size: int
    content_type: str | None
    etag: str | None


class ObjectStorageError(RuntimeError):
    """Stable storage failure that contains no object name or provider detail."""

    def __init__(self, operation: str):
        self.operation = operation
        super().__init__("Object storage operation failed")


def _storage_error(operation: str, error: BaseException) -> ObjectStorageError:
    logger.error(
        "object_storage operation=%s error_type=%s",
        operation,
        type(error).__name__,
    )
    return ObjectStorageError(operation)


def _build_minio_client() -> Minio:
    """构建 MinIO 客户端。"""
    return Minio(
        settings.MINIO_ENDPOINT,
        access_key=settings.MINIO_ACCESS_KEY,
        secret_key=settings.MINIO_SECRET_KEY,
        secure=settings.MINIO_SECURE,
    )


# Initialize MinIO client
minio_client = _build_minio_client()


def _ensure_bucket_exists_sync():
    """Ensure the default bucket exists."""
    if not minio_client.bucket_exists(settings.MINIO_BUCKET):
        minio_client.make_bucket(settings.MINIO_BUCKET)
        logger.info("Object storage bucket created")


async def ensure_bucket_exists():
    """Ensure the default bucket exists."""
    try:
        await asyncio.to_thread(_ensure_bucket_exists_sync)
    except S3Error as exc:
        raise _storage_error("ensure_bucket", exc) from exc


def _upload_file_sync(object_name: str, file_data: bytes, content_type: str) -> str:
    file_stream = BytesIO(file_data)
    file_size = len(file_data)
    minio_client.put_object(
        settings.MINIO_BUCKET,
        object_name,
        file_stream,
        file_size,
        content_type=content_type
    )
    logger.info("Object uploaded (%s bytes)", file_size)
    return f"{settings.MINIO_BUCKET}/{object_name}"


async def upload_file(object_name: str, file_data: bytes, content_type: str = "application/octet-stream") -> str:
    """Upload file to MinIO."""
    try:
        await ensure_bucket_exists()
        return await asyncio.to_thread(_upload_file_sync, object_name, file_data, content_type)
    except S3Error as exc:
        raise _storage_error("upload_bytes", exc) from exc


def _upload_file_from_path_sync(
    object_name: str,
    file_path: str | os.PathLike[str],
    content_type: str,
) -> str:
    path = Path(file_path)
    minio_client.fput_object(
        settings.MINIO_BUCKET,
        object_name,
        str(path),
        content_type=content_type,
    )
    logger.info("Object uploaded (%s bytes)", path.stat().st_size)
    return f"{settings.MINIO_BUCKET}/{object_name}"


async def upload_file_from_path(
    object_name: str,
    file_path: str | os.PathLike[str],
    content_type: str = "application/octet-stream",
) -> str:
    """Upload a local file without materializing its contents as ``bytes``."""
    try:
        await ensure_bucket_exists()
        return await asyncio.to_thread(
            _upload_file_from_path_sync,
            object_name,
            file_path,
            content_type,
        )
    except S3Error as exc:
        raise _storage_error("upload_path", exc) from exc


def _download_file_sync(object_name: str) -> bytes:
    response = minio_client.get_object(settings.MINIO_BUCKET, object_name)
    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()


async def download_file(object_name: str) -> bytes:
    """Download file from MinIO."""
    try:
        return await asyncio.to_thread(_download_file_sync, object_name)
    except S3Error as exc:
        raise _storage_error("download_bytes", exc) from exc


def _download_file_to_path_sync(
    object_name: str,
    destination: str | os.PathLike[str],
    *,
    max_bytes: int | None = None,
    chunk_size: int = DEFAULT_DOWNLOAD_CHUNK_SIZE,
) -> int:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if max_bytes is not None and max_bytes < 0:
        raise ValueError("max_bytes cannot be negative")

    response = minio_client.get_object(settings.MINIO_BUCKET, object_name)
    total_bytes = 0
    try:
        # The destination is created by ``mkstemp``. Requiring it to exist
        # prevents a cancelled worker thread from recreating the path after
        # the async context has already unlinked it.
        with Path(destination).open("r+b") as output:
            output.truncate(0)
            while True:
                chunk = response.read(chunk_size)
                if not chunk:
                    break
                total_bytes += len(chunk)
                if max_bytes is not None and total_bytes > max_bytes:
                    raise ValueError(
                        f"Downloaded object exceeds the {max_bytes}-byte limit"
                    )
                output.write(chunk)
        return total_bytes
    finally:
        response.close()
        response.release_conn()


@asynccontextmanager
async def temporary_download(
    object_name: str,
    *,
    suffix: str = "",
    max_bytes: int | None = None,
    chunk_size: int = DEFAULT_DOWNLOAD_CHUNK_SIZE,
) -> AsyncIterator[Path]:
    """Download an object into a bounded temporary file and always remove it."""
    fd, raw_path = tempfile.mkstemp(prefix="lumen-minio-", suffix=suffix)
    os.close(fd)
    temp_path = Path(raw_path)
    try:
        try:
            await asyncio.to_thread(
                _download_file_to_path_sync,
                object_name,
                temp_path,
                max_bytes=max_bytes,
                chunk_size=chunk_size,
            )
        except S3Error as exc:
            raise _storage_error("temporary_download", exc) from exc
        yield temp_path
    finally:
        temp_path.unlink(missing_ok=True)


def stream_file(object_name: str, chunk_size: int = 64 * 1024) -> Iterator[bytes]:
    """Stream a file from MinIO in chunks."""

    def iterator() -> Iterator[bytes]:
        try:
            response = minio_client.get_object(settings.MINIO_BUCKET, object_name)
        except S3Error as exc:
            raise _storage_error("stream_open", exc) from exc

        try:
            while True:
                chunk = response.read(chunk_size)
                if not chunk:
                    break
                yield chunk
        finally:
            response.close()
            response.release_conn()

    return iterator()


def _object_exists_sync(object_name: str) -> bool:
    try:
        minio_client.stat_object(settings.MINIO_BUCKET, object_name)
        return True
    except S3Error as e:
        if e.code in {"NoSuchKey", "NoSuchObject", "NoSuchBucket"}:
            return False
        raise


async def object_exists(object_name: str) -> bool:
    """Check whether an object exists in MinIO."""
    try:
        return await asyncio.to_thread(_object_exists_sync, object_name)
    except S3Error as exc:
        raise _storage_error("exists", exc) from exc


def _get_object_metadata_sync(object_name: str) -> ObjectMetadata | None:
    try:
        stat = minio_client.stat_object(settings.MINIO_BUCKET, object_name)
    except S3Error as exc:
        if exc.code in {"NoSuchKey", "NoSuchObject", "NoSuchBucket"}:
            return None
        raise

    return ObjectMetadata(
        size=int(stat.size),
        content_type=str(stat.content_type).strip() if stat.content_type else None,
        etag=str(stat.etag).strip() if stat.etag else None,
    )


async def get_object_metadata(object_name: str) -> ObjectMetadata | None:
    """Return authoritative object metadata, or ``None`` when it does not exist."""
    try:
        return await asyncio.to_thread(_get_object_metadata_sync, object_name)
    except S3Error as exc:
        raise _storage_error("metadata", exc) from exc


def _delete_file_sync(object_name: str):
    minio_client.remove_object(settings.MINIO_BUCKET, object_name)
    logger.info("Object deleted")


async def delete_file(object_name: str):
    """Delete file from MinIO."""
    try:
        await asyncio.to_thread(_delete_file_sync, object_name)
    except S3Error as exc:
        raise _storage_error("delete", exc) from exc


def get_file_url(object_name: str, expires_seconds: int = 3600) -> str:
    """Get presigned URL for file access."""
    try:
        expires = timedelta(seconds=expires_seconds)
        url = minio_client.presigned_get_object(
            settings.MINIO_BUCKET,
            object_name,
            expires=expires
        )

        if settings.MINIO_PUBLIC_ENDPOINT == "nginx":
            proxied_url = _to_nginx_minio_proxy_url(url)
            logger.info("Generated proxied presigned object URL")
            return proxied_url

        logger.info("Generated presigned object URL")
        return url
    except S3Error as exc:
        raise _storage_error("presign_download", exc) from exc


def _to_nginx_minio_proxy_url(url: str) -> str:
    parsed = urlparse(url)
    path_with_query = parsed.path
    if parsed.query:
        path_with_query = f"{path_with_query}?{parsed.query}"
    return f"/minio{path_with_query}"


def get_upload_url(object_name: str, expires_seconds: int = 900) -> str:
    """Get presigned URL for direct browser upload."""
    try:
        expires = timedelta(seconds=expires_seconds)
        url = minio_client.presigned_put_object(
            settings.MINIO_BUCKET,
            object_name,
            expires=expires
        )

        if settings.MINIO_PUBLIC_ENDPOINT == "nginx":
            proxied_url = _to_nginx_minio_proxy_url(url)
            logger.info("Generated proxied presigned upload URL")
            return proxied_url

        logger.info("Generated presigned upload URL")
        return url
    except S3Error as exc:
        raise _storage_error("presign_upload", exc) from exc
