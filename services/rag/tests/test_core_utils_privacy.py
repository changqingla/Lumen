"""Logging privacy regressions for shared RAG provider failures."""

import logging
import sys
from enum import StrEnum
from importlib.util import find_spec
from types import ModuleType

import pytest

if find_spec("strenum") is None:
    strenum_module = ModuleType("strenum")
    strenum_module.StrEnum = StrEnum
    sys.modules["strenum"] = strenum_module

from core.utils import log_exception  # noqa: E402


def test_provider_response_and_exception_details_are_not_logged(caplog):
    private_exception_marker = "private-provider-exception"
    private_response_marker = "private-provider-response"

    class ProviderResponse:
        status_code = 502
        text = private_response_marker

    with caplog.at_level(logging.ERROR):
        with pytest.raises(RuntimeError, match="RAG model provider operation failed"):
            log_exception(ValueError(private_exception_marker), ProviderResponse())

    assert private_exception_marker not in caplog.text
    assert private_response_marker not in caplog.text
    assert "response_statuses=[502]" in caplog.text
