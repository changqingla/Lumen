"""Service authentication for the Runtime Gateway HTTP boundary."""

from __future__ import annotations

import secrets

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from src.gateway.config import (
    get_gateway_config,
    validate_gateway_internal_api_token,
)

GATEWAY_INTERNAL_TOKEN_HEADER = "X-Gateway-Internal-Token"
_GATEWAY_INTERNAL_TOKEN_HEADER_BYTES = GATEWAY_INTERNAL_TOKEN_HEADER.lower().encode("ascii")


def build_gateway_internal_auth_headers(token: str | None) -> dict[str, str]:
    """Build the internal authentication header without retaining caller data."""
    normalized = validate_gateway_internal_api_token(token)
    return {GATEWAY_INTERNAL_TOKEN_HEADER: normalized}


def _is_protected_api_path(path: str) -> bool:
    return path == "/api" or path.startswith("/api/")


class GatewayInternalAuthMiddleware:
    """Require the shared service token for every Gateway ``/api`` route."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http" or not _is_protected_api_path(str(scope.get("path") or "")):
            await self._app(scope, receive, send)
            return

        try:
            expected_token = get_gateway_config().internal_api_token.get_secret_value()
        except Exception:
            response = JSONResponse(
                status_code=503,
                content={"detail": "Gateway internal authentication unavailable"},
            )
            await response(scope, receive, send)
            return

        provided_values = [value.decode("latin-1") for name, value in scope.get("headers", []) if name.lower() == _GATEWAY_INTERNAL_TOKEN_HEADER_BYTES]
        provided_token = provided_values[0].strip() if len(provided_values) == 1 else ""
        if not secrets.compare_digest(provided_token, expected_token):
            response = JSONResponse(
                status_code=401,
                content={"detail": "Unauthorized internal request"},
            )
            await response(scope, receive, send)
            return

        await self._app(scope, receive, send)
