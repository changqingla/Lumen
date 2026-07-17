"""Regression tests for vision-provider error privacy."""

from __future__ import annotations

import importlib.util
import logging
import sys
from enum import Enum
from importlib.machinery import ModuleSpec
from pathlib import Path
from types import ModuleType


RAG_ROOT = Path(__file__).resolve().parents[1]
if str(RAG_ROOT) not in sys.path:
    sys.path.insert(0, str(RAG_ROOT))

strenum_module = ModuleType("strenum")
strenum_module.__spec__ = ModuleSpec("strenum", loader=None)


class _StrEnum(str, Enum):
    pass


strenum_module.StrEnum = _StrEnum
sys.modules.setdefault("strenum", strenum_module)

zhipuai_module = ModuleType("zhipuai")
zhipuai_module.__spec__ = ModuleSpec("zhipuai", loader=None)
zhipuai_module.ZhipuAI = object
sys.modules.setdefault("zhipuai", zhipuai_module)

spec = importlib.util.spec_from_file_location(
    "rag_cv_model_under_test",
    RAG_ROOT / "core" / "llm" / "cv_model.py",
)
assert spec is not None and spec.loader is not None
cv_model = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cv_model)

Base = cv_model.Base
VISION_MODEL_ERROR = cv_model.VISION_MODEL_ERROR


class _FailingCompletions:
    def __init__(self, marker: str):
        self.marker = marker

    def create(self, **kwargs):
        del kwargs
        raise RuntimeError(self.marker)


class _FailingChat:
    def __init__(self, marker: str):
        self.completions = _FailingCompletions(marker)


class _FailingClient:
    def __init__(self, marker: str):
        self.chat = _FailingChat(marker)


def test_vision_provider_exception_is_not_returned_or_logged(caplog):
    private_marker = "private-provider-body-and-api-key"
    model = Base()
    model.client = _FailingClient(private_marker)
    model.model_name = "vision-model"

    with caplog.at_level(logging.ERROR):
        answer, token_count = model.chat("", [], {}, [])
        streamed = list(model.chat_streamly("", [], {}, []))

    assert answer == VISION_MODEL_ERROR
    assert token_count == 0
    assert streamed == [VISION_MODEL_ERROR, 0]
    assert private_marker not in answer
    assert private_marker not in "".join(str(value) for value in streamed)
    assert private_marker not in caplog.text
    assert "error_type=RuntimeError" in caplog.text
