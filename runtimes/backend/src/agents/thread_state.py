from typing import Annotated, NotRequired, TypedDict

from langchain.agents import AgentState


class SandboxState(TypedDict):
    sandbox_id: NotRequired[str | None]


class ThreadDataState(TypedDict):
    workspace_path: NotRequired[str | None]
    uploads_path: NotRequired[str | None]
    knowledge_path: NotRequired[str | None]
    outputs_path: NotRequired[str | None]


def merge_artifacts(existing: list[str] | None, new: list[str] | None) -> list[str]:
    """`artifacts` 列表的 reducer：合并并去重。"""
    if existing is None:
        return new or []
    if new is None:
        return existing
    # 使用 dict.fromkeys 去重并保持原有顺序
    return list(dict.fromkeys(existing + new))


class ThreadState(AgentState):
    sandbox: NotRequired[SandboxState | None]
    thread_data: NotRequired[ThreadDataState | None]
    title: NotRequired[str | None]
    artifacts: Annotated[list[str], merge_artifacts]
    todos: NotRequired[list | None]
    uploaded_files: NotRequired[list[dict] | None]
