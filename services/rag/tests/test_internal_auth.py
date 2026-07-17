from __future__ import annotations

import pytest
from fastapi import HTTPException

import auth


_TOKEN = "rag-internal-test-token-0123456789abcdef"


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "short",
        " replace-with-a-random-token-0123456789 ",
        "replace-with-a-random-token-0123456789",
        "non-ascii-token-0123456789abcdef-密钥",
    ],
)
def test_rag_internal_token_rejects_missing_weak_or_template_values(value):
    with pytest.raises(RuntimeError, match="RAG_INTERNAL_API_TOKEN"):
        auth.validate_rag_internal_token(value)


def test_rag_internal_token_accepts_strong_printable_ascii_value():
    assert auth.validate_rag_internal_token(_TOKEN) == _TOKEN


@pytest.mark.asyncio
async def test_internal_auth_requires_exactly_one_matching_header(monkeypatch):
    monkeypatch.setattr(auth.settings, "RAG_INTERNAL_API_TOKEN", _TOKEN)

    await auth.require_internal_token([_TOKEN])

    for supplied in (None, [], ["wrong"], [_TOKEN, _TOKEN]):
        with pytest.raises(HTTPException) as exc_info:
            await auth.require_internal_token(supplied)
        assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_internal_auth_fails_closed_when_server_token_is_invalid(monkeypatch):
    monkeypatch.setattr(
        auth.settings,
        "RAG_INTERNAL_API_TOKEN",
        "replace-with-a-random-token-0123456789",
    )

    with pytest.raises(HTTPException) as exc_info:
        await auth.require_internal_token([_TOKEN])

    assert exc_info.value.status_code == 503
