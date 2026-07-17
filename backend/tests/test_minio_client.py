import asyncio
import logging
import os
import threading

os.environ["DEBUG"] = "false"

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from minio.error import S3Error

from utils import minio_client


def _private_s3_error() -> S3Error:
    return S3Error(
        None,
        "InternalError",
        "private-provider-detail",
        "private-resource",
        "request-id",
        "host-id",
        "private-bucket",
        "private-object-name",
    )


class _StreamingResponse:
    def __init__(self, payload: bytes):
        self.payload = payload
        self.offset = 0
        self.read_sizes: list[int] = []
        self.closed = False
        self.released = False

    def read(self, size: int) -> bytes:
        self.read_sizes.append(size)
        chunk = self.payload[self.offset : self.offset + size]
        self.offset += len(chunk)
        return chunk

    def close(self) -> None:
        self.closed = True

    def release_conn(self) -> None:
        self.released = True


def test_get_file_url_preserves_signature_when_using_nginx_proxy(monkeypatch):
    monkeypatch.setattr(
        minio_client,
        "settings",
        SimpleNamespace(
            MINIO_BUCKET="reader-uploads",
            MINIO_PUBLIC_ENDPOINT="nginx",
        ),
    )
    monkeypatch.setattr(
        minio_client.minio_client,
        "presigned_get_object",
        lambda bucket, object_name, expires: (
            f"http://minio:9000/{bucket}/{object_name}"
            "?X-Amz-Algorithm=AWS4-HMAC-SHA256"
            "&X-Amz-Expires=3600"
            "&X-Amz-Signature=abc123"
        ),
    )

    url = minio_client.get_file_url("kb/demo/report.pdf", expires_seconds=3600)

    assert url.startswith("/minio/reader-uploads/kb/demo/report.pdf?")
    assert "X-Amz-Expires=3600" in url
    assert "X-Amz-Signature=abc123" in url


def test_presign_failure_redacts_provider_and_object_details(monkeypatch, caplog):
    monkeypatch.setattr(
        minio_client,
        "settings",
        SimpleNamespace(MINIO_BUCKET="reader-uploads"),
    )
    monkeypatch.setattr(
        minio_client.minio_client,
        "presigned_get_object",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(_private_s3_error()),
    )

    with (
        caplog.at_level(logging.ERROR),
        pytest.raises(minio_client.ObjectStorageError) as exc_info,
    ):
        minio_client.get_file_url("private/user/object.pdf")

    assert str(exc_info.value) == "Object storage operation failed"
    assert exc_info.value.operation == "presign_download"
    for marker in (
        "private-provider-detail",
        "private-resource",
        "private-bucket",
        "private-object-name",
        "private/user/object.pdf",
    ):
        assert marker not in caplog.text
        assert marker not in str(exc_info.value)
    assert "S3Error" in caplog.text


def test_get_object_metadata_uses_authoritative_stat(monkeypatch):
    monkeypatch.setattr(
        minio_client,
        "settings",
        SimpleNamespace(MINIO_BUCKET="reader-uploads"),
    )
    monkeypatch.setattr(
        minio_client.minio_client,
        "stat_object",
        lambda bucket, object_name: SimpleNamespace(
            size=42,
            content_type="application/pdf",
            etag="abc123",
        ),
    )

    metadata = minio_client._get_object_metadata_sync("kb/demo/report.pdf")

    assert metadata == minio_client.ObjectMetadata(
        size=42,
        content_type="application/pdf",
        etag="abc123",
    )


@pytest.mark.asyncio
async def test_upload_file_from_path_delegates_to_minio_file_api(monkeypatch, tmp_path):
    source_path = tmp_path / "content.md"
    source_path.write_text("stream me", encoding="utf-8")
    calls = []
    ensure_bucket = AsyncMock()
    monkeypatch.setattr(
        minio_client,
        "settings",
        SimpleNamespace(MINIO_BUCKET="reader-uploads"),
    )
    monkeypatch.setattr(minio_client, "ensure_bucket_exists", ensure_bucket)
    monkeypatch.setattr(
        minio_client.minio_client,
        "fput_object",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    result = await minio_client.upload_file_from_path(
        "markdown/content.md",
        source_path,
        "text/markdown",
    )

    assert result == "reader-uploads/markdown/content.md"
    ensure_bucket.assert_awaited_once()
    assert calls == [
        (
            ("reader-uploads", "markdown/content.md", str(source_path)),
            {"content_type": "text/markdown"},
        )
    ]


@pytest.mark.asyncio
async def test_temporary_download_uses_bounded_reads_and_cleans_up(monkeypatch):
    response = _StreamingResponse(b"streamed-object")
    monkeypatch.setattr(
        minio_client,
        "settings",
        SimpleNamespace(MINIO_BUCKET="reader-uploads"),
    )
    monkeypatch.setattr(
        minio_client.minio_client,
        "get_object",
        lambda bucket, object_name: response,
    )

    async with minio_client.temporary_download(
        "kb/demo/report.pdf",
        suffix=".pdf",
        max_bytes=32,
        chunk_size=4,
    ) as temp_path:
        captured_path = temp_path
        assert temp_path.read_bytes() == b"streamed-object"
        assert temp_path.exists()

    assert not captured_path.exists()
    assert response.read_sizes
    assert set(response.read_sizes) == {4}
    assert response.closed is True
    assert response.released is True


@pytest.mark.asyncio
@pytest.mark.parametrize("raised", [RuntimeError("failed"), asyncio.CancelledError()])
async def test_temporary_download_cleans_up_when_body_does_not_complete(
    monkeypatch,
    raised,
):
    response = _StreamingResponse(b"payload")
    monkeypatch.setattr(
        minio_client,
        "settings",
        SimpleNamespace(MINIO_BUCKET="reader-uploads"),
    )
    monkeypatch.setattr(
        minio_client.minio_client,
        "get_object",
        lambda bucket, object_name: response,
    )

    with pytest.raises(type(raised)):
        async with minio_client.temporary_download("kb/demo/report.pdf") as temp_path:
            captured_path = temp_path
            raise raised

    assert not captured_path.exists()


@pytest.mark.asyncio
async def test_temporary_download_rejects_oversized_object_and_removes_partial_file(
    monkeypatch,
):
    response = _StreamingResponse(b"too-large")
    created_paths = []
    real_mkstemp = minio_client.tempfile.mkstemp

    def tracking_mkstemp(*args, **kwargs):
        fd, path = real_mkstemp(*args, **kwargs)
        created_paths.append(path)
        return fd, path

    monkeypatch.setattr(
        minio_client,
        "settings",
        SimpleNamespace(MINIO_BUCKET="reader-uploads"),
    )
    monkeypatch.setattr(
        minio_client.minio_client,
        "get_object",
        lambda bucket, object_name: response,
    )
    monkeypatch.setattr(minio_client.tempfile, "mkstemp", tracking_mkstemp)

    with pytest.raises(ValueError, match="exceeds the 5-byte limit"):
        async with minio_client.temporary_download(
            "kb/demo/report.pdf",
            max_bytes=5,
            chunk_size=4,
        ):
            pytest.fail("oversized object must not be yielded")

    assert created_paths
    assert all(not os.path.exists(path) for path in created_paths)
    assert response.read_sizes == [4, 4]
    assert response.closed is True
    assert response.released is True


@pytest.mark.asyncio
async def test_cancelled_download_cannot_recreate_cleaned_temp_path(monkeypatch):
    get_object_started = threading.Event()
    allow_get_object_to_finish = threading.Event()
    worker_finished = threading.Event()
    created_paths = []
    real_mkstemp = minio_client.tempfile.mkstemp

    def tracking_mkstemp(*args, **kwargs):
        fd, path = real_mkstemp(*args, **kwargs)
        created_paths.append(path)
        return fd, path

    def delayed_get_object(bucket, object_name):
        get_object_started.set()
        allow_get_object_to_finish.wait(timeout=5)
        return _StreamingResponse(b"payload")

    original_download = minio_client._download_file_to_path_sync

    def tracked_download(*args, **kwargs):
        try:
            return original_download(*args, **kwargs)
        finally:
            worker_finished.set()

    monkeypatch.setattr(
        minio_client,
        "settings",
        SimpleNamespace(MINIO_BUCKET="reader-uploads"),
    )
    monkeypatch.setattr(minio_client.tempfile, "mkstemp", tracking_mkstemp)
    monkeypatch.setattr(minio_client.minio_client, "get_object", delayed_get_object)
    monkeypatch.setattr(minio_client, "_download_file_to_path_sync", tracked_download)

    async def download():
        async with minio_client.temporary_download("kb/demo/report.pdf"):
            pytest.fail("cancelled download must not yield a path")

    task = asyncio.create_task(download())
    await asyncio.to_thread(get_object_started.wait, 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    allow_get_object_to_finish.set()
    await asyncio.to_thread(worker_finished.wait, 5)

    assert created_paths
    assert all(not os.path.exists(path) for path in created_paths)
