import io
import json
import logging
import os
import zipfile
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

os.environ["DEBUG"] = "false"

from utils import mineru_service
from utils.mineru_service import (
    MINERU_UPLOAD_CHUNK_SIZE,
    MineruService,
    MineruServiceError,
)
from utils.outbound_endpoint_policy import OutboundEndpointPolicy


class _Response:
    def __init__(
        self,
        *,
        payload=None,
        content: bytes | None = None,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        include_content_length: bool = True,
    ) -> None:
        self.content = (
            json.dumps(payload).encode("utf-8") if payload is not None else content or b""
        )
        self.status_code = status_code
        self.headers = {key.lower(): value for key, value in (headers or {}).items()}
        if include_content_length:
            self.headers.setdefault("content-length", str(len(self.content)))
        self.closed = False
        self.iterated = False

    def raise_for_status(self) -> None:
        if not 200 <= self.status_code < 300:
            raise RuntimeError(
                "remote failure at https://objects.example/result.zip?X-Signature=secret"
            )

    async def aiter_raw(self, chunk_size: int | None = None):
        self.iterated = True
        chunk_size = chunk_size or len(self.content) or 1
        for offset in range(0, len(self.content), chunk_size):
            yield self.content[offset : offset + chunk_size]


class _Policy:
    def __init__(self, responses=()) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []
        self.validated_urls: list[str] = []
        self.upload_payload = b""
        self.upload_chunk_sizes: list[int] = []

    async def validate_url(self, url: str) -> str:
        self.validated_urls.append(url)
        return url

    @asynccontextmanager
    async def stream(self, client, method: str, url: str, **kwargs):
        self.calls.append({"client": client, "method": method, "url": url, **kwargs})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response

        content = kwargs.get("content")
        if method == "PUT" and content is not None:
            if isinstance(content, bytes):
                chunks = [content]
            else:
                chunks = [chunk async for chunk in content]
            self.upload_chunk_sizes.extend(len(chunk) for chunk in chunks)
            self.upload_payload = b"".join(chunks)

        try:
            yield response
        finally:
            response.closed = True


def _settings(**overrides):
    values = {
        "MINERU_API_TOKEN": "api-token",
        "MINERU_MODEL_VERSION": "pipeline",
        "MINERU_API_BASE_URL": "https://mineru.example.test/api/v4",
        "MINERU_DNS_TIMEOUT_SECONDS": 3.0,
        "MINERU_MAX_ZIP_DOWNLOAD_BYTES": 1024 * 1024,
        "MINERU_MAX_ZIP_MEMBER_COUNT": 128,
        "MINERU_MAX_ZIP_MEMBER_UNCOMPRESSED_BYTES": 1024 * 1024,
        "MINERU_MAX_ZIP_TOTAL_UNCOMPRESSED_BYTES": 2 * 1024 * 1024,
        "HTTP_UPLOAD_TIMEOUT": 30.0,
        "HTTP_DOWNLOAD_TIMEOUT": 30.0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _configure_mineru(
    monkeypatch,
    *,
    api_responses=(),
    signed_responses=(),
    **setting_overrides,
):
    api_policy = _Policy(api_responses)
    signed_policy = _Policy(signed_responses)
    client = object()
    monkeypatch.setattr(mineru_service, "settings", _settings(**setting_overrides))
    monkeypatch.setattr(mineru_service, "get_http_client", lambda: client)
    monkeypatch.setattr(
        mineru_service,
        "_new_mineru_policy",
        lambda *, allow_query: signed_policy if allow_query else api_policy,
    )
    return api_policy, signed_policy, client


def _upload_api_response(url: str = "https://upload.example.test/source?sig=abc"):
    return _Response(
        payload={
            "code": 0,
            "data": {"batch_id": "batch-1", "file_urls": [url]},
        }
    )


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return target.getvalue()


@pytest.mark.asyncio
async def test_convert_document_from_path_streams_bounded_chunks(
    monkeypatch,
    tmp_path,
):
    payload = b"a" * (MINERU_UPLOAD_CHUNK_SIZE * 2 + 17)
    source_path = tmp_path / "report.pdf"
    source_path.write_bytes(payload)
    api_response = _upload_api_response()
    upload_response = _Response()
    api_policy, signed_policy, client = _configure_mineru(
        monkeypatch,
        api_responses=[api_response],
        signed_responses=[upload_response],
    )

    result = await MineruService.convert_document_from_path(source_path, "report.pdf")

    assert result == {"batch_id": "batch-1", "task_id": "batch-1"}
    assert signed_policy.upload_payload == payload
    assert signed_policy.upload_chunk_sizes == [
        MINERU_UPLOAD_CHUNK_SIZE,
        MINERU_UPLOAD_CHUNK_SIZE,
        17,
    ]
    assert signed_policy.calls[0]["url"].endswith("?sig=abc")
    assert signed_policy.calls[0]["headers"] == {
        "Accept-Encoding": "identity",
        "Content-Length": str(len(payload)),
    }
    assert api_policy.calls[0]["url"] == (
        "https://mineru.example.test/api/v4/file-urls/batch"
    )
    assert api_policy.calls[0]["headers"]["Authorization"] == "Bearer api-token"
    assert api_policy.calls[0]["client"] is client
    assert api_response.closed is True
    assert upload_response.closed is True


@pytest.mark.asyncio
async def test_convert_document_keeps_bytes_callers_compatible(monkeypatch):
    _api_policy, signed_policy, _client = _configure_mineru(
        monkeypatch,
        api_responses=[_upload_api_response()],
        signed_responses=[_Response()],
    )

    result = await MineruService.convert_document(b"paper", "paper.pdf")

    assert result["batch_id"] == "batch-1"
    assert signed_policy.upload_payload == b"paper"
    assert signed_policy.upload_chunk_sizes == [5]


@pytest.mark.asyncio
async def test_conversion_closes_async_upload_stream_after_failure(monkeypatch):
    class _ClosableStream:
        closed = False

        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

        async def aclose(self):
            self.closed = True

    _configure_mineru(
        monkeypatch,
        api_responses=[_upload_api_response()],
        signed_responses=[RuntimeError("upload failed")],
    )
    stream = _ClosableStream()

    with pytest.raises(RuntimeError, match="upload failed"):
        await MineruService._create_conversion(
            stream,
            "paper.pdf",
            content_length=5,
        )

    assert stream.closed is True


def test_mineru_policy_is_https_only_and_query_is_explicit(monkeypatch):
    calls = []

    class _ConstructedPolicy:
        def __init__(self, **kwargs):
            calls.append(kwargs)

    monkeypatch.setattr(mineru_service, "settings", _settings())
    monkeypatch.setattr(mineru_service, "OutboundEndpointPolicy", _ConstructedPolicy)

    mineru_service._new_mineru_policy(allow_query=False)
    mineru_service._new_mineru_policy(allow_query=True)

    assert calls == [
        {"allow_query": False, "require_https": True, "dns_timeout_seconds": 3.0},
        {"allow_query": True, "require_https": True, "dns_timeout_seconds": 3.0},
    ]


@pytest.mark.asyncio
async def test_private_mineru_api_base_url_is_rejected_before_connect(monkeypatch):
    async def resolve_private(_host: str, _port: int):
        return ("127.0.0.1",)

    settings = _settings(MINERU_API_BASE_URL="https://api.internal.example/v4")
    monkeypatch.setattr(mineru_service, "settings", settings)
    monkeypatch.setattr(mineru_service, "get_http_client", lambda: object())
    monkeypatch.setattr(
        mineru_service,
        "_new_mineru_policy",
        lambda *, allow_query: OutboundEndpointPolicy(
            allow_query=allow_query,
            require_https=True,
            resolver=resolve_private,
        ),
    )

    with pytest.raises(MineruServiceError, match="API request failed"):
        await MineruService.convert_document(b"paper", "paper.pdf")


@pytest.mark.asyncio
async def test_private_presigned_upload_url_is_rejected_before_streaming(monkeypatch):
    async def resolve_private(_host: str, _port: int):
        return ("10.20.30.40",)

    api_policy = _Policy(
        [_upload_api_response("https://upload.internal.example/source?sig=secret")]
    )
    signed_policy = OutboundEndpointPolicy(
        allow_query=True,
        require_https=True,
        resolver=resolve_private,
    )
    monkeypatch.setattr(mineru_service, "settings", _settings())
    monkeypatch.setattr(mineru_service, "get_http_client", lambda: object())
    monkeypatch.setattr(
        mineru_service,
        "_new_mineru_policy",
        lambda *, allow_query: signed_policy if allow_query else api_policy,
    )

    with pytest.raises(MineruServiceError, match="upload request failed"):
        await MineruService.convert_document(b"paper", "paper.pdf")

    assert api_policy.responses == []


@pytest.mark.asyncio
async def test_task_status_encodes_batch_id_and_validates_download_url(monkeypatch):
    full_zip_url = "https://objects.example.test/result/?X-Signature=a%2Fb&part=1"
    api_policy, signed_policy, _client = _configure_mineru(
        monkeypatch,
        api_responses=[
            _Response(
                payload={
                    "code": 0,
                    "data": {
                        "extract_result": [
                            {"state": "done", "full_zip_url": full_zip_url}
                        ]
                    },
                }
            )
        ],
    )

    status = await MineruService.get_task_status("batch/id?part=1")

    assert status == {
        "status": "completed",
        "state": "done",
        "full_zip_url": full_zip_url,
    }
    assert api_policy.calls[0]["url"].endswith(
        "/extract-results/batch/batch%2Fid%3Fpart%3D1"
    )
    assert signed_policy.validated_urls == [full_zip_url]


@pytest.mark.asyncio
async def test_private_download_url_from_status_is_rejected(monkeypatch):
    async def resolve_private(_host: str, _port: int):
        return ("169.254.169.254",)

    api_policy = _Policy(
        [
            _Response(
                payload={
                    "code": 0,
                    "data": {
                        "extract_result": [
                            {
                                "state": "done",
                                "full_zip_url": "https://metadata.internal/latest",
                            }
                        ]
                    },
                }
            )
        ]
    )
    signed_policy = OutboundEndpointPolicy(
        allow_query=True,
        require_https=True,
        resolver=resolve_private,
    )
    monkeypatch.setattr(mineru_service, "settings", _settings())
    monkeypatch.setattr(mineru_service, "get_http_client", lambda: object())
    monkeypatch.setattr(
        mineru_service,
        "_new_mineru_policy",
        lambda *, allow_query: signed_policy if allow_query else api_policy,
    )

    with pytest.raises(MineruServiceError, match="unsafe download URL"):
        await MineruService.get_task_status("batch-1")


@pytest.mark.asyncio
async def test_download_streams_archive_and_preserves_markdown_assets(monkeypatch):
    zip_url = "https://objects.example.test/result/?X-Signature=a%2Fb&part=1"
    archive = _zip_bytes(
        {
            "paper/full.md": b"# Paper\n\n![chart](images/chart.png)",
            "paper/images/chart.png": b"png-bytes",
            "ignored.txt": b"ignored",
        }
    )
    response = _Response(content=archive, include_content_length=False)
    _api_policy, signed_policy, _client = _configure_mineru(
        monkeypatch,
        signed_responses=[response],
    )
    monkeypatch.setattr(
        MineruService,
        "get_task_status",
        AsyncMock(
            return_value={"status": "completed", "full_zip_url": zip_url}
        ),
    )

    result = await MineruService.get_content_with_assets("batch-1")

    assert result.markdown.startswith("# Paper")
    assert result.assets == {"images/chart.png": b"png-bytes"}
    assert result.markdown_path == "paper/full.md"
    assert signed_policy.calls[0]["url"] == zip_url
    assert signed_policy.calls[0]["headers"] == {"Accept-Encoding": "identity"}
    assert response.iterated is True
    assert response.closed is True


@pytest.mark.asyncio
async def test_download_rejects_content_length_over_limit_and_closes_response(
    monkeypatch,
):
    response = _Response(content=b"small", headers={"Content-Length": "101"})
    _api_policy, _signed_policy, _client = _configure_mineru(
        monkeypatch,
        signed_responses=[response],
        MINERU_MAX_ZIP_DOWNLOAD_BYTES=100,
    )
    monkeypatch.setattr(
        MineruService,
        "get_task_status",
        AsyncMock(
            return_value={
                "status": "completed",
                "full_zip_url": "https://objects.example.test/result.zip?sig=secret",
            }
        ),
    )

    with pytest.raises(MineruServiceError, match="byte limit"):
        await MineruService.get_content_with_assets("batch-1")

    assert response.iterated is False
    assert response.closed is True


@pytest.mark.asyncio
async def test_download_rejects_chunked_body_over_limit(monkeypatch):
    response = _Response(content=b"x" * 101, include_content_length=False)
    _api_policy, _signed_policy, _client = _configure_mineru(
        monkeypatch,
        signed_responses=[response],
        MINERU_MAX_ZIP_DOWNLOAD_BYTES=100,
    )
    monkeypatch.setattr(
        MineruService,
        "get_task_status",
        AsyncMock(
            return_value={
                "status": "completed",
                "full_zip_url": "https://objects.example.test/result.zip?sig=secret",
            }
        ),
    )

    with pytest.raises(MineruServiceError, match="byte limit"):
        await MineruService.get_content_with_assets("batch-1")

    assert response.closed is True


@pytest.mark.asyncio
async def test_download_rejects_http_content_encoding(monkeypatch):
    response = _Response(content=b"gzip", headers={"Content-Encoding": "gzip"})
    _configure_mineru(monkeypatch, signed_responses=[response])
    monkeypatch.setattr(
        MineruService,
        "get_task_status",
        AsyncMock(
            return_value={
                "status": "completed",
                "full_zip_url": "https://objects.example.test/result.zip?sig=secret",
            }
        ),
    )

    with pytest.raises(MineruServiceError, match="encoded response"):
        await MineruService.get_content_with_assets("batch-1")

    assert response.closed is True


@pytest.mark.asyncio
async def test_failure_logs_do_not_contain_signed_url_token_or_remote_body(
    monkeypatch,
    caplog,
):
    signed_url = "https://objects.example.test/result.zip?X-Signature=secret-query"
    response = _Response(content=b"remote-secret-body", status_code=403)
    _configure_mineru(
        monkeypatch,
        signed_responses=[response],
        MINERU_API_TOKEN="top-secret-api-token",
    )
    monkeypatch.setattr(
        MineruService,
        "get_task_status",
        AsyncMock(
            return_value={"status": "completed", "full_zip_url": signed_url}
        ),
    )

    with caplog.at_level(logging.INFO), pytest.raises(
        MineruServiceError, match="HTTP 403"
    ) as exc_info:
        await MineruService.get_content_with_assets("batch-1")

    combined = caplog.text + str(exc_info.value)
    assert signed_url not in combined
    assert "secret-query" not in combined
    assert "top-secret-api-token" not in combined
    assert "remote-secret-body" not in combined


def test_zip_rejects_member_count_limit(monkeypatch):
    archive = _zip_bytes({"paper.md": b"markdown", "image.png": b"image"})
    monkeypatch.setattr(
        mineru_service,
        "settings",
        _settings(MINERU_MAX_ZIP_MEMBER_COUNT=1),
    )

    with pytest.raises(MineruServiceError, match="too many members"):
        MineruService._extract_markdown_result_from_zip_file(io.BytesIO(archive))


def test_zip_rejects_single_member_uncompressed_limit(monkeypatch):
    archive = _zip_bytes({"paper.md": b"markdown"})
    monkeypatch.setattr(
        mineru_service,
        "settings",
        _settings(MINERU_MAX_ZIP_MEMBER_UNCOMPRESSED_BYTES=7),
    )

    with pytest.raises(MineruServiceError, match="member exceeds"):
        MineruService._extract_markdown_result_from_zip_file(io.BytesIO(archive))


def test_zip_rejects_total_uncompressed_limit(monkeypatch):
    archive = _zip_bytes({"paper.md": b"12345", "image.png": b"67890"})
    monkeypatch.setattr(
        mineru_service,
        "settings",
        _settings(MINERU_MAX_ZIP_TOTAL_UNCOMPRESSED_BYTES=9),
    )

    with pytest.raises(MineruServiceError, match="total uncompressed"):
        MineruService._extract_markdown_result_from_zip_file(io.BytesIO(archive))
