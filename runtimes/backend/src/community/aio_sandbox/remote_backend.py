"""Authenticated client for the constrained sandbox provisioner API."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import requests

from src.sandbox_provisioner.contract import (
    INTERNAL_TOKEN_ENV,
    INTERNAL_TOKEN_HEADER,
    MAX_RESPONSE_BYTES,
    validate_internal_token,
    validate_provisioner_url,
    validate_sandbox_binding,
    validate_sandbox_id,
    validate_sandbox_response,
)

from .backend import SandboxBackend
from .sandbox_info import SandboxInfo

logger = logging.getLogger(__name__)


class RemoteSandboxBackend(SandboxBackend):
    """Delegate lifecycle operations to an authenticated provisioner."""

    def __init__(self, provisioner_url: str, internal_token: str | None = None):
        self._provisioner_url = validate_provisioner_url(provisioner_url)
        token = validate_internal_token(
            internal_token
            if internal_token is not None
            else os.environ.get(INTERNAL_TOKEN_ENV)
        )
        self._session = requests.Session()
        self._session.trust_env = False
        self._session.headers.update(
            {
                INTERNAL_TOKEN_HEADER: token,
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
        )

    @property
    def provisioner_url(self) -> str:
        return self._provisioner_url

    @property
    def session(self) -> requests.Session:
        return self._session

    def close(self) -> None:
        self._session.close()

    def create(
        self,
        thread_id: str,
        sandbox_id: str,
        extra_mounts: list[tuple[str, str, bool]] | None = None,
    ) -> SandboxInfo:
        del extra_mounts
        thread_id, sandbox_id = validate_sandbox_binding(thread_id, sandbox_id)
        data = self._request_json(
            "POST",
            "/api/sandboxes",
            timeout=30,
            json_body={"sandbox_id": sandbox_id, "thread_id": thread_id},
        )
        validated = validate_sandbox_response(data, expected_sandbox_id=sandbox_id)
        logger.info("Provisioner created or found sandbox")
        return SandboxInfo(
            sandbox_id=validated["sandbox_id"],
            provisioned_sandbox_id=validated["provisioned_sandbox_id"],
            sandbox_url=validated["sandbox_url"],
        )

    def destroy(self, info: SandboxInfo) -> None:
        sandbox_id = validate_sandbox_id(
            info.provisioned_sandbox_id or info.sandbox_id
        )
        self._request_json(
            "DELETE",
            f"/api/sandboxes/{sandbox_id}",
            timeout=15,
            allowed_statuses={200, 404},
        )
        logger.info("Provisioner destroyed sandbox")

    def is_alive(self, info: SandboxInfo) -> bool:
        sandbox_id = validate_sandbox_id(
            info.provisioned_sandbox_id or info.sandbox_id
        )
        response = self._request_json(
            "GET",
            f"/api/sandboxes/{sandbox_id}",
            timeout=10,
            allowed_statuses={200, 404},
        )
        if response is None:
            return False
        validated = validate_sandbox_response(response, expected_sandbox_id=sandbox_id)
        return validated["status"] == "Running"

    def discover(self, sandbox_id: str) -> SandboxInfo | None:
        sandbox_id = validate_sandbox_id(sandbox_id)
        response = self._request_json(
            "GET",
            f"/api/sandboxes/{sandbox_id}",
            timeout=10,
            allowed_statuses={200, 404},
        )
        if response is None:
            return None
        validated = validate_sandbox_response(response, expected_sandbox_id=sandbox_id)
        return SandboxInfo(
            sandbox_id=validated["sandbox_id"],
            provisioned_sandbox_id=validated["provisioned_sandbox_id"],
            sandbox_url=validated["sandbox_url"],
        )

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        timeout: int,
        json_body: dict[str, str] | None = None,
        allowed_statuses: set[int] | None = None,
    ) -> Any:
        expected_statuses = allowed_statuses or {200}
        response: requests.Response | None = None
        try:
            response = self._session.request(
                method,
                f"{self._provisioner_url}{path}",
                json=json_body,
                timeout=timeout,
                allow_redirects=False,
                stream=True,
            )
            if 300 <= response.status_code < 400:
                raise RuntimeError("Sandbox provisioner redirects are forbidden")
            if response.status_code not in expected_statuses:
                raise RuntimeError(
                    f"Sandbox provisioner returned HTTP {response.status_code}"
                )
            if response.status_code == 404:
                return None

            content_type = response.headers.get("Content-Type", "").lower()
            if content_type.split(";", 1)[0].strip() != "application/json":
                raise RuntimeError("Sandbox provisioner returned a non-JSON response")
            content_length = response.headers.get("Content-Length")
            if content_length is not None:
                try:
                    parsed_length = int(content_length)
                    if parsed_length < 0 or parsed_length > MAX_RESPONSE_BYTES:
                        raise RuntimeError("Sandbox provisioner response is too large")
                except ValueError as exc:
                    raise RuntimeError(
                        "Sandbox provisioner returned an invalid Content-Length"
                    ) from exc

            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_content(chunk_size=4096):
                total += len(chunk)
                if total > MAX_RESPONSE_BYTES:
                    raise RuntimeError("Sandbox provisioner response is too large")
                chunks.append(chunk)
            try:
                return json.loads(b"".join(chunks))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise RuntimeError("Sandbox provisioner returned invalid JSON") from exc
        except requests.RequestException as exc:
            raise RuntimeError("Sandbox provisioner request failed") from exc
        finally:
            if response is not None:
                response.close()
