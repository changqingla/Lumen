"""Request-scoped access to the RAG application's lifespan-owned services."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any, Protocol, TypedDict

from fastapi import Depends, Request


class RequestStats(TypedDict):
    start_time: datetime
    total_requests: int
    successful_requests: int
    failed_requests: int
    chunk_requests: int
    embedding_requests: int
    store_requests: int


class UnifiedServiceProtocol(Protocol):
    chunk_edit_service: Any
    document_parse_service: Any

    def detect_parser_type(self, filename: str) -> str: ...

    async def process_chunk(
        self,
        file_content: bytes,
        filename: str,
        request: Any,
    ) -> dict[str, Any]: ...

    async def process_embedding(
        self,
        chunks: list[dict[str, Any]],
        request: Any,
    ) -> dict[str, Any]: ...

    async def process_store(
        self,
        chunks: list[dict[str, Any]],
        request: Any,
    ) -> dict[str, Any]: ...

    async def process_document_delete(self, request: Any) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class RagApplicationState:
    unified_service: UnifiedServiceProtocol
    stats: RequestStats


def create_request_stats() -> RequestStats:
    """Create counters when the application actually starts serving."""
    return {
        "start_time": datetime.now(),
        "total_requests": 0,
        "successful_requests": 0,
        "failed_requests": 0,
        "chunk_requests": 0,
        "embedding_requests": 0,
        "store_requests": 0,
    }


def bind_rag_application_state(app: Any, state: RagApplicationState) -> None:
    if getattr(app.state, "rag_runtime", None) is not None:
        raise RuntimeError("RAG application state is already initialized")
    app.state.rag_runtime = state


def clear_rag_application_state(app: Any, state: RagApplicationState) -> None:
    if getattr(app.state, "rag_runtime", None) is state:
        del app.state.rag_runtime


def get_rag_application_state(request: Request) -> RagApplicationState:
    state = getattr(request.app.state, "rag_runtime", None)
    if not isinstance(state, RagApplicationState):
        raise RuntimeError("RAG application state is not initialized")
    return state


RagStateDependency = Annotated[
    RagApplicationState,
    Depends(get_rag_application_state),
]
