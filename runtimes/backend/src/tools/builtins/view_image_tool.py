import base64
import mimetypes
from pathlib import Path
from typing import Annotated

from langchain.tools import InjectedToolCallId, ToolRuntime, tool
from langchain_core.messages import ToolMessage
from langgraph.types import Command
from langgraph.typing import ContextT

from src.agents.thread_state import ThreadState
from src.sandbox.tools import get_thread_data, replace_virtual_path


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
        image_path: 图片文件绝对路径。常见支持格式：jpg、jpeg、png、webp。
    """
    # 将虚拟路径替换为真实路径
    # /mnt/user-data/* 会映射到线程对应目录
    thread_data = get_thread_data(runtime)
    actual_path = replace_virtual_path(image_path, thread_data)

    # 校验路径必须为绝对路径
    path = Path(actual_path)
    if not path.is_absolute():
        return Command(
            update={"messages": [ToolMessage(f"Error: Path must be absolute, got: {image_path}", tool_call_id=tool_call_id)]},
        )

    # 校验文件存在
    if not path.exists():
        return Command(
            update={"messages": [ToolMessage(f"Error: Image file not found: {image_path}", tool_call_id=tool_call_id)]},
        )

    # 校验为文件而非目录
    if not path.is_file():
        return Command(
            update={"messages": [ToolMessage(f"Error: Path is not a file: {image_path}", tool_call_id=tool_call_id)]},
        )

    # 校验图片扩展名
    valid_extensions = {".jpg", ".jpeg", ".png", ".webp"}
    if path.suffix.lower() not in valid_extensions:
        return Command(
            update={"messages": [ToolMessage(f"Error: Unsupported image format: {path.suffix}. Supported formats: {', '.join(valid_extensions)}", tool_call_id=tool_call_id)]},
        )

    # 根据扩展名推断 MIME 类型
    mime_type, _ = mimetypes.guess_type(actual_path)
    if mime_type is None:
        # 常见图片格式的默认 MIME 兜底
        extension_to_mime = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
        }
        mime_type = extension_to_mime.get(path.suffix.lower(), "application/octet-stream")

    # 读取图片并编码为 base64
    try:
        with open(actual_path, "rb") as f:
            image_data = f.read()
            image_base64 = base64.b64encode(image_data).decode("utf-8")
    except Exception as e:
        return Command(
            update={"messages": [ToolMessage(f"Error reading image file: {str(e)}", tool_call_id=tool_call_id)]},
        )

    # 更新状态中的 `viewed_images`
    # `merge_viewed_images` 归并器会与已有图片记录进行合并
    new_viewed_images = {image_path: {"base64": image_base64, "mime_type": mime_type}}

    return Command(
        update={"viewed_images": new_viewed_images, "messages": [ToolMessage("Successfully read image", tool_call_id=tool_call_id)]},
    )
