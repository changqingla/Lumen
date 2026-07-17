from __future__ import annotations

import importlib
import json
import sys
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest
import requests

agent_sandbox_stub = ModuleType("agent_sandbox")
agent_sandbox_stub.Sandbox = MagicMock
sys.modules.setdefault("agent_sandbox", agent_sandbox_stub)

RemoteSandboxBackend = importlib.import_module(
    "src.community.aio_sandbox.remote_backend"
).RemoteSandboxBackend
provisioner_contract = importlib.import_module("src.sandbox_provisioner.contract")
INTERNAL_TOKEN_HEADER = provisioner_contract.INTERNAL_TOKEN_HEADER
deterministic_sandbox_id = provisioner_contract.deterministic_sandbox_id

TOKEN = "c" * 64


def _response(status: int, payload: object, *, content_type: str = "application/json"):
    response = MagicMock(spec=requests.Response)
    body = json.dumps(payload).encode()
    response.status_code = status
    response.headers = {
        "Content-Type": content_type,
        "Content-Length": str(len(body)),
    }
    response.iter_content.return_value = [body]
    return response


def test_remote_backend_uses_one_proxy_free_authenticated_session():
    session = MagicMock(spec=requests.Session)
    session.headers = {}
    with patch("src.community.aio_sandbox.remote_backend.requests.Session", return_value=session):
        backend = RemoteSandboxBackend(
            "http://lumen_sandbox_provisioner:8002/",
            internal_token=TOKEN,
        )

    assert backend.session is session
    assert session.trust_env is False
    assert session.headers[INTERNAL_TOKEN_HEADER] == TOKEN
    assert backend.provisioner_url == "http://lumen_sandbox_provisioner:8002"


def test_remote_create_never_sends_mounts_and_disables_redirects():
    session = MagicMock(spec=requests.Session)
    session.headers = {}
    thread_id = "thread-123"
    sandbox_id = deterministic_sandbox_id(thread_id)
    session.request.return_value = _response(
        200,
        {
            "sandbox_id": sandbox_id,
            "sandbox_url": "http://host.docker.internal:18080",
            "status": "Running",
        },
    )
    with patch("src.community.aio_sandbox.remote_backend.requests.Session", return_value=session):
        backend = RemoteSandboxBackend("http://provisioner:8002", TOKEN)

    info = backend.create(
        thread_id,
        sandbox_id,
        extra_mounts=[("/", "/host", False)],
    )

    assert info.sandbox_id == sandbox_id
    kwargs = session.request.call_args.kwargs
    assert kwargs["allow_redirects"] is False
    assert kwargs["stream"] is True
    assert kwargs["json"] == {"thread_id": thread_id, "sandbox_id": sandbox_id}
    assert "mounts" not in kwargs["json"]


def test_remote_legacy_alias_is_used_for_liveness_and_destroy():
    session = MagicMock(spec=requests.Session)
    session.headers = {}
    thread_id = "thread-123"
    sandbox_id = deterministic_sandbox_id(thread_id)
    legacy_id = sandbox_id[:8]
    session.request.return_value = _response(
        200,
        {
            "sandbox_id": sandbox_id,
            "provisioned_sandbox_id": legacy_id,
            "sandbox_url": "http://host.docker.internal:18080",
            "status": "Running",
        },
    )
    with patch(
        "src.community.aio_sandbox.remote_backend.requests.Session",
        return_value=session,
    ):
        backend = RemoteSandboxBackend("http://provisioner:8002", TOKEN)

    info = backend.create(thread_id, sandbox_id)
    assert info.sandbox_id == sandbox_id
    assert info.provisioned_sandbox_id == legacy_id

    session.request.return_value = _response(200, {})
    backend.destroy(info)

    assert session.request.call_args.args[1].endswith(
        f"/api/sandboxes/{legacy_id}"
    )


def test_remote_backend_rejects_redirects_and_mismatched_responses():
    session = MagicMock(spec=requests.Session)
    session.headers = {}
    session.request.return_value = _response(302, {})
    with patch("src.community.aio_sandbox.remote_backend.requests.Session", return_value=session):
        backend = RemoteSandboxBackend("http://provisioner:8002", TOKEN)

    with pytest.raises(RuntimeError, match="redirects are forbidden"):
        backend.discover("deadbeef")

    session.request.return_value = _response(
        200,
        {
            "sandbox_id": "cafebabe",
            "sandbox_url": "http://host.docker.internal:18080",
            "status": "Running",
        },
    )
    with pytest.raises(ValueError, match="does not match"):
        backend.discover("deadbeef")


@pytest.mark.parametrize(
    "url",
    [
        "file:///var/run/docker.sock",
        "http://user:password@provisioner:8002",
        "http://provisioner:8002/api",
        "http://provisioner:8002?redirect=http://127.0.0.1",
        "http://provisioner:99999",
        "http://provisioner\\@127.0.0.1:8002",
    ],
)
def test_remote_backend_rejects_invalid_provisioner_urls(url):
    with pytest.raises(ValueError):
        RemoteSandboxBackend(url, TOKEN)


def test_remote_backend_rejects_template_token():
    with pytest.raises(RuntimeError):
        RemoteSandboxBackend(
            "http://provisioner:8002",
            "replace-with-a-strong-random-provisioner-token",
        )
