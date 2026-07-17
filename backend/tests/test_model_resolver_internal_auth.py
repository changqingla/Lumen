from __future__ import annotations

import pytest
from fastapi import HTTPException
from pydantic import SecretStr

from modules.model_config import controller


_TOKEN = "model-resolver-test-token-0123456789abcdef"


def test_model_resolver_internal_auth_accepts_one_exact_token(monkeypatch):
    monkeypatch.setattr(
        controller.settings,
        "MODEL_RESOLVER_INTERNAL_TOKEN",
        SecretStr(_TOKEN),
    )

    controller._verify_internal_request([_TOKEN])


@pytest.mark.parametrize("supplied", [None, [], ["wrong"], [_TOKEN, _TOKEN]])
def test_model_resolver_internal_auth_rejects_missing_wrong_or_duplicate_header(
    monkeypatch,
    supplied,
):
    monkeypatch.setattr(
        controller.settings,
        "MODEL_RESOLVER_INTERNAL_TOKEN",
        SecretStr(_TOKEN),
    )

    with pytest.raises(HTTPException) as exc_info:
        controller._verify_internal_request(supplied)

    assert exc_info.value.status_code == 401


@pytest.mark.parametrize(
    "configured",
    [
        "",
        "short",
        "replace-with-a-random-model-resolver-token",
        "non-ascii-model-resolver-token-012345-密钥",
    ],
)
def test_model_resolver_internal_auth_fails_closed_on_weak_server_token(
    monkeypatch,
    configured,
):
    monkeypatch.setattr(
        controller.settings,
        "MODEL_RESOLVER_INTERNAL_TOKEN",
        SecretStr(configured),
    )

    with pytest.raises(HTTPException) as exc_info:
        controller._verify_internal_request([_TOKEN])

    assert exc_info.value.status_code == 503
