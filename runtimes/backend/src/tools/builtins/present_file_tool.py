from pathlib import Path
from typing import Annotated

from langchain.tools import InjectedToolCallId, ToolRuntime, tool
from langchain_core.messages import ToolMessage
from langgraph.types import Command
from langgraph.typing import ContextT

from src.agents.thread_state import ThreadState
from src.config.paths import VIRTUAL_PATH_PREFIX, get_paths

OUTPUTS_VIRTUAL_PREFIX = f"{VIRTUAL_PATH_PREFIX}/outputs"


def _normalize_presented_filepath(
    runtime: ToolRuntime[ContextT, ThreadState],
    filepath: str,
) -> str:
    """
    接受以下两类路径：
    - 沙箱虚拟路径，例如 `/mnt/user-data/outputs/report.md`
    - 线程输出目录的宿主机路径，例如
      `/app/state/threads/<thread>/user-data/outputs/report.md`

    返回：
        归一化后的虚拟路径。

    异常：
        ValueError: 当 runtime 元数据缺失，或路径不在当前线程 outputs 目录内时抛出。
    """
    if runtime.state is None:
        raise ValueError("Thread runtime state is not available")

    thread_id = runtime.context.get("thread_id")
    if not thread_id:
        raise ValueError("Thread ID is not available in runtime context")

    thread_data = runtime.state.get("thread_data") or {}
    outputs_path = thread_data.get("outputs_path")
    if not outputs_path:
        raise ValueError("Thread outputs path is not available in runtime state")

    outputs_dir = Path(outputs_path).resolve()
    stripped = filepath.lstrip("/")
    virtual_prefix = VIRTUAL_PATH_PREFIX.lstrip("/")

    if stripped == virtual_prefix or stripped.startswith(virtual_prefix + "/"):
        actual_path = get_paths().resolve_virtual_path(thread_id, filepath)
    else:
        actual_path = Path(filepath).expanduser().resolve()

    try:
        relative_path = actual_path.relative_to(outputs_dir)
    except ValueError as exc:
        raise ValueError(f"Only files in {OUTPUTS_VIRTUAL_PREFIX} can be presented: {filepath}") from exc

    if not actual_path.exists():
        raise ValueError(f"File does not exist and cannot be presented: {filepath}")

    if not actual_path.is_file():
        raise ValueError(f"Only regular files can be presented: {filepath}")

    return f"{OUTPUTS_VIRTUAL_PREFIX}/{relative_path.as_posix()}"


@tool("present_files", parse_docstring=False)
def present_file_tool(
    runtime: ToolRuntime[ContextT, ThreadState],
    filepaths: list[str],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """
    `present_files` 工具适用场景：
    - 将文件提供给用户查看、下载或交互
    - 一次呈现多个相关文件
    - 在文件生成完成后展示给用户

    不适用场景：
    - 仅需读取文件内容供内部处理
    - 临时或中间文件，不需要展示给用户

    说明：
    - 应在创建文件并移动到 `/mnt/user-data/outputs` 后调用。
    - 本工具可与其他工具并行调用；状态更新通过 reducer 合并，避免冲突。

    参数：
        filepaths: 需要展示给用户的绝对路径列表。仅允许 `/mnt/user-data/outputs` 下文件。
    """
    try:
        normalized_paths = [_normalize_presented_filepath(runtime, filepath) for filepath in filepaths]
    except ValueError as exc:
        return Command(
            update={"messages": [ToolMessage(f"Error: {exc}", tool_call_id=tool_call_id)]},
        )

    filenames = ", ".join(Path(path).name for path in normalized_paths)

    # `merge_artifacts` 归并器会负责合并与去重
    return Command(
        update={
            "artifacts": normalized_paths,
            "messages": [ToolMessage(f"Presented files: {filenames}", tool_call_id=tool_call_id)],
        },
    )
