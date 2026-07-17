"""Bounded, SSRF-resistant client for the external MinerU API."""

import asyncio
import json
import logging
import os
import tempfile
import zipfile
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Dict
from urllib.parse import quote

import httpx

from config.settings import settings
from utils.http_client import get_http_client
from utils.outbound_endpoint_policy import (
    OutboundEndpointError,
    OutboundEndpointPolicy,
)

logger = logging.getLogger(__name__)

MINERU_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
MINERU_UPLOAD_CHUNK_SIZE = 64 * 1024
MINERU_DOWNLOAD_CHUNK_SIZE = 64 * 1024
MINERU_ZIP_READ_CHUNK_SIZE = 64 * 1024
MINERU_MAX_API_RESPONSE_BYTES = 1024 * 1024
MINERU_ARCHIVE_SPOOL_MEMORY_BYTES = 8 * 1024 * 1024


class MineruServiceError(RuntimeError):
    """Raised for a safely summarized MinerU boundary failure."""


@dataclass(frozen=True)
class MineruMarkdownResult:
    """Markdown and companion assets extracted from a MinerU result archive."""

    markdown: str
    assets: dict[str, bytes]
    markdown_path: str


def _new_mineru_policy(*, allow_query: bool) -> OutboundEndpointPolicy:
    """Build the strict policy used by MinerU API and signed-URL requests."""

    return OutboundEndpointPolicy(
        allow_query=allow_query,
        require_https=True,
        dns_timeout_seconds=settings.MINERU_DNS_TIMEOUT_SECONDS,
    )


class MineruService:
    """Client for the MinerU API and its untrusted object-storage URLs."""

    @staticmethod
    def _get_headers() -> Dict[str, str]:
        """Return API headers without ever placing the token in a URL."""

        return {
            "Accept-Encoding": "identity",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {settings.MINERU_API_TOKEN}",
        }

    @staticmethod
    async def convert_document(file_data: bytes, filename: str) -> Dict[str, Any]:
        """Convert an in-memory document, retained for existing callers."""

        return await MineruService._create_conversion(
            file_data,
            filename,
            content_length=len(file_data),
        )

    @staticmethod
    async def convert_document_from_path(
        file_path: str | os.PathLike[str],
        filename: str,
    ) -> Dict[str, Any]:
        """Convert a local document while reading it in bounded chunks."""

        path = Path(file_path)
        content_length = await asyncio.to_thread(lambda: path.stat().st_size)
        return await MineruService._create_conversion(
            MineruService._iter_file_chunks(path),
            filename,
            content_length=content_length,
        )

    @staticmethod
    async def _iter_file_chunks(
        file_path: Path,
        chunk_size: int = MINERU_UPLOAD_CHUNK_SIZE,
    ) -> AsyncIterator[bytes]:
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")

        with file_path.open("rb") as source:
            while True:
                chunk = await asyncio.to_thread(source.read, chunk_size)
                if not chunk:
                    break
                yield chunk

    @staticmethod
    async def _create_conversion(
        content: bytes | AsyncIterator[bytes],
        filename: str,
        *,
        content_length: int,
    ) -> Dict[str, Any]:
        """Create a MinerU batch and stream its source payload."""

        try:
            logger.info("[MinerU] Requesting an upload URL")
            result = await MineruService._request_api_json(
                "POST",
                "/file-urls/batch",
                json={
                    "files": [{"name": filename}],
                    "model_version": settings.MINERU_MODEL_VERSION,
                },
            )

            if result.get("code") != 0:
                raise MineruServiceError("MinerU rejected the upload request")
            data = result.get("data")
            if not isinstance(data, dict):
                raise MineruServiceError("MinerU returned an invalid upload response")

            batch_id = MineruService._normalize_batch_id(data.get("batch_id"))
            file_urls = data.get("file_urls")
            if not isinstance(file_urls, list) or not file_urls:
                raise MineruServiceError("MinerU returned no upload URL")
            upload_url = file_urls[0]
            if not isinstance(upload_url, str):
                raise MineruServiceError("MinerU returned an invalid upload URL")

            logger.info("[MinerU] Uploading source document")
            policy = _new_mineru_policy(allow_query=True)
            try:
                async with policy.stream(
                    get_http_client(),
                    "PUT",
                    upload_url,
                    content=content,
                    headers={
                        "Accept-Encoding": "identity",
                        "Content-Length": str(content_length),
                    },
                    timeout=settings.HTTP_UPLOAD_TIMEOUT,
                ) as response:
                    MineruService._raise_for_status(response, "upload")
            except (httpx.HTTPError, OutboundEndpointError):
                raise MineruServiceError("MinerU upload request failed") from None

            logger.info("[MinerU] Source document uploaded")
            return {"batch_id": batch_id, "task_id": batch_id}
        finally:
            close_content = getattr(content, "aclose", None)
            if close_content is not None:
                try:
                    await close_content()
                except Exception:
                    logger.warning("[MinerU] Failed to close upload stream")

    @staticmethod
    async def get_task_status(batch_id: str) -> Dict[str, Any]:
        """Get MinerU task status through the guarded official API boundary."""

        normalized_batch_id = MineruService._normalize_batch_id(batch_id)
        result = await MineruService._request_api_json(
            "GET",
            f"/extract-results/batch/{quote(normalized_batch_id, safe='')}",
        )

        if result.get("code") != 0:
            raise MineruServiceError("MinerU rejected the task-status request")
        data = result.get("data")
        if not isinstance(data, dict):
            raise MineruServiceError("MinerU returned an invalid task-status response")
        extract_results = data.get("extract_result", [])
        if not isinstance(extract_results, list):
            raise MineruServiceError("MinerU returned an invalid task-status response")

        if not extract_results:
            return {
                "status": "pending",
                "message": "Waiting for file processing",
            }

        file_result = extract_results[0]
        if not isinstance(file_result, dict):
            raise MineruServiceError("MinerU returned an invalid task result")
        state = str(file_result.get("state") or "pending")
        status_map = {
            "pending": "pending",
            "waiting-file": "pending",
            "running": "running",
            "converting": "running",
            "done": "completed",
            "failed": "failed",
        }
        normalized_status = status_map.get(state, "pending")
        response_data: Dict[str, Any] = {
            "status": normalized_status,
            "state": state,
        }

        if normalized_status == "completed":
            zip_url = file_result.get("full_zip_url")
            if zip_url is not None:
                if not isinstance(zip_url, str):
                    raise MineruServiceError("MinerU returned an invalid download URL")
                try:
                    zip_url = await _new_mineru_policy(
                        allow_query=True
                    ).validate_url(zip_url)
                except OutboundEndpointError:
                    raise MineruServiceError(
                        "MinerU returned an unsafe download URL"
                    ) from None
            response_data["full_zip_url"] = zip_url
        elif normalized_status == "failed":
            response_data["message"] = "MinerU task failed"
        elif normalized_status == "running":
            progress = file_result.get("extract_progress", {})
            if not isinstance(progress, dict):
                progress = {}
            response_data["progress"] = {
                "extracted_pages": progress.get("extracted_pages", 0),
                "total_pages": progress.get("total_pages", 0),
                "start_time": progress.get("start_time"),
            }

        return response_data

    @staticmethod
    async def get_content(batch_id: str) -> str:
        """Download and extract markdown content from a MinerU result."""

        result = await MineruService.get_content_with_assets(batch_id)
        return result.markdown

    @staticmethod
    async def get_content_with_assets(batch_id: str) -> MineruMarkdownResult:
        """Download bounded ZIP content and extract Markdown plus image assets."""

        status = await MineruService.get_task_status(batch_id)
        if status["status"] != "completed":
            raise MineruServiceError(
                f"MinerU task is not completed (status={status['status']})"
            )

        zip_url = status.get("full_zip_url")
        if not isinstance(zip_url, str) or not zip_url:
            raise MineruServiceError("MinerU returned no download URL")

        max_download_bytes = int(settings.MINERU_MAX_ZIP_DOWNLOAD_BYTES)
        logger.info("[MinerU] Downloading result archive")
        policy = _new_mineru_policy(allow_query=True)
        try:
            with tempfile.SpooledTemporaryFile(
                max_size=MINERU_ARCHIVE_SPOOL_MEMORY_BYTES,
                mode="w+b",
            ) as archive:
                async with policy.stream(
                    get_http_client(),
                    "GET",
                    zip_url,
                    headers={"Accept-Encoding": "identity"},
                    timeout=settings.HTTP_DOWNLOAD_TIMEOUT,
                ) as response:
                    MineruService._raise_for_status(response, "download")
                    downloaded_bytes = await MineruService._copy_bounded_response(
                        response,
                        archive,
                        max_bytes=max_download_bytes,
                    )

                archive.seek(0)
                result = await asyncio.to_thread(
                    MineruService._extract_markdown_result_from_zip_file,
                    archive,
                )
        except (httpx.HTTPError, OutboundEndpointError):
            raise MineruServiceError("MinerU download request failed") from None

        logger.info(
            "[MinerU] Extracted %s archive bytes into %s Markdown chars and %s asset(s)",
            downloaded_bytes,
            len(result.markdown),
            len(result.assets),
        )
        return result

    @staticmethod
    async def _request_api_json(
        method: str,
        path: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        base_url = str(settings.MINERU_API_BASE_URL or "").rstrip("/")
        url = f"{base_url}/{path.lstrip('/')}"
        policy = _new_mineru_policy(allow_query=False)
        request_headers = dict(MineruService._get_headers())
        request_headers.update(kwargs.pop("headers", {}))

        try:
            with tempfile.SpooledTemporaryFile(
                max_size=MINERU_MAX_API_RESPONSE_BYTES,
                mode="w+b",
            ) as payload_file:
                async with policy.stream(
                    get_http_client(),
                    method,
                    url,
                    headers=request_headers,
                    **kwargs,
                ) as response:
                    MineruService._raise_for_status(response, "API")
                    await MineruService._copy_bounded_response(
                        response,
                        payload_file,
                        max_bytes=MINERU_MAX_API_RESPONSE_BYTES,
                    )
                payload_file.seek(0)
                payload = payload_file.read()
        except (httpx.HTTPError, OutboundEndpointError):
            raise MineruServiceError("MinerU API request failed") from None

        try:
            result = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise MineruServiceError("MinerU returned invalid JSON") from None
        if not isinstance(result, dict):
            raise MineruServiceError("MinerU returned an invalid API response")
        return result

    @staticmethod
    async def _copy_bounded_response(
        response: Any,
        destination: BinaryIO,
        *,
        max_bytes: int,
    ) -> int:
        if max_bytes <= 0:
            raise MineruServiceError("MinerU response limit is invalid")

        headers = getattr(response, "headers", {})
        content_encoding = str(headers.get("content-encoding", "")).strip().lower()
        if content_encoding not in {"", "identity"}:
            raise MineruServiceError("MinerU returned an encoded response")

        raw_content_length = headers.get("content-length")
        if raw_content_length is not None:
            try:
                content_length = int(raw_content_length)
            except (TypeError, ValueError):
                raise MineruServiceError(
                    "MinerU returned an invalid Content-Length"
                ) from None
            if content_length < 0 or content_length > max_bytes:
                raise MineruServiceError("MinerU response exceeds the byte limit")

        total = 0
        async for chunk in response.aiter_raw(MINERU_DOWNLOAD_CHUNK_SIZE):
            if not chunk:
                continue
            if len(chunk) > max_bytes - total:
                raise MineruServiceError("MinerU response exceeds the byte limit")
            destination.write(chunk)
            total += len(chunk)
        return total

    @staticmethod
    def _raise_for_status(response: Any, operation: str) -> None:
        try:
            response.raise_for_status()
        except Exception:
            status_code = getattr(response, "status_code", "unknown")
            raise MineruServiceError(
                f"MinerU {operation} failed with HTTP {status_code}"
            ) from None

    @staticmethod
    def _normalize_batch_id(value: Any) -> str:
        normalized = str(value or "").strip()
        if (
            not normalized
            or len(normalized) > 256
            or any(ord(character) < 32 or ord(character) == 127 for character in normalized)
        ):
            raise MineruServiceError("MinerU returned an invalid batch ID")
        return normalized

    @staticmethod
    def _extract_markdown_result_from_zip_file(
        zip_source: BinaryIO,
    ) -> MineruMarkdownResult:
        """Extract selected files after validating every ZIP member size."""

        max_members = int(settings.MINERU_MAX_ZIP_MEMBER_COUNT)
        max_member_bytes = int(settings.MINERU_MAX_ZIP_MEMBER_UNCOMPRESSED_BYTES)
        max_total_bytes = int(settings.MINERU_MAX_ZIP_TOTAL_UNCOMPRESSED_BYTES)
        if min(max_members, max_member_bytes, max_total_bytes) <= 0:
            raise MineruServiceError("MinerU ZIP limits are invalid")

        try:
            with zipfile.ZipFile(zip_source, "r") as zf:
                members = zf.infolist()
                if len(members) > max_members:
                    raise MineruServiceError("MinerU ZIP contains too many members")

                declared_total = 0
                for member in members:
                    if member.file_size < 0 or member.file_size > max_member_bytes:
                        raise MineruServiceError(
                            "MinerU ZIP member exceeds the uncompressed byte limit"
                        )
                    declared_total += member.file_size
                    if declared_total > max_total_bytes:
                        raise MineruServiceError(
                            "MinerU ZIP exceeds the total uncompressed byte limit"
                        )

                md_files = [
                    member
                    for member in members
                    if not member.is_dir() and member.filename.endswith(".md")
                ]
                if not md_files:
                    md_files = [
                        member
                        for member in members
                        if not member.is_dir() and ".md" in member.filename.lower()
                    ]
                if not md_files:
                    raise MineruServiceError("MinerU ZIP contains no Markdown file")

                md_member = sorted(md_files, key=lambda item: len(item.filename))[0]
                markdown_bytes = MineruService._read_zip_member_bounded(
                    zf,
                    md_member,
                    max_bytes=max_member_bytes,
                )
                try:
                    markdown = markdown_bytes.decode("utf-8")
                except UnicodeDecodeError:
                    raise MineruServiceError(
                        "MinerU Markdown is not valid UTF-8"
                    ) from None

                md_parent = PurePosixPath(md_member.filename).parent
                assets: dict[str, bytes] = {}
                for member in members:
                    if member.is_dir():
                        continue
                    path = PurePosixPath(member.filename)
                    if any(part in {"", ".", ".."} for part in path.parts):
                        continue
                    if path.suffix.lower() not in MINERU_IMAGE_EXTENSIONS:
                        continue
                    try:
                        relative_path = path.relative_to(md_parent)
                    except ValueError:
                        relative_path = PurePosixPath(path.name)
                    normalized = str(relative_path).lstrip("/")
                    if (
                        not normalized
                        or normalized.startswith("../")
                        or "/../" in normalized
                    ):
                        continue
                    assets[normalized] = MineruService._read_zip_member_bounded(
                        zf,
                        member,
                        max_bytes=max_member_bytes,
                    )

                return MineruMarkdownResult(
                    markdown=markdown,
                    assets=assets,
                    markdown_path=md_member.filename,
                )
        except zipfile.BadZipFile:
            raise MineruServiceError("MinerU returned an invalid ZIP archive") from None
        except MineruServiceError:
            raise
        except (OSError, RuntimeError, NotImplementedError):
            raise MineruServiceError("Failed to read the MinerU ZIP archive") from None

    @staticmethod
    def _read_zip_member_bounded(
        archive: zipfile.ZipFile,
        member: zipfile.ZipInfo,
        *,
        max_bytes: int,
    ) -> bytes:
        content = bytearray()
        with archive.open(member, "r") as source:
            while True:
                remaining = max_bytes - len(content)
                chunk = source.read(min(MINERU_ZIP_READ_CHUNK_SIZE, remaining + 1))
                if not chunk:
                    break
                content.extend(chunk)
                if len(content) > max_bytes:
                    raise MineruServiceError(
                        "MinerU ZIP member exceeds the uncompressed byte limit"
                    )
        if len(content) != member.file_size:
            raise MineruServiceError("MinerU ZIP member size is inconsistent")
        return bytes(content)
