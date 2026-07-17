"""Logging privacy regressions for the Jina integration."""

import logging
from unittest.mock import MagicMock

from src.community.jina_ai.jina_client import JinaClient


def test_error_response_body_is_not_logged(monkeypatch, caplog):
    private_response_marker = "private-jina-response"
    response = MagicMock(
        status_code=502,
        text=private_response_marker,
        content=private_response_marker.encode(),
    )
    monkeypatch.setattr(
        "src.community.jina_ai.jina_client.provider_post",
        lambda *_args, **_kwargs: response,
    )

    with caplog.at_level(logging.ERROR):
        result = JinaClient().crawl("https://example.com")

    assert private_response_marker not in result
    assert result == "Error: Jina API returned status 502"
    assert private_response_marker not in caplog.text
    assert "status=502" in caplog.text


def test_request_exception_body_is_not_returned_or_logged(monkeypatch, caplog):
    marker = "private-jina-provider-error"

    def fail(*_args, **_kwargs):
        raise RuntimeError(marker)

    monkeypatch.setattr(
        "src.community.jina_ai.jina_client.provider_post",
        fail,
    )

    with caplog.at_level(logging.ERROR):
        result = JinaClient().crawl("https://example.com")

    assert result == "Error: Jina API request failed"
    assert marker not in result
    assert marker not in caplog.text
    assert "RuntimeError" in caplog.text
