from __future__ import annotations

import importlib.util
from pathlib import Path


def test_shared_recall_lib_resolves_repo_rag_core():
    repo_root = Path(__file__).resolve().parents[3]
    module_path = repo_root / "shared" / "python" / "recall_lib" / "_paths.py"
    spec = importlib.util.spec_from_file_location("recall_lib_paths_for_test", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    assert module.resolve_rag_root() == repo_root / "services" / "rag"
