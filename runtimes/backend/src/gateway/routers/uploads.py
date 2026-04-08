"""处理文件上传的路由模块。"""

import asyncio
from contextlib import asynccontextmanager
import tempfile
import logging
from pathlib import Path
import re
from typing import Any
from uuid import uuid4

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from src.config import get_app_config
from src.config.uploads_config import UploadsConfig
from src.config.paths import VIRTUAL_PATH_PREFIX, get_paths
from src.sandbox.sandbox_provider import get_sandbox_provider

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/threads/{thread_id}/uploads", tags=["uploads"])
_thread_upload_locks: dict[str, asyncio.Lock] = {}
_thread_upload_lock_guard = asyncio.Lock()
_thread_upload_lock_dir = Path(tempfile.gettempdir()) / "lumen-thread-locks"


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

        logger.info(f"Converted {file_path.name} to markdown: {md_path.name}")
        return md_path
    except Exception as e:
        logger.error(f"Failed to convert {file_path.name} to markdown: {e}")
        return None


async def convert_file_to_markdown(file_path: Path) -> Path | None:
    """在线程池中异步执行 Markdown 转换。"""
    return await asyncio.to_thread(_convert_file_to_markdown_sync, file_path)


def get_markdown_extensions() -> set[str]:
    """获取当前配置下允许转换为 Markdown 的文件扩展名集合。"""
    try:
        return set(get_app_config().uploads.markdown_extensions)
    except Exception:
        logger.warning("Falling back to default markdown upload extensions because app config could not be loaded", exc_info=True)
        return set(UploadsConfig().markdown_extensions)


def _normalize_filename(filename: str) -> str | None:
    """规范化并校验文件名。"""
    safe_filename = Path(filename).name
    if not safe_filename or safe_filename in {".", ".."}:
        return None
    if "/" in safe_filename or "\\" in safe_filename:
        return None
    return safe_filename


def _make_file_writable_for_sandbox(file_path: Path) -> None:
    """确保宿主机侧新文件对容器内非 root 用户也可写。"""
    try:
        file_path.chmod(0o666)
    except FileNotFoundError:
        return
    except Exception:
        logger.warning("Failed to chmod uploaded file for sandbox write access: %s", file_path, exc_info=True)


def _allocate_unique_filename(uploads_dir: Path, filename: str) -> str:
    """为线程 uploads 目录分配不会覆盖已有文件的文件名。"""
    candidate = filename
    target_path = uploads_dir / candidate
    if not target_path.exists():
        return candidate

    stem = Path(filename).stem.strip() or "file"
    suffix = Path(filename).suffix
    for index in range(2, 10_000):
        candidate = f"{stem}-{index}{suffix}"
        target_path = uploads_dir / candidate
        if not target_path.exists():
            return candidate

    # 极端情况下仍然冲突时，退化为随机后缀，避免覆盖旧文件。
    return f"{stem}-{uuid4().hex[:8]}{suffix}"


@router.post("", response_model=UploadResponse)
async def upload_files(
    thread_id: str,
    files: list[UploadFile] = File(...),
) -> UploadResponse:
    """上传文件到线程目录，并按需转换为 Markdown。

    PDF/PPT/Excel/Word 文件会通过 markitdown 转换为 Markdown。
    原文件与转换文件都会保存到 `/mnt/user-data/uploads`。

    参数：
        thread_id: 目标线程 ID。
        files: 待上传文件列表。

    返回：
        包含成功状态与文件信息的上传响应。
    """
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")

    uploads_dir = get_uploads_dir(thread_id)
    paths = get_paths()
    uploaded_files: list[dict[str, Any]] = []

    async with _acquire_thread_upload_guard(thread_id):
        for file in files:
            if not file.filename:
                continue

            try:
                safe_filename = _normalize_filename(file.filename)
                if safe_filename is None:
                    logger.warning(f"Skipping file with unsafe filename: {file.filename!r}")
                    continue

                content = await file.read()
                stored_filename = _allocate_unique_filename(uploads_dir, safe_filename)
                file_path = uploads_dir / stored_filename
                file_path.write_bytes(content)
                _make_file_writable_for_sandbox(file_path)

                # 线程 uploads 目录已直接挂载到所有沙箱；
                # 只写宿主机目录即可，避免通过沙箱 API 重复写入导致权限冲突。
                relative_path = str(paths.sandbox_uploads_dir(thread_id) / stored_filename)
                virtual_path = f"{VIRTUAL_PATH_PREFIX}/uploads/{stored_filename}"

                file_info: dict[str, Any] = {
                    "filename": stored_filename,
                    "size": len(content),
                    "path": relative_path,  # 实际文件系统路径（相对 backend/）
                    "virtual_path": virtual_path,  # Agent 在沙箱中访问的路径
                    "artifact_url": f"/api/threads/{thread_id}/artifacts/mnt/user-data/uploads/{stored_filename}",  # HTTP 访问地址
                }

                logger.info(f"Saved file: {stored_filename} ({len(content)} bytes) to {relative_path}")

                # 检查文件是否需要转换为 Markdown
                file_ext = file_path.suffix.lower()
                if file_ext in get_markdown_extensions():
                    md_path = await convert_file_to_markdown(file_path)
                    if md_path:
                        _make_file_writable_for_sandbox(md_path)
                        md_relative_path = str(paths.sandbox_uploads_dir(thread_id) / md_path.name)
                        md_virtual_path = f"{VIRTUAL_PATH_PREFIX}/uploads/{md_path.name}"

                        file_info["markdown_file"] = md_path.name
                        file_info["markdown_path"] = md_relative_path
                        file_info["markdown_virtual_path"] = md_virtual_path
                        file_info["markdown_artifact_url"] = f"/api/threads/{thread_id}/artifacts/mnt/user-data/uploads/{md_path.name}"

                uploaded_files.append(file_info)

            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Failed to upload {file.filename}: {e}")
                raise HTTPException(status_code=500, detail=f"Failed to upload {file.filename}: {str(e)}")

    return UploadResponse(
        success=True,
        files=uploaded_files,
        message=f"Successfully uploaded {len(uploaded_files)} file(s)",
    )


@router.get("/list", response_model=ListUploadsResponse)
async def list_uploaded_files(thread_id: str) -> ListUploadsResponse:
    """列出线程 uploads 目录中的文件。

    参数：
        thread_id: 要查询的线程 ID。

    返回：
        包含文件元数据列表的响应对象。
    """
    uploads_dir = get_uploads_dir(thread_id)

    if not uploads_dir.exists():
        return ListUploadsResponse(files=[], count=0)

    files = []
    for file_path in sorted(uploads_dir.iterdir()):
        if file_path.is_file():
            stat = file_path.stat()
            relative_path = str(get_paths().sandbox_uploads_dir(thread_id) / file_path.name)
            files.append(
                {
                    "filename": file_path.name,
                    "size": stat.st_size,
                    "path": relative_path,  # 实际文件系统路径
                    "virtual_path": f"{VIRTUAL_PATH_PREFIX}/uploads/{file_path.name}",  # Agent 在沙箱中访问的路径
                    "artifact_url": f"/api/threads/{thread_id}/artifacts/mnt/user-data/uploads/{file_path.name}",  # HTTP 访问地址
                    "extension": file_path.suffix,
                    "modified": stat.st_mtime,
                }
            )

    return ListUploadsResponse(files=files, count=len(files))


@router.delete("/{filename}", response_model=DeleteUploadResponse)
async def delete_uploaded_file(thread_id: str, filename: str) -> DeleteUploadResponse:
    """删除线程 uploads 目录中的指定文件。

    参数：
        thread_id: 线程 ID。
        filename: 待删除文件名。

    返回：
        删除结果消息。
    """
    uploads_dir = get_uploads_dir(thread_id)
    safe_filename = _normalize_filename(filename)
    if safe_filename is None or safe_filename != filename:
        raise HTTPException(status_code=400, detail=f"Invalid filename: {filename}")

    file_path = uploads_dir / safe_filename

    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {safe_filename}")

    # 安全校验：确保路径位于 uploads 目录内
    try:
        file_path.resolve().relative_to(uploads_dir.resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied")

    async with _acquire_thread_upload_guard(thread_id):
        try:
            file_path.unlink()
            logger.info(f"Deleted file: {safe_filename}")
            return DeleteUploadResponse(success=True, message=f"Deleted {safe_filename}")
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to delete {safe_filename}: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to delete {safe_filename}: {str(e)}")
