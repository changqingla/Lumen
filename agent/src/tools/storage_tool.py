"""UploadToStorageTool — 将 Workspace 内的文件上传到 MinIO 对象存储。

验证路径安全 → 读取文件 → 上传到 MinIO → 返回 Presigned URL。
上传超时由外层 asyncio.wait_for 控制（60 秒）。
"""

import asyncio
from pathlib import Path
from typing import Optional

from langchain.tools import BaseTool
from langchain_core.callbacks import CallbackManagerForToolRun

from ..utils.logger import get_logger
from ..utils.minio_client import get_agent_minio_client

logger = get_logger(__name__)

UPLOAD_TIMEOUT = 60  # seconds


class UploadToStorageTool(BaseTool):
    """将工作目录中的文件上传到对象存储，返回下载链接。"""

    name: str = "upload_to_storage"
    description: str = (
        "将工作目录中的文件上传到对象存储，返回下载链接。\n\n"
        '输入: 文件的相对路径（字符串），如 "output.pptx"\n'
        "输出: 文件的下载 URL"
    )

    workspace_path: str = ""
    session_id: str = ""

    class Config:
        arbitrary_types_allowed = True

    def _validate_path(self, path: str) -> tuple[bool, str]:
        """验证路径安全性 — resolve() 后检查是否在 workspace 内。

        Returns:
            (is_valid, error_message) — 合法时 error_message 为空字符串。
        """
        if not path or not path.strip():
            return False, "文件路径不能为空"

        path = path.strip()

        if path.startswith("/"):
            return False, "不允许使用绝对路径"

        workspace = Path(self.workspace_path).resolve()
        target = (workspace / path).resolve()

        if not str(target).startswith(str(workspace) + "/") and target != workspace:
            return False, f"路径 '{path}' 超出工作目录边界"

        return True, ""

    def _run(self, query: str, run_manager: Optional[CallbackManagerForToolRun] = None) -> str:
        """Sync wrapper — delegates to _arun."""
        import asyncio as _asyncio

        return _asyncio.get_event_loop().run_until_complete(self._arun(query, run_manager))

    async def _arun(self, query: str, run_manager=None) -> str:
        """主入口：验证路径 → 读取文件 → 上传 MinIO → 返回下载链接。"""
        try:
            file_path = query.strip().strip('"').strip("'")

            # 1. 验证路径安全
            is_valid, err_msg = self._validate_path(file_path)
            if not is_valid:
                return f"[ERROR] upload_to_storage: {err_msg}"

            # 2. 检查文件存在于 workspace
            workspace = Path(self.workspace_path)
            target = (workspace / file_path).resolve()

            if not target.is_file():
                return f"[ERROR] upload_to_storage: 文件不存在: {file_path}"

            # 3. 读取文件字节
            file_data = target.read_bytes()
            filename = target.name

            # 4. 构造 object_name
            object_name = f"agent-outputs/{self.session_id}/{filename}"

            # 5. 根据文件扩展名推断 content_type
            import mimetypes
            content_type, _ = mimetypes.guess_type(filename)
            if not content_type:
                content_type = "application/octet-stream"

            # 6. 调用 MinIO 客户端上传
            client = get_agent_minio_client()
            await client.upload_file(object_name=object_name, file_data=file_data, content_type=content_type)

            # 7. 获取 presigned URL
            url = client.get_presigned_url(object_name)

            logger.info(
                "upload_to_storage success",
                extra={
                    "file": file_path,
                    "object_name": object_name,
                    "size": len(file_data),
                },
            )

            # 8. 返回下载链接字符串
            return f"文件已上传。下载链接（24小时有效）：{url}"

        except Exception as exc:
            logger.error("upload_to_storage unexpected error", extra={"error": str(exc)})
            return f"[ERROR] upload_to_storage: {exc}"
