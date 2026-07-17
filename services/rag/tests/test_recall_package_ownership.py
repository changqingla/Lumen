from __future__ import annotations

import ast
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_rag_uses_canonical_shared_recall_package():
    common_utils = REPO_ROOT / "services" / "rag" / "api" / "common_utils.py"
    chunk_store = REPO_ROOT / "services" / "rag" / "embed_store" / "chunk_store.py"
    recall_route = REPO_ROOT / "services" / "rag" / "api" / "routes" / "recall.py"

    common_utils_tree = ast.parse(common_utils.read_text(encoding="utf-8"))
    assert any(
        isinstance(node, ast.ImportFrom)
        and node.module == "recall_lib"
        and {alias.name for alias in node.names}
        >= {"DeepRagPureRetriever", "DeepRagRetrievalConfig"}
        for node in ast.walk(common_utils_tree)
    )

    chunk_store_tree = ast.parse(chunk_store.read_text(encoding="utf-8"))
    assert any(
        isinstance(node, ast.ImportFrom)
        and node.module == "recall_lib"
        and {alias.name for alias in node.names} >= {"SimpleESConnection"}
        for node in ast.walk(chunk_store_tree)
    )

    recall_route_tree = ast.parse(recall_route.read_text(encoding="utf-8"))
    assert any(
        isinstance(node, ast.ImportFrom)
        and node.module == "recall_lib"
        and {alias.name for alias in node.names}
        >= {"create_embedding_model", "create_rerank_model"}
        for node in ast.walk(recall_route_tree)
    )
    duplicated_factories = {
        node.name
        for node in ast.walk(common_utils_tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "create_embedding_model" not in duplicated_factories
    assert "create_rerank_model" not in duplicated_factories


def test_rag_does_not_carry_a_second_recall_implementation():
    assert not (REPO_ROOT / "services" / "rag" / "recall").exists()
    assert not (
        REPO_ROOT / "services" / "rag" / "embed_store" / "es_connection.py"
    ).exists()


def test_compose_mounts_canonical_recall_package_read_only():
    compose_path = REPO_ROOT / "docker" / "docker-compose.yml"
    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    rag_service = compose["services"]["rag"]

    assert "/workspace/shared/python" in rag_service["environment"]["PYTHONPATH"]
    assert (
        "../shared/python:/workspace/shared/python:ro" in rag_service["volumes"]
    )
