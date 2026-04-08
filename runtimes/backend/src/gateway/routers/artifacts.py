import logging
import mimetypes
import zipfile
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, Response

from src.gateway.path_utils import resolve_thread_virtual_path

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["artifacts"])


def is_text_file_by_content(path: Path, sample_size: int = 8192) -> bool:
    """通过检测内容中的空字节判断文件是否为文本。"""
    try:
        with open(path, "rb") as f:
            chunk = f.read(sample_size)
            # 文本文件通常不应包含空字节
            return b"\x00" not in chunk
    except Exception:
        return False


def _extract_file_from_skill_archive(zip_path: Path, internal_path: str) -> bytes | None:
    """从 `.skill` 压缩包中提取内部文件内容。

    参数：
        zip_path: `.skill` 文件路径（ZIP 格式）。
        internal_path: 压缩包内文件路径（如 `SKILL.md`）。

    返回：
        找到时返回文件字节内容，否则返回 `None`。
    """
    if not zipfile.is_zipfile(zip_path):
        return None

    try:
        with zipfile.ZipFile(zip_path, "r") as zip_ref:
            # 列出压缩包内全部文件
            namelist = zip_ref.namelist()

            # 先尝试直接匹配路径
            if internal_path in namelist:
                return zip_ref.read(internal_path)

            # 再尝试带任意顶层目录前缀的匹配（如 "skill-name/SKILL.md"）
            for name in namelist:
                if name.endswith("/" + internal_path) or name == internal_path:
                    return zip_ref.read(name)

            # 未找到目标文件
            return None
    except (zipfile.BadZipFile, KeyError):
        return None


@router.get(
    "/threads/{thread_id}/artifacts/{path:path}",
    summary="获取产物文件",
    description="读取 AI Agent 生成的产物文件，支持文本、HTML 和二进制文件。",
)
async def get_artifact(thread_id: str, path: str, request: Request) -> FileResponse:
    """获取线程产物文件，自动返回合适的内容类型。

    支持通过 `?download=true` 强制下载。

    参数：
        thread_id: 线程 ID。
        path: 带虚拟前缀的产物路径（如 `mnt/user-data/outputs/file.txt`）。
        request: FastAPI 请求对象（自动注入）。

    返回：
        对应文件内容响应。

    异常：
        HTTPException:
            - 400：路径无效或不是文件
            - 403：访问被拒绝（路径穿越）
            - 404：文件不存在
    """
    # 检查是否请求 .skill 压缩包内文件（如 xxx.skill/SKILL.md）
    if ".skill/" in path:
        # 在 ".skill/" 处分割，得到压缩包路径与内部文件路径
        skill_marker = ".skill/"
        marker_pos = path.find(skill_marker)
        skill_file_path = path[: marker_pos + len(".skill")]  # 例如："mnt/user-data/outputs/my-skill.skill"
        internal_path = path[marker_pos + len(skill_marker) :]  # 例如："SKILL.md"

        actual_skill_path = resolve_thread_virtual_path(thread_id, skill_file_path)

        if not actual_skill_path.exists():
            raise HTTPException(status_code=404, detail=f"Skill file not found: {skill_file_path}")

        if not actual_skill_path.is_file():
            raise HTTPException(status_code=400, detail=f"Path is not a file: {skill_file_path}")

        # 从 .skill 压缩包提取目标文件
        content = _extract_file_from_skill_archive(actual_skill_path, internal_path)
        if content is None:
            raise HTTPException(status_code=404, detail=f"File '{internal_path}' not found in skill archive")

        # 根据内部文件名推断 MIME 类型
        mime_type, _ = mimetypes.guess_type(internal_path)
        # 添加缓存头，避免频繁重复解压（缓存 5 分钟）
        cache_headers = {"Cache-Control": "private, max-age=300"}
        if mime_type and mime_type.startswith("text/"):
            return PlainTextResponse(content=content.decode("utf-8"), media_type=mime_type, headers=cache_headers)

        # 对看起来是文本但类型未知的内容，默认按纯文本返回
        try:
            return PlainTextResponse(content=content.decode("utf-8"), media_type="text/plain", headers=cache_headers)
        except UnicodeDecodeError:
            return Response(content=content, media_type=mime_type or "application/octet-stream", headers=cache_headers)

    actual_path = resolve_thread_virtual_path(thread_id, path)

    logger.info(f"解析产物路径：thread_id={thread_id}, requested_path={path}, actual_path={actual_path}")

    if not actual_path.exists():
        raise HTTPException(status_code=404, detail=f"Artifact not found: {path}")

    if not actual_path.is_file():
        raise HTTPException(status_code=400, detail=f"Path is not a file: {path}")

    mime_type, _ = mimetypes.guess_type(actual_path)

    # 为 Content-Disposition 头编码文件名（RFC 5987）
    encoded_filename = quote(actual_path.name)

    # 若 `download` 查询参数为 true，则按附件下载返回
    download = request.query_params.get("download")
    should_download = str(download).lower() in {"1", "true", "yes", "on"} if download is not None else False
    if should_download:
        return FileResponse(path=actual_path, filename=actual_path.name, media_type=mime_type, headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}"})

    if mime_type and mime_type == "text/html":
        return HTMLResponse(content=actual_path.read_text())

    if mime_type and mime_type.startswith("text/"):
        return PlainTextResponse(content=actual_path.read_text(), media_type=mime_type)

    if is_text_file_by_content(actual_path):
        return PlainTextResponse(content=actual_path.read_text(), media_type=mime_type)

    return Response(content=actual_path.read_bytes(), media_type=mime_type, headers={"Content-Disposition": f"inline; filename*=UTF-8''{encoded_filename}"})
