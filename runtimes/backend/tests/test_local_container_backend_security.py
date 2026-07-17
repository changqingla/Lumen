import importlib
import logging
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.config.sandbox_config import SandboxConfig

agent_sandbox_stub = ModuleType("agent_sandbox")
agent_sandbox_stub.Sandbox = MagicMock
sys.modules.setdefault("agent_sandbox", agent_sandbox_stub)

LocalContainerBackend = importlib.import_module(
    "src.community.aio_sandbox.local_backend"
).LocalContainerBackend
AioSandbox = importlib.import_module(
    "src.community.aio_sandbox.aio_sandbox"
).AioSandbox
SandboxInfo = importlib.import_module(
    "src.community.aio_sandbox.sandbox_info"
).SandboxInfo
deterministic_sandbox_id = importlib.import_module(
    "src.sandbox_provisioner.contract"
).deterministic_sandbox_id


def _backend(**overrides) -> LocalContainerBackend:
    values = {
        "image": "sandbox:test",
        "base_port": 8080,
        "container_prefix": "lumen-sandbox",
        "config_mounts": [],
        "environment": {"API_TOKEN": "do-not-log-this-secret"},
        "pids_limit": 256,
        "drop_all_capabilities": True,
    }
    values.update(overrides)
    return LocalContainerBackend(**values)


def test_docker_sandbox_uses_private_publish_and_hardening_flags(monkeypatch, caplog):
    monkeypatch.setenv("LUMEN_SANDBOX_BIND_HOST", "127.0.0.1")
    backend = _backend()
    completed = SimpleNamespace(stdout="container-id\n", stderr="", returncode=0)

    with patch("src.community.aio_sandbox.local_backend.subprocess.run", return_value=completed) as run:
        with caplog.at_level(logging.INFO):
            container_id = backend._start_container("lumen-sandbox-deadbeef", 8123)

    command = run.call_args.args[0]
    assert container_id == "container-id"
    assert "127.0.0.1:8123:8080" in command
    assert ["--security-opt", "no-new-privileges:true"] == command[2:4]
    assert "seccomp=unconfined" not in command
    assert command[command.index("--pids-limit") + 1] == "256"
    assert command[command.index("--cap-drop") + 1] == "ALL"
    assert "API_TOKEN=do-not-log-this-secret" in command
    assert "do-not-log-this-secret" not in caplog.text


@pytest.mark.parametrize("bind_host", ["0.0.0.0", "::", "8.8.8.8"])
def test_sandbox_publish_rejects_wildcard_or_public_addresses(monkeypatch, bind_host):
    monkeypatch.setenv("LUMEN_SANDBOX_BIND_HOST", bind_host)

    with pytest.raises(RuntimeError, match="loopback, private, or link-local"):
        _backend()._start_container("lumen-sandbox-deadbeef", 8123)


def test_sandbox_publish_resolves_private_hostnames(monkeypatch):
    monkeypatch.setenv("LUMEN_SANDBOX_BIND_HOST", "host.docker.internal")
    private_result = (2, 1, 6, "", ("172.18.0.1", 0))

    with patch(
        "src.community.aio_sandbox.local_backend.socket.getaddrinfo",
        return_value=[private_result],
    ):
        assert LocalContainerBackend._resolve_publish_host() == "172.18.0.1"


def test_sandbox_config_bounds_process_limit():
    assert SandboxConfig(use="test:Provider").pids_limit == 512
    with pytest.raises(ValueError):
        SandboxConfig(use="test:Provider", pids_limit=1)


def test_local_backend_adopts_only_mount_verified_legacy_container():
    backend = _backend()
    thread_id = "thread-123"
    current_id = deterministic_sandbox_id(thread_id)
    legacy_id = current_id[:8]
    expected_mounts = [
        (
            f"/srv/lumen/threads/{thread_id}/user-data/outputs",
            "/mnt/user-data/outputs",
            False,
        )
    ]
    with (
        patch.object(backend, "_is_container_running", return_value=True),
        patch.object(backend, "_has_required_labels", return_value=True),
        patch.object(backend, "_has_expected_mounts", return_value=True),
        patch.object(backend, "_get_container_port", return_value=18080),
        patch(
            "src.community.aio_sandbox.local_backend.wait_for_sandbox_ready",
            return_value=True,
        ),
    ):
        adopted = backend._discover_verified_legacy_alias(
            thread_id,
            current_id,
            expected_mounts,
        )

    assert adopted is not None
    assert adopted.sandbox_id == current_id
    assert adopted.provisioned_sandbox_id == legacy_id
    assert adopted.container_name == f"lumen-sandbox-{legacy_id}"

    with (
        patch.object(backend, "_is_container_running", return_value=True),
        patch.object(backend, "_has_required_labels", return_value=True),
        patch.object(backend, "_has_expected_mounts", return_value=False),
    ):
        assert (
            backend._discover_verified_legacy_alias(
                thread_id,
                current_id,
                expected_mounts,
            )
            is None
        )


def test_local_create_returns_verified_legacy_alias_without_starting_container():
    backend = _backend()
    thread_id = "thread-123"
    current_id = deterministic_sandbox_id(thread_id)
    alias = SandboxInfo(
        sandbox_id=current_id,
        provisioned_sandbox_id=current_id[:8],
        sandbox_url="http://127.0.0.1:18080",
        container_name=f"lumen-sandbox-{current_id[:8]}",
    )

    with (
        patch.object(
            backend,
            "_discover_verified_legacy_alias",
            return_value=alias,
        ),
        patch.object(backend, "_start_container") as start_container,
    ):
        result = backend.create(thread_id, current_id, extra_mounts=[])

    assert result is alias
    start_container.assert_not_called()


def test_local_create_does_not_replace_matching_but_unready_legacy_container():
    backend = _backend()
    thread_id = "thread-123"
    current_id = deterministic_sandbox_id(thread_id)

    with (
        patch.object(backend, "_is_container_running", return_value=True),
        patch.object(backend, "_has_required_labels", return_value=True),
        patch.object(backend, "_has_expected_mounts", return_value=True),
        patch.object(backend, "_get_container_port", return_value=18080),
        patch(
            "src.community.aio_sandbox.local_backend.wait_for_sandbox_ready",
            return_value=False,
        ),
        patch.object(backend, "_start_container") as start_container,
        pytest.raises(RuntimeError, match="legacy sandbox is not ready"),
    ):
        backend.create(thread_id, current_id, extra_mounts=[("/host", "/mnt", False)])

    start_container.assert_not_called()


def test_legacy_mount_verification_checks_source_destination_and_mode():
    backend = _backend()
    backend._runtime = "docker"
    mounts = [
        (
            "/srv/lumen/threads/thread-123/user-data/knowledge",
            "/mnt/user-data/knowledge",
            True,
        )
    ]
    completed = SimpleNamespace(
        returncode=0,
        stdout=(
            '[{"Type":"bind","Source":"/srv/lumen/threads/thread-123/'
            'user-data/knowledge","Destination":"/mnt/user-data/knowledge",'
            '"RW":false}]'
        ),
    )

    with patch(
        "src.community.aio_sandbox.local_backend.subprocess.run",
        return_value=completed,
    ):
        assert backend._has_expected_mounts("lumen-sandbox-deadbeef", mounts)

    completed.stdout = completed.stdout.replace('"RW":false', '"RW":true')
    with patch(
        "src.community.aio_sandbox.local_backend.subprocess.run",
        return_value=completed,
    ):
        assert not backend._has_expected_mounts(
            "lumen-sandbox-deadbeef",
            mounts,
        )


def test_aio_sandbox_list_dir_quotes_untrusted_path():
    sandbox = AioSandbox.__new__(AioSandbox)
    result = SimpleNamespace(data=SimpleNamespace(output="/tmp/example\n"))
    sandbox._client = SimpleNamespace(
        shell=SimpleNamespace(exec_command=MagicMock(return_value=result)),
    )

    entries = sandbox.list_dir("/tmp/example; touch /tmp/injected", max_depth=3)

    assert entries == ["/tmp/example"]
    command = sandbox._client.shell.exec_command.call_args.kwargs["command"]
    assert "find '/tmp/example; touch /tmp/injected' -maxdepth 3" in command
    assert "\\( -type f -o -type d \\)" in command


@pytest.mark.parametrize("path, depth", [("", 2), ("/tmp/ok", -1), ("/tmp/ok", 11), ("/tmp/ok", True)])
def test_aio_sandbox_list_dir_rejects_invalid_bounds(path, depth):
    sandbox = AioSandbox.__new__(AioSandbox)
    sandbox._client = MagicMock()

    with pytest.raises(ValueError):
        sandbox.list_dir(path, max_depth=depth)

    sandbox._client.shell.exec_command.assert_not_called()
