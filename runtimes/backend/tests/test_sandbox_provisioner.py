from __future__ import annotations

import hashlib
import importlib
import logging
import sys
from pathlib import Path, PurePosixPath
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

agent_sandbox_stub = ModuleType("agent_sandbox")
agent_sandbox_stub.Sandbox = MagicMock
sys.modules.setdefault("agent_sandbox", agent_sandbox_stub)

provisioner_app = importlib.import_module("src.sandbox_provisioner.app")
provisioner_contract = importlib.import_module("src.sandbox_provisioner.contract")
SandboxInfo = importlib.import_module(
    "src.community.aio_sandbox.sandbox_info"
).SandboxInfo
DockerSandboxProvisioner = provisioner_app.DockerSandboxProvisioner
ProvisionerSettings = provisioner_app.ProvisionerSettings
create_app = provisioner_app.create_app
load_settings = provisioner_app.load_settings
INTERNAL_TOKEN_HEADER = provisioner_contract.INTERNAL_TOKEN_HEADER
deterministic_sandbox_id = provisioner_contract.deterministic_sandbox_id
validate_host_root = provisioner_contract.validate_host_root
validate_internal_token = provisioner_contract.validate_internal_token
validate_sandbox_id = provisioner_contract.validate_sandbox_id
validate_sandbox_image = provisioner_contract.validate_sandbox_image

TOKEN = "a" * 64


def _settings(tmp_path: Path) -> ProvisionerSettings:
    state = tmp_path / "state"
    skills = tmp_path / "skills"
    state.mkdir()
    skills.mkdir()
    return ProvisionerSettings(
        internal_token=TOKEN,
        image="sandbox:test",
        host_state_root=PurePosixPath("/srv/lumen/state"),
        visible_state_root=state,
        host_skills_root=PurePosixPath("/srv/lumen/skills"),
        visible_skills_root=skills,
    )


@pytest.mark.parametrize(
    "token",
    [None, "short", "replace-with-a-strong-random-token-value", "x" * 31, "测" * 32],
)
def test_internal_token_rejects_missing_weak_or_template_values(token):
    with pytest.raises(RuntimeError):
        validate_internal_token(token)


@pytest.mark.parametrize(
    "root",
    ["", ".", "/", "/etc/lumen", "/proc/lumen", "/srv/lumen/../state", "/var/run/docker.sock"],
)
def test_host_root_validation_rejects_broad_or_unsafe_paths(root):
    with pytest.raises(RuntimeError):
        validate_host_root(root, setting_name="TEST_ROOT")


def test_settings_fail_closed_without_token_or_roots(tmp_path):
    with pytest.raises(RuntimeError):
        load_settings({})

    environment = {
        "SANDBOX_PROVISIONER_INTERNAL_TOKEN": TOKEN,
        "SANDBOX_PROVISIONER_HOST_STATE_ROOT": "/srv/lumen/state",
        "SANDBOX_PROVISIONER_VISIBLE_STATE_ROOT": str(tmp_path / "missing-state"),
        "SANDBOX_PROVISIONER_HOST_SKILLS_ROOT": "/srv/lumen/skills",
        "SANDBOX_PROVISIONER_VISIBLE_SKILLS_ROOT": str(tmp_path / "missing-skills"),
    }
    with pytest.raises(RuntimeError, match="existing directory"):
        load_settings(environment)


def test_settings_reject_missing_token_even_when_roots_are_valid(tmp_path):
    state = tmp_path / "state"
    skills = tmp_path / "skills"
    state.mkdir()
    skills.mkdir()
    environment = {
        "SANDBOX_PROVISIONER_HOST_STATE_ROOT": "/srv/lumen/state",
        "SANDBOX_PROVISIONER_VISIBLE_STATE_ROOT": str(state),
        "SANDBOX_PROVISIONER_HOST_SKILLS_ROOT": "/srv/lumen/skills",
        "SANDBOX_PROVISIONER_VISIBLE_SKILLS_ROOT": str(skills),
    }

    with pytest.raises(RuntimeError, match="SANDBOX_PROVISIONER_INTERNAL_TOKEN"):
        load_settings(environment)


@pytest.mark.parametrize(
    "image",
    [
        "sandbox:latest",
        "sandbox:test",
        "sandbox@sha256:short",
        "sandbox@sha256:" + "A" * 64,
        "-sandbox@sha256:" + "a" * 64,
    ],
)
def test_provisioner_image_must_be_pinned_by_digest(image):
    with pytest.raises(RuntimeError, match="immutable sha256 digest"):
        validate_sandbox_image(image)


def test_provisioner_image_accepts_lowercase_sha256_digest():
    image = "registry.example/lumen/sandbox@sha256:" + "a" * 64
    assert validate_sandbox_image(image) == image


def test_thread_mounts_are_fixed_and_knowledge_and_skills_are_read_only(tmp_path):
    provisioner = DockerSandboxProvisioner(_settings(tmp_path))
    mounts = provisioner._thread_mounts("thread-123")

    assert mounts == [
        (
            "/srv/lumen/state/threads/thread-123/user-data/workspace",
            "/mnt/user-data/workspace",
            False,
        ),
        (
            "/srv/lumen/state/threads/thread-123/user-data/uploads",
            "/mnt/user-data/uploads",
            False,
        ),
        (
            "/srv/lumen/state/threads/thread-123/user-data/knowledge",
            "/mnt/user-data/knowledge",
            True,
        ),
        (
            "/srv/lumen/state/threads/thread-123/user-data/outputs",
            "/mnt/user-data/outputs",
            False,
        ),
        ("/srv/lumen/skills", "/mnt/skills", True),
    ]
    assert all(mount[1] != "/mnt/user-data" for mount in mounts)


def test_create_requires_deterministic_thread_binding(tmp_path):
    provisioner = DockerSandboxProvisioner(_settings(tmp_path))
    provisioner._backend = MagicMock()

    with pytest.raises(ValueError, match="deterministic thread binding"):
        provisioner.create("thread-123", "deadbeef")

    provisioner._backend.create.assert_not_called()


def test_current_ids_use_128_bits_but_legacy_ids_remain_addressable():
    current = deterministic_sandbox_id("thread-123")

    assert len(current) == 32
    assert validate_sandbox_id(current) == current
    assert validate_sandbox_id(current[:8]) == current[:8]
    with pytest.raises(ValueError):
        validate_sandbox_id(current[:16])


def test_health_is_anonymous_but_every_other_route_requires_token(tmp_path):
    settings = _settings(tmp_path)
    service = MagicMock()
    sandbox_id = deterministic_sandbox_id("thread-123")
    service.create.return_value = SandboxInfo(
        sandbox_id=sandbox_id,
        sandbox_url="http://host.docker.internal:18080",
    )
    app = create_app(settings=settings, service=service, validate_runtime=False)

    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert client.get(f"/api/sandboxes/{sandbox_id}").status_code == 401
        assert (
            client.get(
                f"/api/sandboxes/{sandbox_id}",
                headers={INTERNAL_TOKEN_HEADER: "b" * 64},
            ).status_code
            == 401
        )
        duplicate_header_response = client.get(
            f"/api/sandboxes/{sandbox_id}",
            headers=[
                (INTERNAL_TOKEN_HEADER, TOKEN),
                (INTERNAL_TOKEN_HEADER, TOKEN),
            ],
        )
        assert duplicate_header_response.status_code == 401
        response = client.post(
            "/api/sandboxes",
            headers={INTERNAL_TOKEN_HEADER: TOKEN},
            json={"thread_id": "thread-123", "sandbox_id": sandbox_id},
        )

    assert response.status_code == 200
    assert response.json()["sandbox_id"] == sandbox_id


def test_api_forbids_caller_supplied_container_options(tmp_path):
    settings = _settings(tmp_path)
    service = MagicMock()
    sandbox_id = deterministic_sandbox_id("thread-123")
    app = create_app(settings=settings, service=service, validate_runtime=False)

    with TestClient(app) as client:
        response = client.post(
            "/api/sandboxes",
            headers={INTERNAL_TOKEN_HEADER: TOKEN},
            json={
                "thread_id": "thread-123",
                "sandbox_id": sandbox_id,
                "image": "attacker/image",
                "mounts": ["/:/host"],
                "command": ["sh"],
                "privileged": True,
            },
        )

    assert response.status_code == 422
    service.create.assert_not_called()


def test_deterministic_id_matches_provider_mount_generation():
    thread_id = "72df5e4c-f596-4a14-bdfd-8a33a29c184a"
    expected = hashlib.sha256(f"mount-v2:{thread_id}".encode()).hexdigest()[:32]
    assert deterministic_sandbox_id(thread_id) == expected


def test_runtime_check_rejects_missing_docker_socket(tmp_path):
    provisioner = DockerSandboxProvisioner(_settings(tmp_path))
    with patch("src.sandbox_provisioner.app.Path.exists", return_value=False):
        with pytest.raises(RuntimeError, match="Docker socket"):
            provisioner.validate_runtime()


def test_provisioned_container_command_has_fixed_hardening(tmp_path, monkeypatch, caplog):
    provisioner = DockerSandboxProvisioner(_settings(tmp_path))
    backend = provisioner.backend
    monkeypatch.setenv("LUMEN_SANDBOX_BIND_HOST", "127.0.0.1")
    completed = SimpleNamespace(stdout="a" * 64 + "\n", stderr="", returncode=0)

    with patch(
        "src.community.aio_sandbox.local_backend.subprocess.run",
        return_value=completed,
    ) as run:
        with caplog.at_level(logging.INFO):
            backend._start_container(
                "lumen-sandbox-deadbeef",
                18080,
                [("/srv/lumen/state/thread/workspace", "/mnt/user-data/workspace", False)],
            )

    command = run.call_args.args[0]
    assert command[command.index("--privileged=false")] == "--privileged=false"
    assert command[command.index("--cap-drop") + 1] == "ALL"
    assert {
        command[index + 1]
        for index, value in enumerate(command)
        if value == "--cap-add"
    } == {"CHOWN", "DAC_OVERRIDE", "FOWNER", "SETGID", "SETUID"}
    assert command[command.index("--pids-limit") + 1] == "512"
    assert "seccomp=unconfined" not in command
    assert "--read-only" not in command
    assert command.count("--tmpfs") == 2
    assert "com.lumen.managed-by=sandbox-provisioner" in command
    assert "BROWSER_NO_SANDBOX=--no-sandbox" in command
    assert TOKEN not in command
    assert TOKEN not in caplog.text
