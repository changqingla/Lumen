from typing import Annotated, NotRequired, TypedDict

from langchain.agents import AgentState


class SandboxState(TypedDict):
    sandbox_id: NotRequired[str | None]


class ThreadDataState(TypedDict):
    workspace_path: NotRequired[str | None]
    uploads_path: NotRequired[str | None]
    outputs_path: NotRequired[str | None]


class ViewedImageData(TypedDict):
    base64: str
    mime_type: str


def merge_artifacts(existing: list[str] | None, new: list[str] | None) -> list[str]:
    """`artifacts` 列表的 reducer：合并并去重。"""
    if existing is None:
        return new or []
    if new is None:
        return existing
    # 使用 dict.fromkeys 去重并保持原有顺序
    return list(dict.fromkeys(existing + new))


def merge_viewed_images(existing: dict[str, ViewedImageData] | None, new: dict[str, ViewedImageData] | None) -> dict[str, ViewedImageData]:
    """`viewed_images` 字典的 reducer：合并图片记录。

    特殊规则：当 `new` 是空字典 `{}` 时，表示清空已有图片记录，
    以便中间件在处理完成后重置 `viewed_images` 状态。
    """
    if existing is None:
        return new or {}
    if new is None:
        return existing
    # 特殊规则：空字典表示清空全部已查看图片
    if len(new) == 0:
        return {}
    # 合并字典；相同 key 由新值覆盖旧值
    return {**existing, **new}


class ThreadState(AgentState):
    sandbox: NotRequired[SandboxState | None]
    thread_data: NotRequired[ThreadDataState | None]
    title: NotRequired[str | None]
    artifacts: Annotated[list[str], merge_artifacts]
    todos: NotRequired[list | None]
    uploaded_files: NotRequired[list[dict] | None]
    viewed_images: Annotated[dict[str, ViewedImageData], merge_viewed_images]  # 图片路径 -> {base64, mime_type}
