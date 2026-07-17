"""Regression coverage for lifespan-owned RAG application state."""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from starlette.requests import Request

from runtime_state import (
    RagApplicationState,
    bind_rag_application_state,
    clear_rag_application_state,
    create_request_stats,
    get_rag_application_state,
)


def _request_for(app: FastAPI) -> Request:
    return Request({"type": "http", "app": app})


def _state(marker: str) -> RagApplicationState:
    return RagApplicationState(
        unified_service=SimpleNamespace(marker=marker),
        stats=create_request_stats(),
    )


def test_runtime_state_is_isolated_per_fastapi_instance():
    first_app = FastAPI()
    second_app = FastAPI()
    first_state = _state("first")
    second_state = _state("second")

    bind_rag_application_state(first_app, first_state)
    bind_rag_application_state(second_app, second_state)

    assert get_rag_application_state(_request_for(first_app)) is first_state
    assert get_rag_application_state(_request_for(second_app)) is second_state


def test_runtime_state_is_cleared_only_by_its_owner():
    app = FastAPI()
    state = _state("active")
    unrelated_state = _state("unrelated")
    bind_rag_application_state(app, state)

    clear_rag_application_state(app, unrelated_state)
    assert get_rag_application_state(_request_for(app)) is state

    clear_rag_application_state(app, state)
    with pytest.raises(RuntimeError, match="not initialized"):
        get_rag_application_state(_request_for(app))


def test_python_script_routes_do_not_reimport_app_entrypoint():
    """`python app.py` names the entrypoint `__main__`, never `app`."""
    routes_dir = Path(__file__).resolve().parents[1] / "api" / "routes"
    reverse_imports: list[str] = []

    for route_path in routes_dir.glob("*.py"):
        tree = ast.parse(route_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "app":
                reverse_imports.append(f"{route_path.name}:{node.lineno}")
            elif isinstance(node, ast.Import) and any(
                alias.name == "app" for alias in node.names
            ):
                reverse_imports.append(f"{route_path.name}:{node.lineno}")

    assert reverse_imports == []
