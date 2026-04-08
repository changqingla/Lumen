"""将上传文件信息注入 agent 上下文的中间件。"""

import logging
from pathlib import Path
from typing import NotRequired, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage
from langgraph.runtime import Runtime

from src.config.paths import Paths, get_paths

logger = logging.getLogger(__name__)


class UploadsMiddlewareState(AgentState):
    """上传中间件（Uploads）状态模式。"""

    uploaded_files: NotRequired[list[dict] | None]


class UploadsMiddleware(AgentMiddleware[UploadsMiddlewareState]):
    """将上传文件列表注入到最后一条 human 消息前缀中。

    该中间件会从当前消息的 `additional_kwargs.files` 读取文件元数据
    （由前端在上传后写入），并在最后一条 human 消息前追加
    `<uploaded_files>` 区块，让模型感知可用文件。
    """

    state_schema = UploadsMiddlewareState

    def __init__(self, base_dir: str | None = None):
        """初始化上传中间件。

        参数：
            base_dir: 线程数据根目录；若不传则使用 `Paths` 默认解析。
        """
        super().__init__()
        self._paths = Paths(base_dir) if base_dir else get_paths()

    def _create_files_message(self, new_files: list[dict], historical_files: list[dict]) -> str:
        """构建 `<uploaded_files>` 内容块文本。

        参数：
            new_files: 当前消息中新上传的文件。
            historical_files: 历史消息中上传且仍可用的文件。

        返回：
            `<uploaded_files>` 标签包裹的格式化字符串。
        """
        lines = ["<uploaded_files>"]

        lines.append("The following files were uploaded in this message:")
        lines.append("")
        if new_files:
            for file in new_files:
                size_kb = file["size"] / 1024
                size_str = f"{size_kb:.1f} KB" if size_kb < 1024 else f"{size_kb / 1024:.1f} MB"
                lines.append(f"- {file['filename']} ({size_str})")
                lines.append(f"  Path: {file['path']}")
                lines.append("")
        else:
            lines.append("(empty)")

        if historical_files:
            lines.append("The following files were uploaded in previous messages and are still available:")
            lines.append("")
            for file in historical_files:
                size_kb = file["size"] / 1024
                size_str = f"{size_kb:.1f} KB" if size_kb < 1024 else f"{size_kb / 1024:.1f} MB"
                lines.append(f"- {file['filename']} ({size_str})")
                lines.append(f"  Path: {file['path']}")
                lines.append("")

        lines.append("You can read these files using the `read_file` tool with the paths shown above.")
        lines.append("</uploaded_files>")

        return "\n".join(lines)

    def _files_from_kwargs(self, message: HumanMessage, uploads_dir: Path | None = None) -> list[dict] | None:
        """从消息 `additional_kwargs.files` 解析文件列表。

        前端在上传成功后会将文件元数据写入 `additional_kwargs.files`，
        每个条目通常包含：`filename`、`size(bytes)`、`path(virtual)`、`status`。

        参数：
            message: 待检查的 human 消息。
            uploads_dir: 物理上传目录（用于校验文件是否仍存在）。
                若提供，则已不存在的条目会被跳过。

        返回：
            带虚拟路径的文件字典列表；若字段缺失或为空则返回 None。
        """
        kwargs_files = (message.additional_kwargs or {}).get("files")
        if not isinstance(kwargs_files, list) or not kwargs_files:
            return None

        files = []
        for f in kwargs_files:
            if not isinstance(f, dict):
                continue
            filename = f.get("filename") or ""
            if not filename or Path(filename).name != filename:
                continue
            if uploads_dir is not None and not (uploads_dir / filename).is_file():
                continue
            files.append(
                {
                    "filename": filename,
                    "size": int(f.get("size") or 0),
                    "path": f"/mnt/user-data/uploads/{filename}",
                    "extension": Path(filename).suffix,
                }
            )
        return files if files else None

    @override
    def before_agent(self, state: UploadsMiddlewareState, runtime: Runtime) -> dict | None:
        """在进入 agent 前注入上传文件上下文。

        新上传文件来自当前消息的 `additional_kwargs.files`；
        历史文件来自线程 uploads 目录（排除本轮新文件）。

        会将 `<uploaded_files>` 区块前置到最后一条 human 消息内容中。
        同时保留原始 `additional_kwargs`（含文件元数据），便于前端
        从流式消息中读取结构化文件信息。

        参数：
            state: 当前 agent 状态。
            runtime: 运行时上下文（包含 thread_id）。

        返回：
            包含上传文件列表与消息更新的状态增量。
        """
        messages = list(state.get("messages", []))
        if not messages:
            return None

        last_message_index = len(messages) - 1
        last_message = messages[last_message_index]

        if not isinstance(last_message, HumanMessage):
            return None

        # 解析 uploads 目录，用于文件存在性校验
        thread_id = runtime.context.get("thread_id")
        uploads_dir = self._paths.sandbox_uploads_dir(thread_id) if thread_id else None

        # 从当前消息 additional_kwargs.files 获取本轮新上传文件
        new_files = self._files_from_kwargs(last_message, uploads_dir) or []

        # 从 uploads 目录收集历史文件（排除本轮新文件）
        new_filenames = {f["filename"] for f in new_files}
        historical_files: list[dict] = []
        if uploads_dir and uploads_dir.exists():
            for file_path in sorted(uploads_dir.iterdir()):
                if file_path.is_file() and file_path.name not in new_filenames:
                    stat = file_path.stat()
                    historical_files.append(
                        {
                            "filename": file_path.name,
                            "size": stat.st_size,
                            "path": f"/mnt/user-data/uploads/{file_path.name}",
                            "extension": file_path.suffix,
                        }
                    )

        if not new_files and not historical_files:
            return None

        logger.debug(f"New files: {[f['filename'] for f in new_files]}, historical: {[f['filename'] for f in historical_files]}")

        # 生成文件说明并前置到最后一条 human 消息内容
        files_message = self._create_files_message(new_files, historical_files)

        # 提取原始内容（兼容字符串与列表两种格式）
        original_content = ""
        if isinstance(last_message.content, str):
            original_content = last_message.content
        elif isinstance(last_message.content, list):
            text_parts = []
            for block in last_message.content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
            original_content = "\n".join(text_parts)

        # 构造合并后的新消息。
        # 保留 additional_kwargs（含文件元数据），便于前端在流式消息中读取。
        updated_message = HumanMessage(
            content=f"{files_message}\n\n{original_content}",
            id=last_message.id,
            additional_kwargs=last_message.additional_kwargs,
        )

        messages[last_message_index] = updated_message

        return {
            "uploaded_files": new_files,
            "messages": messages,
        }
