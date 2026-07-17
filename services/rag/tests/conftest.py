from __future__ import annotations

import sys
from pathlib import Path


RAG_ROOT = Path(__file__).resolve().parents[1]
API_ROOT = RAG_ROOT / "api"
SHARED_PYTHON_ROOT = RAG_ROOT.parents[1] / "shared" / "python"

for path in (str(SHARED_PYTHON_ROOT), str(API_ROOT), str(RAG_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)
