"""Public error-boundary regressions for document parse routes."""

import logging
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from runtime_state import RagApplicationState, create_request_stats
from routes import parse as parse_routes


@pytest.mark.asyncio
async def test_chunk_route_omits_internal_exception_body(caplog):
    private_failure_marker = "private-route-provider-body"

    class FailingUpload:
        @property
        def filename(self):
            raise RuntimeError(private_failure_marker)

    state = RagApplicationState(
        unified_service=SimpleNamespace(),
        stats=create_request_stats(),
    )

    with caplog.at_level(logging.ERROR, logger=parse_routes.__name__):
        with pytest.raises(HTTPException) as raised:
            await parse_routes.chunk_document(state=state, file=FailingUpload())

    assert raised.value.status_code == 500
    assert raised.value.detail == "文档分块失败"
    assert private_failure_marker not in caplog.text
    assert private_failure_marker not in str(raised.value)
