from typing import Annotated

from langchain.tools import InjectedToolCallId, ToolRuntime, tool
from langchain_core.messages import ToolMessage
from langgraph.types import Command
from langgraph.typing import ContextT

from src.agents.thread_state import ThreadState
from src.config.paths import VIRTUAL_PATH_PREFIX, get_paths
from src.utils.image_files import (
    MAX_VIEW_IMAGE_BYTES,
    SUPPORTED_IMAGE_EXTENSIONS,
    VIEW_IMAGE_SUCCESS_MESSAGE,
    ImageFileError,
    ImageTooLargeError,
    inspect_image_file,
    resolve_image_path,
)


def _tool_message(content: str, tool_call_id: str, *, error: bool = False) -> Command:
    return Command(
        update={
            "messages": [
                ToolMessage(
                    content,
                    tool_call_id=tool_call_id,
                    status="error" if error else "success",
                )
            ]
        }
    )


@tool("view_image", parse_docstring=False)
def view_image_tool(
    runtime: ToolRuntime[ContextT, ThreadState],
    image_path: str,
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """
    使用此工具读取图片文件，并使其可用于展示。

    适用场景：
    - 需要查看单张图片文件。

    不适用场景：
    - 非图片文件（请改用 present_files）
    - 一次处理多文件（请改用 present_files）

    参数：
        image_path: `/mnt/user-data` 内的图片绝对路径。常见支持格式：jpg、jpeg、png、webp。
    """
    context = getattr(runtime, "context", None) or {}
    thread_id = str(context.get("thread_id") or "").strip()
    if not thread_id:
        return _tool_message("Error: Thread ID is required", tool_call_id, error=True)

    try:
        path = resolve_image_path(get_paths(), thread_id, image_path)
        inspect_image_file(path, max_bytes=MAX_VIEW_IMAGE_BYTES)
    except ImageTooLargeError:
        return _tool_message(
            f"Error: Image file exceeds the {MAX_VIEW_IMAGE_BYTES // (1024 * 1024)} MiB size limit: {image_path}",
            tool_call_id,
            error=True,
        )
    except ImageFileError as exc:
        detail = str(exc)
        if detail == "Unsupported image format":
            formats = ", ".join(sorted(SUPPORTED_IMAGE_EXTENSIONS))
            return _tool_message(
                f"Error: Unsupported image format. Supported formats: {formats}",
                tool_call_id,
                error=True,
            )
        if detail == "Image file not found":
            return _tool_message(f"Error: Image file not found: {image_path}", tool_call_id, error=True)
        if detail == "Image path is not a regular file":
            return _tool_message(f"Error: Path is not a file: {image_path}", tool_call_id, error=True)
        if detail.startswith("Path must be inside"):
            return _tool_message(
                f"Error: Image path must be inside the current thread's {VIRTUAL_PATH_PREFIX} directory: {image_path}",
                tool_call_id,
                error=True,
            )
        return _tool_message(f"Error reading image file: {image_path}", tool_call_id, error=True)

    # Bytes are intentionally not returned in state or ToolMessage. The
    # ViewImageMiddleware reads them into an ephemeral model request only.
    return _tool_message(VIEW_IMAGE_SUCCESS_MESSAGE, tool_call_id)
