"""Inject only the current tenant's long-term memory into model requests."""

from __future__ import annotations

from typing import override

from langchain.agents.middleware import AgentMiddleware, ModelRequest
from langchain_core.messages import SystemMessage

from src.agents.lead_agent.prompt import _get_memory_context
from src.agents.memory.scope import normalize_agent_name, normalize_memory_scope


class ScopedMemoryPromptMiddleware(AgentMiddleware):
    """Build memory prompt content per run instead of caching it in the graph."""

    def __init__(self, *, agent_name: str | None = None):
        super().__init__()
        self._agent_name = normalize_agent_name(agent_name)

    def _inject(self, request: ModelRequest) -> ModelRequest:
        scope = normalize_memory_scope(
            request.runtime.context.get("memory_scope"),
            allow_none=True,
        )
        if scope is None:
            return request

        memory_context = _get_memory_context(
            memory_scope=scope,
            agent_name=self._agent_name,
        )
        if not memory_context:
            return request

        if request.system_message is None:
            system_message = SystemMessage(content=memory_context)
        else:
            base_content = request.system_message.content
            if not isinstance(base_content, str):
                raise TypeError("lead agent system prompt must be text")
            system_message = request.system_message.model_copy(update={"content": f"{base_content}\n\n{memory_context}"})
        return request.override(system_message=system_message)

    @override
    def wrap_model_call(self, request: ModelRequest, handler):
        return handler(self._inject(request))

    @override
    async def awrap_model_call(self, request: ModelRequest, handler):
        return await handler(self._inject(request))
