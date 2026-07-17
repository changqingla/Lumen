import asyncio
import logging
import mimetypes
import os
import zipfile
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse, Response, StreamingResponse

from src.config.paths import get_paths
from src.utils.thread_files import (
    ResolvedThreadFile,
    ThreadFileAccessError,
    ThreadFileChangedError,
    ThreadFileNotFoundError,
    ThreadFileNotRegularError,
    ThreadFileTooLargeError,
    open_thread_file,
    resolve_thread_file,
    snapshot_thread_file_async,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["artifacts"])

_STREAM_CHUNK_SIZE = 64 * 1024
_SKILL_ARCHIVE_SOURCE_MAX_BYTES = 100 * 1024 * 1024
_SKILL_ARCHIVE_MEMBER_MAX_BYTES = 10 * 1024 * 1024


class _SkillArchiveMemberTooLarge(ValueError):
    """Raised when an archive preview member exceeds its response limit."""


def _is_text_file_descriptor(file_fd: int, sample_size: int = 8192) -> bool:
    """Detect likely text content from an already-open descriptor snapshot."""

    try:
        chunk = os.pread(file_fd, sample_size, 0)
    except OSError:
        return False
    return b"\x00" not in chunk


def _stream_file_descriptor(file_fd: int, size: int) -> Iterator[bytes]:
    """Stream at most the size observed when the descriptor was opened."""

    remaining = size
    try:
        while remaining > 0:
            chunk = os.read(file_fd, min(_STREAM_CHUNK_SIZE, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk
    finally:
        os.close(file_fd)


def _extract_file_from_skill_archive(
    zip_path: Path,
    internal_path: str,
    *,
    max_bytes: int = _SKILL_ARCHIVE_MEMBER_MAX_BYTES,
) -> bytes | None:
    """Bounded-read one regular member from a trusted `.skill` snapshot."""

    try:
        with zipfile.ZipFile(zip_path, "r") as archive:
            selected: zipfile.ZipInfo | None = None
            for info in archive.infolist():
                if info.filename == internal_path or info.filename.endswith(
                    "/" + internal_path
                ):
                    selected = info
                    break
            if selected is None or selected.is_dir():
                return None
            if selected.file_size > max_bytes:
                raise _SkillArchiveMemberTooLarge

            with archive.open(selected, "r") as source:
                content = source.read(max_bytes + 1)
            if len(content) > max_bytes or len(content) != selected.file_size:
                raise _SkillArchiveMemberTooLarge
            return content
    except _SkillArchiveMemberTooLarge:
        raise
    except (zipfile.BadZipFile, zipfile.LargeZipFile, EOFError, KeyError):
        return None


def _resolve_artifact_file(
    thread_id: str,
    virtual_path: str,
) -> ResolvedThreadFile:
    try:
        return resolve_thread_file(get_paths(), thread_id, virtual_path)
    except ThreadFileAccessError as exc:
        raise HTTPException(status_code=403, detail="Access denied") from exc


@router.get(
    "/threads/{thread_id}/artifacts/{path:path}",
    summary="获取产物文件",
    description="读取 AI Agent 生成的产物文件，支持文本、HTML 和二进制文件。",
)
async def get_artifact(thread_id: str, path: str, request: Request) -> Response:
    """Securely stream a thread artifact or bounded-preview a `.skill` member."""

    if ".skill/" in path:
        skill_marker = ".skill/"
        marker_pos = path.find(skill_marker)
        skill_file_path = path[: marker_pos + len(".skill")]
        internal_path = path[marker_pos + len(skill_marker) :]
        if not internal_path or "\x00" in internal_path:
            raise HTTPException(status_code=400, detail="Invalid skill archive path")

        resolved = _resolve_artifact_file(thread_id, skill_file_path)
        snapshot = None
        try:
            snapshot = await snapshot_thread_file_async(
                resolved,
                max_bytes=_SKILL_ARCHIVE_SOURCE_MAX_BYTES,
                suffix=".skill",
            )
            content = await asyncio.to_thread(
                _extract_file_from_skill_archive,
                snapshot.path,
                internal_path,
            )
        except ThreadFileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Skill file not found") from exc
        except ThreadFileNotRegularError as exc:
            raise HTTPException(status_code=400, detail="Skill path is not a file") from exc
        except ThreadFileTooLargeError as exc:
            raise HTTPException(status_code=413, detail="Skill archive exceeds the size limit") from exc
        except ThreadFileChangedError as exc:
            raise HTTPException(status_code=409, detail="Skill archive changed while being read") from exc
        except ThreadFileAccessError as exc:
            raise HTTPException(status_code=403, detail="Access denied") from exc
        except _SkillArchiveMemberTooLarge as exc:
            raise HTTPException(status_code=413, detail="Archive member exceeds the preview size limit") from exc
        finally:
            if snapshot is not None:
                snapshot.cleanup()

        if content is None:
            raise HTTPException(status_code=404, detail="File not found in skill archive")

        mime_type, _ = mimetypes.guess_type(internal_path)
        cache_headers = {
            "Cache-Control": "private, max-age=300",
            "X-Content-Type-Options": "nosniff",
        }
        if mime_type and mime_type.startswith("text/"):
            try:
                decoded = content.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise HTTPException(status_code=422, detail="Archive text member is not valid UTF-8") from exc
            return PlainTextResponse(
                content=decoded,
                media_type=mime_type,
                headers=cache_headers,
            )

        try:
            decoded = content.decode("utf-8")
        except UnicodeDecodeError:
            return Response(
                content=content,
                media_type=mime_type or "application/octet-stream",
                headers=cache_headers,
            )
        return PlainTextResponse(
            content=decoded,
            media_type="text/plain",
            headers=cache_headers,
        )

    resolved = _resolve_artifact_file(thread_id, path)
    try:
        file_fd, file_stat = open_thread_file(resolved)
    except ThreadFileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Artifact not found") from exc
    except ThreadFileNotRegularError as exc:
        raise HTTPException(status_code=400, detail="Artifact path is not a file") from exc
    except ThreadFileAccessError as exc:
        raise HTTPException(status_code=403, detail="Access denied") from exc

    filename = resolved.parts[-1]
    mime_type, _ = mimetypes.guess_type(filename)
    if mime_type is None and _is_text_file_descriptor(file_fd):
        mime_type = "text/plain"

    encoded_filename = quote(filename)
    download = request.query_params.get("download")
    should_download = (
        str(download).lower() in {"1", "true", "yes", "on"}
        if download is not None
        else False
    )
    disposition = "attachment" if should_download else "inline"
    headers = {
        "Content-Disposition": (
            f"{disposition}; filename*=UTF-8''{encoded_filename}"
        ),
        "Content-Length": str(file_stat.st_size),
        "X-Content-Type-Options": "nosniff",
    }
    if mime_type == "text/html":
        headers["Content-Security-Policy"] = (
            "sandbox; default-src 'none'; img-src data:; style-src 'unsafe-inline'"
        )

    return StreamingResponse(
        _stream_file_descriptor(file_fd, file_stat.st_size),
        media_type=mime_type or "application/octet-stream",
        headers=headers,
    )
