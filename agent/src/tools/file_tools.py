"""文件写入工具 — write_file（覆盖）和 append_to_file（追加）。

通过 Python 文件 IO 直接写入，不经过 shell，
以支持任意字符（反引号、$、引号、换行等）。
"""
import json
from pathlib import Path
from typing import Optional

from langchain.tools import BaseTool
from langchain_core.callbacks import CallbackManagerForToolRun

from ..utils.logger import get_logger

logger = get_logger(__name__)


def _validate_workspace_path(workspace_path: str, path: str) -> tuple[bool, str]:
    """验证路径安全性 — resolve() 后检查是否在 workspace 内。

    Returns:
        (is_valid, error_message) — 合法时 error_message 为空字符串。
    """
    if not path or not path.strip():
        return False, "路径不能为空"

    path = path.strip()

    if path.startswith("/"):
        return False, "不允许使用绝对路径"

    workspace = Path(workspace_path).resolve()
    target = (workspace / path).resolve()

    if not str(target).startswith(str(workspace) + "/") and target != workspace:
        return False, f"路径 '{path}' 超出工作目录边界"

    return True, ""


class WriteFileTool(BaseTool):
    """在工作目录中创建或覆盖写入文件。"""

    name: str = "write_file"
    description: str = (
        "在工作目录中创建或覆盖写入文件（如果文件已存在则覆盖）。\n\n"
        '输入格式（JSON）:\n  {"path": "generate.js", "content": "...文件内容..."}\n\n'
        "注意: path 必须是相对路径，不能包含 .. 或以 / 开头。\n"
        "与 append_to_file 的区别: write_file 会覆盖已有文件，适合创建新脚本。"
    )

    workspace_path: str = ""

    class Config:
        arbitrary_types_allowed = True

    def _run(self, query: str, run_manager: Optional[CallbackManagerForToolRun] = None) -> str:
        import asyncio
        return asyncio.get_event_loop().run_until_complete(self._arun(query, run_manager))

    async def _arun(self, query: str, run_manager=None) -> str:
        try:
            query = query.strip()
            try:
                data = json.loads(query)
            except json.JSONDecodeError:
                return '[ERROR] write_file: 输入格式错误，需要 JSON 格式 {"path": "...", "content": "..."}'

            path = data.get("path", "").strip()
            content = data.get("content", "")

            if not path:
                return "[ERROR] write_file: 缺少 path 字段"

            is_valid, err_msg = _validate_workspace_path(self.workspace_path, path)
            if not is_valid:
                return f"[ERROR] write_file: {err_msg}"

            workspace = Path(self.workspace_path)
            target = workspace / path
            target.parent.mkdir(parents=True, exist_ok=True)

            with open(target, "w", encoding="utf-8") as f:
                f.write(content)

            logger.info("write_file success", extra={"path": path, "chars": len(content)})
            return f"已写入 {len(content)} 字符到 {path}"

        except Exception as exc:
            logger.error("write_file unexpected error", extra={"error": str(exc)})
            return f"[ERROR] write_file: {exc}"


class AppendToFileTool(BaseTool):
    """在工作目录中追加写入文件。"""

    name: str = "append_to_file"
    description: str = (
        "在工作目录中追加写入文件。\n\n"
        '输入格式（JSON）:\n  {"path": "scripts/generate.js", "content": "...文件内容..."}\n\n'
        "注意: path 必须是相对路径，不能包含 .. 或以 / 开头"
    )

    workspace_path: str = ""

    class Config:
        arbitrary_types_allowed = True

    def _validate_path(self, path: str) -> tuple[bool, str]:
        return _validate_workspace_path(self.workspace_path, path)

    def _run(self, query: str, run_manager: Optional[CallbackManagerForToolRun] = None) -> str:
        """Sync wrapper — delegates to _arun."""
        import asyncio
        return asyncio.get_event_loop().run_until_complete(self._arun(query, run_manager))

    async def _arun(self, query: str, run_manager=None) -> str:
        """主入口：解析 JSON 输入 → 验证路径 → 追加写入文件。"""
        try:
            # 解析输入
            query = query.strip()
            try:
                data = json.loads(query)
            except json.JSONDecodeError:
                return "[ERROR] append_to_file: 输入格式错误，需要 JSON 格式 {\"path\": \"...\", \"content\": \"...\"}"

            path = data.get("path", "").strip()
            content = data.get("content", "")

            if not path:
                return "[ERROR] append_to_file: 缺少 path 字段"

            # 验证路径安全
            is_valid, err_msg = self._validate_path(path)
            if not is_valid:
                return f"[ERROR] append_to_file: {err_msg}"

            # 构造目标路径并创建父目录
            workspace = Path(self.workspace_path)
            target = workspace / path
            target.parent.mkdir(parents=True, exist_ok=True)

            # 以追加模式写入（UTF-8）
            with open(target, "a", encoding="utf-8") as f:
                f.write(content)

            logger.info("append_to_file success", extra={
                "path": path,
                "chars": len(content),
            })

            return f"已追加 {len(content)} 字符到 {path}"

        except Exception as exc:
            logger.error("append_to_file unexpected error", extra={"error": str(exc)})
            return f"[ERROR] append_to_file: {exc}"
