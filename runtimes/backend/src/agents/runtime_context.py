"""Schema for trusted per-run Runtime context."""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import StringConstraints
from typing_extensions import TypedDict

from src.agents.memory.scope import MEMORY_SCOPE_PATTERN

MemoryScope = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=64,
        max_length=64,
        pattern=MEMORY_SCOPE_PATTERN,
    ),
]


class RuntimeContext(TypedDict, total=False):
    """All context fields accepted by the lead graph.

    ``memory_scope`` is additionally revalidated at the memory boundaries so
    direct in-process graph invocation cannot bypass the schema contract.
    """

    thread_id: str
    model_name: str
    thinking_enabled: bool
    is_plan_mode: bool
    subagent_enabled: bool
    disable_model_streaming: bool
    reasoning_effort: str
    max_concurrent_subagents: int
    is_bootstrap: bool
    agent_name: str
    memory_scope: MemoryScope
    usage_context: str
    dynamic_model_token: str
    sandbox_id: str
    knowledge_scope: dict[str, Any]
    kb_ids: list[str]
    doc_ids: list[str]
    kb_id: str
