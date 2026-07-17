import re

from langchain.tools import ToolRuntime, tool
from langgraph.typing import ContextT

from src.agents.thread_state import ThreadDataState, ThreadState
from src.config.paths import VIRTUAL_PATH_PREFIX
from src.sandbox.exceptions import (
    SandboxError,
    SandboxNotFoundError,
    SandboxRuntimeError,
)
from src.sandbox.sandbox import Sandbox
from src.sandbox.sandbox_provider import get_sandbox_provider


def replace_virtual_path(path: str, thread_data: ThreadDataState | None) -> str:
    """
    映射规则：
        /mnt/user-data/workspace/* -> thread_data['workspace_path']/*
        /mnt/user-data/uploads/* -> thread_data['uploads_path']/*
        /mnt/user-data/knowledge/* -> thread_data['knowledge_path']/*
        /mnt/user-data/outputs/* -> thread_data['outputs_path']/*

    参数：
        path: 可能包含虚拟路径前缀的路径。
        thread_data: 包含真实路径的线程数据。

    返回：
        将虚拟前缀替换为真实路径后的结果。
    """
    if not path.startswith(VIRTUAL_PATH_PREFIX):
        return path

    if thread_data is None:
        return path

    # 将虚拟子目录映射到 thread_data 对应键
    path_mapping = {
        "workspace": thread_data.get("workspace_path"),
        "uploads": thread_data.get("uploads_path"),
        "knowledge": thread_data.get("knowledge_path"),
        "outputs": thread_data.get("outputs_path"),
    }

    # 提取 /mnt/user-data/ 之后的子目录
    relative_path = path[len(VIRTUAL_PATH_PREFIX) :].lstrip("/")
    if not relative_path:
        return path

    # 判断该路径属于哪个子目录
    parts = relative_path.split("/", 1)
    subdir = parts[0]
    rest = parts[1] if len(parts) > 1 else ""

    actual_base = path_mapping.get(subdir)
    if actual_base is None:
        return path

    if rest:
        return f"{actual_base}/{rest}"
    return actual_base


def replace_virtual_paths_in_command(command: str, thread_data: ThreadDataState | None) -> str:
    """
    参数：
        command: 可能包含虚拟路径的命令字符串。
        thread_data: 包含真实路径的线程数据。

    返回：
        已替换全部虚拟路径的命令字符串。
    """
    if VIRTUAL_PATH_PREFIX not in command:
        return command

    if thread_data is None:
        return command

    # 匹配 /mnt/user-data 后接路径字符的模式
    pattern = re.compile(rf"{re.escape(VIRTUAL_PATH_PREFIX)}(/[^\s\"';&|<>()]*)?")

    def replace_match(match: re.Match) -> str:
        full_path = match.group(0)
        return replace_virtual_path(full_path, thread_data)

    return pattern.sub(replace_match, command)


def restore_virtual_paths_in_output(
    output: str,
    thread_data: ThreadDataState | None,
) -> str:
    """Replace local thread-directory paths with their sandbox-visible names."""

    if thread_data is None:
        return output
    mappings = {
        f"{VIRTUAL_PATH_PREFIX}/workspace": thread_data.get("workspace_path"),
        f"{VIRTUAL_PATH_PREFIX}/uploads": thread_data.get("uploads_path"),
        f"{VIRTUAL_PATH_PREFIX}/knowledge": thread_data.get("knowledge_path"),
        f"{VIRTUAL_PATH_PREFIX}/outputs": thread_data.get("outputs_path"),
    }
    restored = output
    ordered = sorted(
        (
            (str(actual), virtual)
            for virtual, actual in mappings.items()
            if actual
        ),
        key=lambda item: len(item[0]),
        reverse=True,
    )
    for actual, virtual in ordered:
        restored = restored.replace(actual, virtual)
    return restored


def get_thread_data(runtime: ToolRuntime[ContextT, ThreadState] | None) -> ThreadDataState | None:
    """从 runtime state 中提取 thread_data。"""
    if runtime is None:
        return None
    if runtime.state is None:
        return None
    return runtime.state.get("thread_data")


def is_local_sandbox(runtime: ToolRuntime[ContextT, ThreadState] | None) -> bool:
    """
    仅本地沙箱需要路径替换；aio 沙箱容器内已挂载 /mnt/user-data。

    """
    if runtime is None:
        return False
    if runtime.state is None:
        return False
    sandbox_state = runtime.state.get("sandbox")
    if sandbox_state is None:
        return False
    return sandbox_state.get("sandbox_id") == "local"


def ensure_sandbox_initialized(runtime: ToolRuntime[ContextT, ThreadState] | None = None) -> Sandbox:
    """
    首次调用时，从 provider 获取沙箱并写入 runtime state；
    后续调用复用已有沙箱。

    线程安全由 provider 内部锁机制保证。

    参数：
        runtime: 包含 state 与 context 的工具运行时。

    返回：
        已初始化的沙箱实例。

    异常：
        SandboxRuntimeError: runtime 不可用或缺少 thread_id。
        SandboxNotFoundError: 沙箱获取失败。
    """
    if runtime is None:
        raise SandboxRuntimeError("Tool runtime not available")

    if runtime.state is None:
        raise SandboxRuntimeError("Tool runtime state not available")

    # 检查 state 中是否已存在沙箱
    sandbox_state = runtime.state.get("sandbox")
    if sandbox_state is not None:
        sandbox_id = sandbox_state.get("sandbox_id")
        if sandbox_id is not None:
            sandbox = get_sandbox_provider().get(sandbox_id)
            if sandbox is not None:
                runtime.context["sandbox_id"] = sandbox_id  # 确保 context 中有 sandbox_id，供 after_agent 释放
                return sandbox
            # 沙箱可能已释放，继续走新建流程

    # 懒加载获取：读取 thread_id 并获取沙箱
    thread_id = runtime.context.get("thread_id")
    if thread_id is None:
        raise SandboxRuntimeError("Thread ID not available in runtime context")

    provider = get_sandbox_provider()
    sandbox_id = provider.acquire(thread_id)

    # 更新 runtime state，该状态会跨多次工具调用保留
    runtime.state["sandbox"] = {"sandbox_id": sandbox_id}

    # 获取并返回沙箱实例
    sandbox = provider.get(sandbox_id)
    if sandbox is None:
        raise SandboxNotFoundError("Sandbox not found after acquisition", sandbox_id=sandbox_id)

    runtime.context["sandbox_id"] = sandbox_id  # 确保 context 中有 sandbox_id，供 after_agent 释放
    return sandbox


def ensure_thread_directories_exist(runtime: ToolRuntime[ContextT, ThreadState] | None) -> None:
    """
    该函数在首次使用任一沙箱工具时懒执行。
    对本地沙箱：在本地文件系统创建目录；
    对其他沙箱（如 aio）：目录通常已在容器中挂载。

    参数：
        runtime: 包含 state 与 context 的工具运行时。
    """
    if runtime is None:
        return

    # 仅本地沙箱需要创建目录
    if not is_local_sandbox(runtime):
        return

    thread_data = get_thread_data(runtime)
    if thread_data is None:
        return

    # 检查目录是否已创建过
    if runtime.state.get("thread_directories_created"):
        return

    # 创建三个标准目录
    import os

    for key in ["workspace_path", "uploads_path", "knowledge_path", "outputs_path"]:
        path = thread_data.get(key)
        if path:
            os.makedirs(path, exist_ok=True)

    # 标记为已创建，避免重复操作
    runtime.state["thread_directories_created"] = True


@tool("bash", parse_docstring=False)
def bash_tool(runtime: ToolRuntime[ContextT, ThreadState], description: str, command: str) -> str:
    """
    - 使用 `python` 运行 Python 代码。
    - 使用 `pip install` 安装 Python 包。

    参数：
        description: 请用简短语句说明执行该命令的原因。必须第一个提供此参数。
        command: 要执行的 bash 命令。文件和目录请始终使用绝对路径。
    """
    try:
        sandbox = ensure_sandbox_initialized(runtime)
        ensure_thread_directories_exist(runtime)
        thread_data = None
        if is_local_sandbox(runtime):
            thread_data = get_thread_data(runtime)
            command = replace_virtual_paths_in_command(command, thread_data)
        output = sandbox.execute_command(command)
        return restore_virtual_paths_in_output(output, thread_data)
    except SandboxError as exc:
        return f"Error: {exc.message}"
    except Exception as exc:
        return f"Error: Unexpected error executing command ({type(exc).__name__})"


@tool("ls", parse_docstring=False)
def ls_tool(runtime: ToolRuntime[ContextT, ThreadState], description: str, path: str) -> str:
    """
    参数：
        description: 请用简短语句说明为何要列出该目录。必须第一个提供此参数。
        path: 要列出的目录**绝对路径**。
    """
    requested_path = path
    try:
        sandbox = ensure_sandbox_initialized(runtime)
        ensure_thread_directories_exist(runtime)
        thread_data = None
        if is_local_sandbox(runtime):
            thread_data = get_thread_data(runtime)
            path = replace_virtual_path(path, thread_data)
        children = sandbox.list_dir(path)
        if not children:
            return "(empty)"
        return restore_virtual_paths_in_output("\n".join(children), thread_data)
    except SandboxError as exc:
        return f"Error: {exc.message}"
    except FileNotFoundError:
        return f"Error: Directory not found: {requested_path}"
    except PermissionError:
        return f"Error: Permission denied: {requested_path}"
    except Exception as exc:
        return f"Error: Unexpected error listing directory ({type(exc).__name__})"


@tool("read_file", parse_docstring=False)
def read_file_tool(
    runtime: ToolRuntime[ContextT, ThreadState],
    description: str,
    path: str,
    start_line: int | None = None,
    end_line: int | None = None,
) -> str:
    """
    参数：
        description: 请用简短语句说明为何要读取该文件。必须第一个提供此参数。
        path: 要读取文件的**绝对路径**。
        start_line: 可选起始行号（从 1 开始，含当前行）。与 end_line 配合可读取指定区间。
        end_line: 可选结束行号（从 1 开始，含当前行）。与 start_line 配合可读取指定区间。
    """
    requested_path = path
    try:
        sandbox = ensure_sandbox_initialized(runtime)
        ensure_thread_directories_exist(runtime)
        thread_data = None
        if is_local_sandbox(runtime):
            thread_data = get_thread_data(runtime)
            path = replace_virtual_path(path, thread_data)
        content = sandbox.read_file(path)
        if not content:
            return "(empty)"
        if start_line is not None and end_line is not None:
            content = "\n".join(content.splitlines()[start_line - 1 : end_line])
        return restore_virtual_paths_in_output(content, thread_data)
    except SandboxError as exc:
        return f"Error: {exc.message}"
    except FileNotFoundError:
        return f"Error: File not found: {requested_path}"
    except PermissionError:
        return f"Error: Permission denied reading file: {requested_path}"
    except IsADirectoryError:
        return f"Error: Path is a directory, not a file: {requested_path}"
    except Exception as exc:
        return f"Error: Unexpected error reading file ({type(exc).__name__})"


@tool("write_file", parse_docstring=False)
def write_file_tool(
    runtime: ToolRuntime[ContextT, ThreadState],
    description: str,
    path: str,
    content: str,
    append: bool = False,
) -> str:
    """
    参数：
        description: 请用简短语句说明为何要写入该文件。必须第一个提供此参数。
        path: 要写入文件的**绝对路径**。必须第二个提供此参数。
        content: 写入文件的内容。必须第三个提供此参数。
    """
    requested_path = path
    try:
        sandbox = ensure_sandbox_initialized(runtime)
        ensure_thread_directories_exist(runtime)
        if is_local_sandbox(runtime):
            thread_data = get_thread_data(runtime)
            path = replace_virtual_path(path, thread_data)
        sandbox.write_file(path, content, append)
        return "OK"
    except SandboxError as exc:
        return f"Error: {exc.message}"
    except PermissionError:
        return f"Error: Permission denied writing to file: {requested_path}"
    except IsADirectoryError:
        return f"Error: Path is a directory, not a file: {requested_path}"
    except OSError:
        return f"Error: Failed to write file: {requested_path}"
    except Exception as exc:
        return f"Error: Unexpected error writing file ({type(exc).__name__})"


@tool("str_replace", parse_docstring=False)
def str_replace_tool(
    runtime: ToolRuntime[ContextT, ThreadState],
    description: str,
    path: str,
    old_str: str,
    new_str: str,
    replace_all: bool = False,
) -> str:
    """
    当 `replace_all` 为 False（默认）时，待替换子串必须在文件中**恰好出现一次**。

    参数：
        description: 请用简短语句说明为何要替换该子串。必须第一个提供此参数。
        path: 要执行子串替换的文件**绝对路径**。必须第二个提供此参数。
        old_str: 待替换子串。必须第三个提供此参数。
        new_str: 新子串。必须第四个提供此参数。
        replace_all: 是否替换全部匹配项。若为 False，仅替换第一个匹配项。默认 False。
    """
    requested_path = path
    try:
        sandbox = ensure_sandbox_initialized(runtime)
        ensure_thread_directories_exist(runtime)
        if is_local_sandbox(runtime):
            thread_data = get_thread_data(runtime)
            path = replace_virtual_path(path, thread_data)
        content = sandbox.read_file(path)
        if not content:
            return "OK"
        if old_str not in content:
            return f"Error: String to replace not found in file: {requested_path}"
        if replace_all:
            content = content.replace(old_str, new_str)
        else:
            content = content.replace(old_str, new_str, 1)
        sandbox.write_file(path, content)
        return "OK"
    except SandboxError as exc:
        return f"Error: {exc.message}"
    except FileNotFoundError:
        return f"Error: File not found: {requested_path}"
    except PermissionError:
        return f"Error: Permission denied accessing file: {requested_path}"
    except Exception as exc:
        return f"Error: Unexpected error replacing string ({type(exc).__name__})"
