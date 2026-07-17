"""Resolve the RAG core dependency in source and container layouts."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def resolve_rag_root() -> Path:
    configured = os.getenv("LUMEN_RAG_ROOT", "").strip()
    package_dir = Path(__file__).resolve().parent
    candidates = [
        Path(configured) if configured else None,
        package_dir.parents[2] / "services" / "rag",
        Path("/workspace/rag"),
        Path("/app/services/rag"),
    ]

    for candidate in candidates:
        if candidate is not None and (candidate / "core").is_dir():
            return candidate

    searched = ", ".join(str(path) for path in candidates if path is not None)
    raise ImportError(
        "recall_lib requires the RAG core package; set LUMEN_RAG_ROOT or mount "
        f"one of: {searched}"
    )


def ensure_rag_root_on_path() -> Path:
    rag_root = resolve_rag_root()
    normalized = str(rag_root)
    if normalized not in sys.path:
        sys.path.insert(0, normalized)
    return rag_root
