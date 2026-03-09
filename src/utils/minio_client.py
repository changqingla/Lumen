"""MinIO client for object storage operations."""
from minio import Minio
from minio.error import S3Error
from io import BytesIO
from datetime import timedelta
from config.settings import settings
import logging
import urllib3

logger = logging.getLogger(__name__)


class _RoutingPoolManager(urllib3.PoolManager):
    """
    自定义 PoolManager：SDK 认为 endpoint 是 localhost:port（签名和 Host header 都用 localhost），
    但实际 TCP 连接路由到真正的 MinIO 地址（Docker 服务名）。
    
    原因：MinIO RELEASE.2023-12-20 校验 Host header，只接受 localhost/127.0.0.1/容器IP，
    不接受 Docker DNS 名（如 reader_minio）。
    """

    def __init__(self, real_host: str, real_port: int, **kwargs):
        super().__init__(**kwargs)
        self._real_host = real_host
        self._real_port = real_port

    def connection_from_host(self, host, port=None, scheme="http", pool_kwargs=None):
        """Override: 将连接目标从 localhost 替换为真实的 MinIO 地址。"""
        return super().connection_from_host(
            self._real_host, self._real_port, scheme, pool_kwargs
        )


def _build_minio_client() -> Minio:
    """构建 MinIO 客户端，处理 Docker 环境下的 Host header 校验问题。"""
    endpoint = settings.MINIO_ENDPOINT  # e.g. "reader_minio:9000"
    parts = endpoint.split(":")
    real_host = parts[0]
    real_port = int(parts[1]) if len(parts) > 1 else 9000

    # 如果 endpoint 不是 localhost/127.0.0.1/IP，需要路由
    needs_routing = real_host not in ("localhost", "127.0.0.1")

    if needs_routing:
        http_client = _RoutingPoolManager(
            real_host=real_host,
            real_port=real_port,
            num_pools=10,
            timeout=urllib3.Timeout(connect=5, read=30),
            retries=urllib3.Retry(total=3, backoff_factor=0.2),
        )
        # SDK 用 localhost 作为 endpoint（签名 + Host header），实际连接走 real_host
        return Minio(
            f"localhost:{real_port}",
            access_key=settings.MINIO_ACCESS_KEY,
            secret_key=settings.MINIO_SECRET_KEY,
            secure=settings.MINIO_SECURE,
            http_client=http_client,
        )
    else:
        return Minio(
            endpoint,
            access_key=settings.MINIO_ACCESS_KEY,
            secret_key=settings.MINIO_SECRET_KEY,
            secure=settings.MINIO_SECURE,
        )


# Initialize MinIO client
minio_client = _build_minio_client()


def ensure_bucket_exists():
    """Ensure the default bucket exists."""
    try:
        if not minio_client.bucket_exists(settings.MINIO_BUCKET):
            minio_client.make_bucket(settings.MINIO_BUCKET)
            logger.info(f"Created bucket: {settings.MINIO_BUCKET}")
    except S3Error as e:
        logger.error(f"Error ensuring bucket exists: {e}")
        raise


async def upload_file(object_name: str, file_data: bytes, content_type: str = "application/octet-stream") -> str:
    """Upload file to MinIO."""
    try:
        ensure_bucket_exists()
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
    except S3Error as e:
        logger.error(f"Error uploading file {object_name}: {e}")
        raise Exception(f"Failed to upload file: {e}")


async def download_file(object_name: str) -> bytes:
    """Download file from MinIO."""
    try:
        response = minio_client.get_object(settings.MINIO_BUCKET, object_name)
        data = response.read()
        response.close()
        response.release_conn()
        return data
    except S3Error as e:
        logger.error(f"Error downloading file {object_name}: {e}")
        raise Exception(f"Failed to download file: {e}")


async def delete_file(object_name: str):
    """Delete file from MinIO."""
    try:
        minio_client.remove_object(settings.MINIO_BUCKET, object_name)
        logger.info(f"Deleted file: {object_name}")
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
