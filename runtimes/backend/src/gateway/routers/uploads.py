"""处理文件上传的路由模块。"""

import asyncio
import errno
import hashlib
import logging
import os
import re
import stat
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from src.config import get_app_config
from src.config.paths import VIRTUAL_PATH_PREFIX, get_paths
from src.config.uploads_config import UploadsConfig
from src.utils.thread_files import (
    ResolvedThreadFile,
    ThreadFileError,
    ThreadFileSnapshot,
    snapshot_thread_file_async,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/threads/{thread_id}/uploads", tags=["uploads"])
_thread_upload_locks: dict[str, asyncio.Lock] = {}
_thread_upload_lock_guard = asyncio.Lock()
_thread_upload_lock_dir = Path(tempfile.gettempdir()) / "lumen-thread-locks"
_UUID_FILENAME_COMPONENT = (
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_MANAGED_KB_FILENAME_RE = re.compile(
    rf"^kb__{_UUID_FILENAME_COMPONENT}__{_UUID_FILENAME_COMPONENT}__"
    r"(?:[0-9a-f]{16}__)?[A-Za-z0-9._-]+\.md$"
)


class UploadedFileInfo(BaseModel):
    """上传文件元数据。"""

    filename: str
    size: int
    path: str
    virtual_path: str
    artifact_url: str
    extension: str | None = None
    modified: float | None = None
    markdown_file: str | None = None
    markdown_path: str | None = None
    markdown_virtual_path: str | None = None
    markdown_artifact_url: str | None = None


class UploadResponse(BaseModel):
    """文件上传响应模型。"""

    success: bool
    files: list[UploadedFileInfo]
    message: str


class ListUploadsResponse(BaseModel):
    """上传文件列表响应模型。"""

    files: list[UploadedFileInfo]
    count: int


class DeleteUploadResponse(BaseModel):
    """删除上传文件操作响应模型。"""

    success: bool
    message: str


class ManagedUploadMetadata(BaseModel):
    """受管知识文件的内容元数据。"""

    filename: str
    size: int
    sha256: str


class _UnsafeUploadTarget(Exception):
    """上传目标不是 uploads 目录中的普通文件。"""


class _UploadChangedDuringInspection(Exception):
    """上传文件在完整性读取期间发生了变化。"""


class _ManagedMigrationConflict(Exception):
    """Legacy writable and managed read-only copies disagree."""


async def _get_thread_upload_lock(thread_id: str) -> asyncio.Lock:
    async with _thread_upload_lock_guard:
        lock = _thread_upload_locks.get(thread_id)
        if lock is None:
            lock = asyncio.Lock()
            _thread_upload_locks[thread_id] = lock
        return lock


def _thread_upload_lock_path(thread_id: str) -> Path:
    safe_thread_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", thread_id).strip("._") or "thread"
    return _thread_upload_lock_dir / f"uploads-{safe_thread_id}.lock"


def _acquire_process_lock(lock_path: Path):
    if fcntl is None:
        return None
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+", encoding="utf-8")
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    return handle


def _release_process_lock(handle) -> None:
    if handle is None or fcntl is None:
        return
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


@asynccontextmanager
async def _acquire_thread_upload_guard(thread_id: str):
    lock = await _get_thread_upload_lock(thread_id)
    async with lock:
        process_lock_handle = await asyncio.to_thread(
            _acquire_process_lock,
            _thread_upload_lock_path(thread_id),
        )
        try:
            yield
        finally:
            await asyncio.to_thread(_release_process_lock, process_lock_handle)


def get_uploads_dir(thread_id: str) -> Path:
    """获取线程 uploads 目录（不存在则创建）。

    参数：
        thread_id: 线程 ID。

    返回：
        uploads 目录路径。
    """
    try:
        base_dir = get_paths().sandbox_uploads_dir(thread_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir


def get_managed_uploads_dir(thread_id: str) -> Path:
    """Return the Backend-managed directory mounted read-only in sandboxes."""
    try:
        base_dir = get_paths().sandbox_knowledge_dir(thread_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir


def _convert_file_to_markdown_sync(file_path: Path) -> Path | None:
    """同步将文件转换为 Markdown。

    参数：
        file_path: 待转换文件路径。

    返回：
        转换成功返回 Markdown 文件路径，否则返回 None。
    """
    try:
        from markitdown import MarkItDown

        md = MarkItDown()
        result = md.convert(str(file_path))

        # 以同名 `.md` 文件落盘
        md_path = file_path.with_suffix(".md")
        md_path.write_text(result.text_content, encoding="utf-8")

        logger.info("Converted upload to markdown")
        return md_path
    except Exception as exc:
        logger.warning(
            "Upload conversion failed (%s)",
            type(exc).__name__,
        )
        return None


async def convert_file_to_markdown(file_path: Path) -> Path | None:
    """在线程池中异步执行 Markdown 转换。"""
    return await asyncio.to_thread(_convert_file_to_markdown_sync, file_path)


def get_markdown_extensions() -> set[str]:
    """获取当前配置下允许转换为 Markdown 的文件扩展名集合。"""
    try:
        return set(get_app_config().uploads.markdown_extensions)
    except Exception as exc:
        logger.warning(
            "Falling back to default markdown upload extensions (%s)",
            type(exc).__name__,
        )
        return set(UploadsConfig().markdown_extensions)


def get_uploads_config() -> UploadsConfig:
    """获取上传限制配置；独立调用时加载失败则使用安全默认值。"""
    try:
        return get_app_config().uploads
    except Exception as exc:
        logger.warning(
            "Falling back to default upload limits (%s)",
            type(exc).__name__,
        )
        return UploadsConfig()


def _normalize_filename(filename: str) -> str | None:
    """规范化并校验文件名。"""
    if "\x00" in filename or any(ord(char) < 32 or ord(char) == 127 for char in filename):
        return None
    safe_filename = Path(filename).name
    if not safe_filename or safe_filename in {".", ".."}:
        return None
    if "/" in safe_filename or "\\" in safe_filename:
        return None
    if len(safe_filename.encode("utf-8")) > 240:
        return None
    return safe_filename


def _is_reserved_managed_filename(filename: str) -> bool:
    """检查文件名是否占用知识物化保留命名空间。"""
    return filename.casefold().startswith("kb__")


def _is_valid_managed_filename(filename: str) -> bool:
    """仅接受 Backend 知识物化器生成的单层文件名。"""
    return _normalize_filename(filename) == filename and _MANAGED_KB_FILENAME_RE.fullmatch(filename) is not None


def _stream_managed_upload_metadata(
    uploads_dir: Path,
    filename: str,
    *,
    chunk_size: int,
) -> ManagedUploadMetadata:
    """从固定目录文件描述符分块读取并校验一个受管上传文件。"""
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    file_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    directory_fd = os.open(uploads_dir.resolve(strict=True), directory_flags)
    file_fd: int | None = None

    try:
        try:
            file_fd = os.open(filename, file_flags, dir_fd=directory_fd)
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise _UnsafeUploadTarget from exc
            raise

        before = os.fstat(file_fd)
        if not stat.S_ISREG(before.st_mode):
            raise _UnsafeUploadTarget

        digest = hashlib.sha256()
        size = 0
        while chunk := os.read(file_fd, chunk_size):
            digest.update(chunk)
            size += len(chunk)

        after = os.fstat(file_fd)
        try:
            current = os.stat(filename, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError as exc:
            raise _UploadChangedDuringInspection from exc

        if not stat.S_ISREG(current.st_mode):
            raise _UnsafeUploadTarget
        if (current.st_dev, current.st_ino) != (after.st_dev, after.st_ino):
            raise _UploadChangedDuringInspection
        if (
            size != after.st_size
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or before.st_ctime_ns != after.st_ctime_ns
            or current.st_size != after.st_size
            or current.st_mtime_ns != after.st_mtime_ns
            or current.st_ctime_ns != after.st_ctime_ns
        ):
            raise _UploadChangedDuringInspection

        return ManagedUploadMetadata(
            filename=filename,
            size=size,
            sha256=digest.hexdigest(),
        )
    finally:
        if file_fd is not None:
            os.close(file_fd)
        os.close(directory_fd)


def _migrate_legacy_managed_uploads(
    uploads_dir: Path,
    managed_dir: Path,
    *,
    chunk_size: int,
) -> None:
    """Atomically move legacy managed files out of the writable uploads mount."""
    managed_dir.mkdir(parents=True, exist_ok=True)
    for source in sorted(uploads_dir.iterdir()):
        if not _is_valid_managed_filename(source.name):
            continue
        source_stat = source.lstat()
        if not stat.S_ISREG(source_stat.st_mode):
            raise _UnsafeUploadTarget

        destination = managed_dir / source.name
        if destination.exists() or destination.is_symlink():
            source_metadata = _stream_managed_upload_metadata(
                uploads_dir,
                source.name,
                chunk_size=chunk_size,
            )
            destination_metadata = _stream_managed_upload_metadata(
                managed_dir,
                source.name,
                chunk_size=chunk_size,
            )
            if source_metadata.size != destination_metadata.size or source_metadata.sha256 != destination_metadata.sha256:
                raise _ManagedMigrationConflict
            source.unlink()
        else:
            os.replace(source, destination)
        destination.chmod(0o444)


def _allocate_unique_filename(uploads_dir: Path, filename: str, *, reserve_markdown_path: bool = False) -> str:
    """为线程 uploads 目录分配不会覆盖已有文件的文件名。"""

    def is_available(candidate_name: str) -> bool:
        target = uploads_dir / candidate_name
        if target.exists():
            return False
        return not reserve_markdown_path or not target.with_suffix(".md").exists()

    candidate = filename
    if is_available(candidate):
        return candidate

    stem = Path(filename).stem.strip() or "file"
    suffix = Path(filename).suffix
    for index in range(2, 10_000):
        candidate = f"{stem}-{index}{suffix}"
        if is_available(candidate):
            return candidate

    # 极端情况下仍然冲突时，退化为随机后缀，避免覆盖旧文件。
    return f"{stem}-{uuid4().hex[:8]}{suffix}"


class _UploadLimitExceeded(Exception):
    """上传流超过应用层限制。"""


def _write_upload_stream(
    upload: UploadFile,
    target_path: Path,
    *,
    max_file_size: int,
    remaining_request_size: int,
    chunk_size: int,
    mode: int,
) -> int:
    """从 multipart 临时文件分块写盘，避免把完整文件复制到内存。"""
    written = 0
    created_target = False
    upload.file.seek(0)

    try:
        target = target_path.open("xb")
        created_target = True
        with target:
            while chunk := upload.file.read(chunk_size):
                written += len(chunk)
                if written > max_file_size:
                    raise _UploadLimitExceeded(f"File exceeds the {max_file_size}-byte upload limit")
                if written > remaining_request_size:
                    raise _UploadLimitExceeded("Upload request exceeds the configured total size limit")
                target.write(chunk)
            os.fchmod(target.fileno(), mode)
    except Exception:
        if created_target:
            target_path.unlink(missing_ok=True)
        raise

    return written


def _copy_conversion_output(
    source_path: Path,
    target_path: Path,
    *,
    max_bytes: int,
    chunk_size: int,
) -> int:
    """Publish trusted conversion output without following a raced target link."""

    written = 0
    created_target = False
    try:
        with source_path.open("rb") as source:
            target = target_path.open("xb")
            created_target = True
            with target:
                while chunk := source.read(chunk_size):
                    written += len(chunk)
                    if written > max_bytes:
                        raise _UploadLimitExceeded(
                            "Converted upload exceeds the configured file size limit"
                        )
                    target.write(chunk)
                os.fchmod(target.fileno(), 0o666)
    except Exception:
        if created_target:
            target_path.unlink(missing_ok=True)
        raise
    return written


async def _convert_snapshot_to_markdown(
    snapshot_path: Path,
    target_path: Path,
    *,
    max_bytes: int,
    chunk_size: int,
) -> Path | None:
    """Convert a private source snapshot and exclusively publish its Markdown."""

    converted_snapshot = snapshot_path.with_suffix(".md")
    try:
        converted = await convert_file_to_markdown(snapshot_path)
        if converted is None:
            return None
        await asyncio.to_thread(
            _copy_conversion_output,
            converted,
            target_path,
            max_bytes=max_bytes,
            chunk_size=chunk_size,
        )
        return target_path
    finally:
        converted_snapshot.unlink(missing_ok=True)


def _remove_created_files(paths: list[Path]) -> None:
    """回滚当前请求创建的文件，不触碰请求前已有内容。"""
    for path in reversed(paths):
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning(
                "Failed to clean up incomplete upload (%s)",
                type(exc).__name__,
            )


def _remove_thread_file_snapshots(
    snapshots: list[ThreadFileSnapshot],
) -> None:
    for snapshot in snapshots:
        try:
            snapshot.cleanup()
        except OSError as exc:
            logger.warning(
                "Failed to clean up upload snapshot (%s)",
                type(exc).__name__,
            )


@router.post("", response_model=UploadResponse)
async def upload_files(
    thread_id: str,
    files: list[UploadFile] = File(...),
) -> UploadResponse:
    """上传文件到线程目录，并按需转换为 Markdown。

    PDF/PPT/Excel/Word 文件会通过 markitdown 转换为 Markdown。
    普通文件与转换文件保存在 `/mnt/user-data/uploads`；Backend 物化的
    `kb__...` 知识 Markdown 保存在只读的 `/mnt/user-data/knowledge`。

    参数：
        thread_id: 目标线程 ID。
        files: 待上传文件列表。

    返回：
        包含成功状态与文件信息的上传响应。
    """
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")

    uploads_dir = get_uploads_dir(thread_id)
    managed_dir = get_managed_uploads_dir(thread_id)
    paths = get_paths()
    upload_config = get_uploads_config()
    markdown_extensions = get_markdown_extensions()
    uploaded_files: list[dict[str, Any]] = []
    created_paths: list[Path] = []
    conversion_snapshots: list[ThreadFileSnapshot] = []
    total_uploaded_size = 0

    try:
        async with _acquire_thread_upload_guard(thread_id):
            await asyncio.to_thread(
                _migrate_legacy_managed_uploads,
                uploads_dir,
                managed_dir,
                chunk_size=upload_config.stream_chunk_size_bytes,
            )
            for file in files:
                if not file.filename:
                    continue

                safe_filename = _normalize_filename(file.filename)
                if safe_filename is None:
                    logger.warning("Skipping upload with an unsafe filename")
                    continue

                is_managed = _is_reserved_managed_filename(safe_filename)
                if is_managed and (safe_filename != file.filename or not _is_valid_managed_filename(safe_filename)):
                    raise HTTPException(
                        status_code=400,
                        detail="Invalid managed upload filename",
                    )
                target_dir = managed_dir if is_managed else uploads_dir
                virtual_subdir = "knowledge" if is_managed else "uploads"
                needs_markdown_conversion = not is_managed and Path(safe_filename).suffix.lower() in markdown_extensions
                stored_filename = (
                    safe_filename
                    if is_managed
                    else _allocate_unique_filename(
                        target_dir,
                        safe_filename,
                        reserve_markdown_path=needs_markdown_conversion,
                    )
                )
                file_path = target_dir / stored_filename
                write_path = target_dir / f".managed-upload-{uuid4().hex}.tmp" if is_managed else file_path
                conversion_snapshot = None
                try:
                    file_size = await asyncio.to_thread(
                        _write_upload_stream,
                        file,
                        write_path,
                        max_file_size=upload_config.max_file_size_bytes,
                        remaining_request_size=upload_config.max_request_size_bytes - total_uploaded_size,
                        chunk_size=upload_config.stream_chunk_size_bytes,
                        mode=0o444 if is_managed else 0o666,
                    )
                except _UploadLimitExceeded as exc:
                    raise HTTPException(status_code=413, detail=f"{safe_filename}: {exc}") from exc

                created_paths.append(write_path)
                total_uploaded_size += file_size

                if needs_markdown_conversion:
                    try:
                        resolved_upload = ResolvedThreadFile(
                            root=uploads_dir.parent.resolve(),
                            parts=(uploads_dir.name, stored_filename),
                        )
                        conversion_snapshot = await snapshot_thread_file_async(
                            resolved_upload,
                            max_bytes=upload_config.max_file_size_bytes,
                            suffix=Path(stored_filename).suffix,
                        )
                        conversion_snapshots.append(conversion_snapshot)
                    except ThreadFileError as exc:
                        raise HTTPException(
                            status_code=409,
                            detail="Upload changed before conversion",
                        ) from exc
                if is_managed:
                    target_existed = file_path.exists() or file_path.is_symlink()
                    if target_existed and not stat.S_ISREG(file_path.lstat().st_mode):
                        raise _UnsafeUploadTarget
                    # A managed filename is content-addressed and canonical. Replace
                    # a corrupt prior copy atomically instead of inventing a filename
                    # that no longer matches the reserved-name contract.
                    os.replace(write_path, file_path)
                    created_paths.remove(write_path)
                    if not target_existed:
                        created_paths.append(file_path)

                # 线程 uploads 目录已直接挂载到所有沙箱；
                # 只写宿主机目录即可，避免通过沙箱 API 重复写入导致权限冲突。
                storage_dir = paths.sandbox_knowledge_dir(thread_id) if is_managed else paths.sandbox_uploads_dir(thread_id)
                relative_path = str(storage_dir / stored_filename)
                virtual_path = f"{VIRTUAL_PATH_PREFIX}/{virtual_subdir}/{stored_filename}"

                file_info: dict[str, Any] = {
                    "filename": stored_filename,
                    "size": file_size,
                    "path": relative_path,  # 实际文件系统路径（相对 backend/）
                    "virtual_path": virtual_path,  # Agent 在沙箱中访问的路径
                    "artifact_url": f"/api/threads/{thread_id}/artifacts/mnt/user-data/{virtual_subdir}/{stored_filename}",  # HTTP 访问地址
                }

                logger.info("Stored uploaded file (%s bytes)", file_size)

                # 检查文件是否需要转换为 Markdown
                if needs_markdown_conversion:
                    try:
                        md_path = await _convert_snapshot_to_markdown(
                            conversion_snapshot.path,
                            file_path.with_suffix(".md"),
                            max_bytes=upload_config.max_file_size_bytes,
                            chunk_size=upload_config.stream_chunk_size_bytes,
                        )
                        if md_path:
                            created_paths.append(md_path)
                            md_relative_path = str(paths.sandbox_uploads_dir(thread_id) / md_path.name)
                            md_virtual_path = f"{VIRTUAL_PATH_PREFIX}/uploads/{md_path.name}"

                            file_info["markdown_file"] = md_path.name
                            file_info["markdown_path"] = md_relative_path
                            file_info["markdown_virtual_path"] = md_virtual_path
                            file_info["markdown_artifact_url"] = f"/api/threads/{thread_id}/artifacts/mnt/user-data/uploads/{md_path.name}"
                    except _UploadLimitExceeded as exc:
                        raise HTTPException(status_code=413, detail=str(exc)) from exc
                    except FileExistsError as exc:
                        raise HTTPException(
                            status_code=409,
                            detail="Markdown upload target changed during conversion",
                        ) from exc

                uploaded_files.append(file_info)
    except HTTPException:
        _remove_created_files(created_paths)
        raise
    except _ManagedMigrationConflict as exc:
        _remove_created_files(created_paths)
        raise HTTPException(
            status_code=409,
            detail="Managed upload migration conflict",
        ) from exc
    except _UnsafeUploadTarget as exc:
        _remove_created_files(created_paths)
        raise HTTPException(status_code=403, detail="Unsafe managed upload target") from exc
    except Exception as exc:
        _remove_created_files(created_paths)
        logger.error("Failed to store uploaded files (%s)", type(exc).__name__)
        raise HTTPException(status_code=500, detail="Failed to store uploaded files") from exc
    finally:
        _remove_thread_file_snapshots(conversion_snapshots)

    return UploadResponse(
        success=True,
        files=uploaded_files,
        message=f"Successfully uploaded {len(uploaded_files)} file(s)",
    )


@router.get("/list", response_model=ListUploadsResponse)
async def list_uploaded_files(thread_id: str) -> ListUploadsResponse:
    """List ordinary uploads and Backend-managed knowledge files.

    参数：
        thread_id: 要查询的线程 ID。

    返回：
        包含文件元数据列表的响应对象。
    """
    uploads_dir = get_uploads_dir(thread_id)
    managed_dir = get_managed_uploads_dir(thread_id)
    chunk_size = get_uploads_config().stream_chunk_size_bytes
    files: list[dict[str, Any]] = []
    async with _acquire_thread_upload_guard(thread_id):
        try:
            await asyncio.to_thread(
                _migrate_legacy_managed_uploads,
                uploads_dir,
                managed_dir,
                chunk_size=chunk_size,
            )
        except (_UnsafeUploadTarget, _ManagedMigrationConflict) as exc:
            raise HTTPException(
                status_code=409,
                detail="Managed upload migration requires operator intervention",
            ) from exc

        paths = get_paths()
        for directory, storage_dir, virtual_subdir in (
            (
                uploads_dir,
                paths.sandbox_uploads_dir(thread_id),
                "uploads",
            ),
            (
                managed_dir,
                paths.sandbox_knowledge_dir(thread_id),
                "knowledge",
            ),
        ):
            for file_path in sorted(directory.iterdir()):
                file_stat = file_path.lstat()
                if not stat.S_ISREG(file_stat.st_mode):
                    continue
                files.append(
                    {
                        "filename": file_path.name,
                        "size": file_stat.st_size,
                        "path": str(storage_dir / file_path.name),
                        "virtual_path": (f"{VIRTUAL_PATH_PREFIX}/{virtual_subdir}/{file_path.name}"),
                        "artifact_url": (f"/api/threads/{thread_id}/artifacts/mnt/user-data/{virtual_subdir}/{file_path.name}"),
                        "extension": file_path.suffix,
                        "modified": file_stat.st_mtime,
                    }
                )

    return ListUploadsResponse(files=files, count=len(files))


@router.get(
    "/metadata",
    response_model=ManagedUploadMetadata,
    include_in_schema=False,
)
async def get_managed_upload_metadata(
    thread_id: str,
    filename: str,
) -> ManagedUploadMetadata:
    """供 Backend 内部校验受管知识文件的实际内容。"""
    if not _is_valid_managed_filename(filename):
        raise HTTPException(status_code=400, detail="Invalid managed upload filename")

    uploads_dir = get_uploads_dir(thread_id)
    managed_dir = get_managed_uploads_dir(thread_id)
    chunk_size = get_uploads_config().stream_chunk_size_bytes
    async with _acquire_thread_upload_guard(thread_id):
        try:
            await asyncio.to_thread(
                _migrate_legacy_managed_uploads,
                uploads_dir,
                managed_dir,
                chunk_size=chunk_size,
            )
            return await asyncio.to_thread(
                _stream_managed_upload_metadata,
                managed_dir,
                filename,
                chunk_size=chunk_size,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"File not found: {filename}") from exc
        except (PermissionError, _UnsafeUploadTarget) as exc:
            raise HTTPException(status_code=403, detail="Access denied") from exc
        except _UploadChangedDuringInspection as exc:
            raise HTTPException(status_code=409, detail="Upload changed during integrity inspection") from exc
        except _ManagedMigrationConflict as exc:
            raise HTTPException(
                status_code=409,
                detail="Managed upload migration conflict",
            ) from exc
        except OSError as exc:
            logger.error(
                "Failed to inspect managed upload (%s)",
                type(exc).__name__,
            )
            raise HTTPException(status_code=500, detail="Failed to inspect managed upload") from exc


@router.delete("/{filename}", response_model=DeleteUploadResponse)
async def delete_uploaded_file(
    thread_id: str,
    filename: str,
    companion_filename: str | None = None,
) -> DeleteUploadResponse:
    """删除普通上传或 Backend 管理的知识文件。

    参数：
        thread_id: 线程 ID。
        filename: 待删除文件名。

    返回：
        删除结果消息。
    """
    uploads_dir = get_uploads_dir(thread_id)
    managed_dir = get_managed_uploads_dir(thread_id)
    chunk_size = get_uploads_config().stream_chunk_size_bytes
    safe_filename = _normalize_filename(filename)
    if safe_filename is None or safe_filename != filename:
        raise HTTPException(status_code=400, detail=f"Invalid filename: {filename}")

    is_managed = _is_reserved_managed_filename(safe_filename)
    if is_managed and not _is_valid_managed_filename(safe_filename):
        raise HTTPException(status_code=400, detail="Invalid managed upload filename")
    if is_managed and companion_filename is not None:
        raise HTTPException(
            status_code=400,
            detail="Managed uploads cannot have a markdown companion",
        )

    target_dir = managed_dir if is_managed else uploads_dir
    file_path = target_dir / safe_filename
    companion_path: Path | None = None
    if companion_filename:
        safe_companion = _normalize_filename(companion_filename)
        expected_companion = file_path.with_suffix(".md").name
        if safe_companion is None or safe_companion != companion_filename or safe_companion != expected_companion or safe_companion == safe_filename:
            raise HTTPException(status_code=400, detail="Invalid companion filename")
        companion_path = uploads_dir / safe_companion

    async with _acquire_thread_upload_guard(thread_id):
        try:
            await asyncio.to_thread(
                _migrate_legacy_managed_uploads,
                uploads_dir,
                managed_dir,
                chunk_size=chunk_size,
            )
        except _ManagedMigrationConflict as exc:
            raise HTTPException(
                status_code=409,
                detail="Managed upload migration conflict",
            ) from exc
        except _UnsafeUploadTarget as exc:
            raise HTTPException(status_code=403, detail="Unsafe managed upload target") from exc

        if not file_path.exists() and not file_path.is_symlink():
            raise HTTPException(status_code=404, detail=f"File not found: {safe_filename}")

        for candidate in (file_path, companion_path):
            if candidate is None or (not candidate.exists() and not candidate.is_symlink()):
                continue
            try:
                candidate.resolve().relative_to(target_dir.resolve())
                candidate_stat = candidate.lstat()
            except (OSError, ValueError) as exc:
                raise HTTPException(status_code=403, detail="Access denied") from exc
            if not stat.S_ISREG(candidate_stat.st_mode):
                raise HTTPException(status_code=403, detail="Access denied")

        try:
            if companion_path is not None:
                companion_path.unlink(missing_ok=True)
            file_path.unlink()
            deleted_names = [safe_filename]
            if companion_path is not None:
                deleted_names.append(companion_path.name)
            logger.info("Deleted %s uploaded file(s)", len(deleted_names))
            return DeleteUploadResponse(
                success=True,
                message=f"Deleted {', '.join(deleted_names)}",
            )
        except HTTPException:
            raise
        except Exception as exc:
            logger.error("Failed to delete uploaded file (%s)", type(exc).__name__)
            raise HTTPException(
                status_code=500,
                detail="Failed to delete uploaded file",
            ) from exc
