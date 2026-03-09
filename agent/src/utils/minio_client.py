"""Agent-side MinIO client for object storage operations."""

import os
from datetime import timedelta
from io import BytesIO
from typing import Optional

import urllib3
from minio import Minio
from minio.error import S3Error

from .logger import get_logger

logger = get_logger(__name__)

# Defaults
AGENT_OUTPUTS_BUCKET = "agent-outputs"
PRESIGNED_URL_EXPIRY = 86400  # 24 hours


class _RoutingPoolManager(urllib3.PoolManager):
    """Custom PoolManager: SDK thinks endpoint is localhost:port (signature and Host header use localhost),
    but actual TCP connection routes to the real MinIO address (Docker service name).

    Reason: MinIO RELEASE.2023-12-20 validates Host header, only accepts localhost/127.0.0.1/container-IP,
    not Docker DNS names (e.g. reader_minio).
    """

    def __init__(self, real_host: str, real_port: int, **kwargs):
        super().__init__(**kwargs)
        self._real_host = real_host
        self._real_port = real_port

    def connection_from_host(self, host, port=None, scheme="http", pool_kwargs=None):
        """Override: route connection target from localhost to the real MinIO address."""
        return super().connection_from_host(
            self._real_host, self._real_port, scheme, pool_kwargs
        )


class AgentMinioClient:
    """Agent-side MinIO client with Docker network Host header routing."""

    def __init__(self):
        self._endpoint = os.environ.get("MINIO_ENDPOINT", "reader_minio:9000")
        self._access_key = os.environ.get("MINIO_ACCESS_KEY", "reader")
        self._secret_key = os.environ.get("MINIO_SECRET_KEY", "reader_dev_password")
        self._bucket = os.environ.get("MINIO_BUCKET", AGENT_OUTPUTS_BUCKET)
        self._secure = os.environ.get("MINIO_SECURE", "false").lower() == "true"
        self._public_endpoint = os.environ.get("MINIO_PUBLIC_ENDPOINT", "")
        self._client = self._build_client()

    def _build_client(self) -> Minio:
        """Build MinIO client, handling Docker network Host header validation."""
        parts = self._endpoint.split(":")
        real_host = parts[0]
        real_port = int(parts[1]) if len(parts) > 1 else 9000

        needs_routing = real_host not in ("localhost", "127.0.0.1")

        if needs_routing:
            http_client = _RoutingPoolManager(
                real_host=real_host,
                real_port=real_port,
                num_pools=10,
                timeout=urllib3.Timeout(connect=5, read=30),
                retries=urllib3.Retry(total=3, backoff_factor=0.2),
            )
            return Minio(
                f"localhost:{real_port}",
                access_key=self._access_key,
                secret_key=self._secret_key,
                secure=self._secure,
                http_client=http_client,
            )
        else:
            return Minio(
                self._endpoint,
                access_key=self._access_key,
                secret_key=self._secret_key,
                secure=self._secure,
            )

    def _ensure_bucket(self) -> None:
        """Ensure the target bucket exists, create if missing."""
        try:
            if not self._client.bucket_exists(self._bucket):
                self._client.make_bucket(self._bucket)
                logger.info(f"Created bucket: {self._bucket}")
        except S3Error as e:
            logger.error(f"Error ensuring bucket exists: {e}")
            raise

    async def upload_file(
        self,
        object_name: str,
        file_data: bytes,
        content_type: str = "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ) -> str:
        """Upload file to MinIO.

        The MinIO Python SDK is synchronous, so we run the blocking I/O
        in a thread to avoid stalling the event loop.

        Args:
            object_name: Object key in the bucket.
            file_data: Raw file bytes.
            content_type: MIME type of the file.

        Returns:
            The object_name that was stored.
        """
        import asyncio

        def _sync_upload():
            self._ensure_bucket()
            file_stream = BytesIO(file_data)
            file_size = len(file_data)
            self._client.put_object(
                self._bucket,
                object_name,
                file_stream,
                file_size,
                content_type=content_type,
            )

        try:
            await asyncio.to_thread(_sync_upload)
            logger.info(f"Uploaded file: {object_name} ({len(file_data)} bytes)")
            return object_name
        except S3Error as e:
            logger.error(f"Error uploading file {object_name}: {e}")
            raise Exception(f"Failed to upload file: {e}")

    def get_presigned_url(self, object_name: str, expires_seconds: int = PRESIGNED_URL_EXPIRY) -> str:
        """Generate a presigned URL or Nginx proxy path for the object.

        If MINIO_PUBLIC_ENDPOINT is "nginx", returns an Nginx proxy path.
        Otherwise returns a standard MinIO presigned URL.
        """
        try:
            if self._public_endpoint == "nginx":
                path = f"/minio/{self._bucket}/{object_name}"
                logger.info(f"Generated Nginx proxy URL for {object_name}")
                return path

            expires = timedelta(seconds=expires_seconds)
            url = self._client.presigned_get_object(
                self._bucket,
                object_name,
                expires=expires,
            )
            logger.info(f"Generated presigned URL for {object_name}")
            return url
        except S3Error as e:
            logger.error(f"Error generating presigned URL for {object_name}: {e}")
            raise Exception(f"Failed to generate URL: {e}")


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------
_agent_minio_client: Optional[AgentMinioClient] = None


def get_agent_minio_client() -> AgentMinioClient:
    """Return the global AgentMinioClient singleton, creating it on first call."""
    global _agent_minio_client
    if _agent_minio_client is None:
        _agent_minio_client = AgentMinioClient()
    return _agent_minio_client
