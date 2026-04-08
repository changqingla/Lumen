"""MinIO client for object storage operations."""
import asyncio
import logging
from collections.abc import Iterator
from datetime import timedelta
from io import BytesIO
from urllib.parse import urlparse

from minio import Minio
from minio.error import S3Error
from config.settings import settings

logger = logging.getLogger(__name__)


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
        logger.info(f"Created bucket: {settings.MINIO_BUCKET}")


async def ensure_bucket_exists():
    """Ensure the default bucket exists."""
    try:
        await asyncio.to_thread(_ensure_bucket_exists_sync)
    except S3Error as e:
        logger.error(f"Error ensuring bucket exists: {e}")
        raise


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
    logger.info(f"Uploaded file: {object_name} ({file_size} bytes)")
    return f"{settings.MINIO_BUCKET}/{object_name}"


async def upload_file(object_name: str, file_data: bytes, content_type: str = "application/octet-stream") -> str:
    """Upload file to MinIO."""
    try:
        await ensure_bucket_exists()
        return await asyncio.to_thread(_upload_file_sync, object_name, file_data, content_type)
    except S3Error as e:
        logger.error(f"Error uploading file {object_name}: {e}")
        raise Exception(f"Failed to upload file: {e}")


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
    except S3Error as e:
        logger.error(f"Error downloading file {object_name}: {e}")
        raise Exception(f"Failed to download file: {e}")


def stream_file(object_name: str, chunk_size: int = 64 * 1024) -> Iterator[bytes]:
    """Stream a file from MinIO in chunks."""

    def iterator() -> Iterator[bytes]:
        try:
            response = minio_client.get_object(settings.MINIO_BUCKET, object_name)
        except S3Error as e:
            logger.error(f"Error opening file stream {object_name}: {e}")
            raise Exception(f"Failed to download file: {e}") from e

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
    except S3Error as e:
        logger.error(f"Error checking file {object_name}: {e}")
        raise Exception(f"Failed to check file: {e}")


def _delete_file_sync(object_name: str):
    minio_client.remove_object(settings.MINIO_BUCKET, object_name)
    logger.info(f"Deleted file: {object_name}")


async def delete_file(object_name: str):
    """Delete file from MinIO."""
    try:
        await asyncio.to_thread(_delete_file_sync, object_name)
    except S3Error as e:
        logger.error(f"Error deleting file {object_name}: {e}")
        raise Exception(f"Failed to delete file: {e}")


def get_file_url(object_name: str, expires_seconds: int = 3600) -> str:
    """Get presigned URL for file access."""
    try:
        if settings.MINIO_PUBLIC_ENDPOINT == "nginx":
            path_without_signature = f"/minio/{settings.MINIO_BUCKET}/{object_name}"
            logger.info(f"Generated Nginx proxy URL (no signature) for {object_name}")
            return path_without_signature

        expires = timedelta(seconds=expires_seconds)
        url = minio_client.presigned_get_object(
            settings.MINIO_BUCKET,
            object_name,
            expires=expires
        )
        logger.info(f"Generated presigned URL for {object_name}")
        return url
    except S3Error as e:
        logger.error(f"Error generating presigned URL for {object_name}: {e}")
        raise Exception(f"Failed to generate URL: {e}")


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
            parsed = urlparse(url)
            path_with_query = parsed.path
            if parsed.query:
                path_with_query = f"{path_with_query}?{parsed.query}"
            proxied_url = f"/minio{path_with_query}"
            logger.info(f"Generated Nginx proxied upload URL for {object_name}")
            return proxied_url

        logger.info(f"Generated presigned upload URL for {object_name}")
        return url
    except S3Error as e:
        logger.error(f"Error generating upload URL for {object_name}: {e}")
        raise Exception(f"Failed to generate upload URL: {e}")
