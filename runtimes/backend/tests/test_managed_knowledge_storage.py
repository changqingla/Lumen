import hashlib
import importlib
import sys
from types import ModuleType
from unittest.mock import MagicMock

from src.config.paths import Paths
from src.sandbox.tools import replace_virtual_path

agent_sandbox_stub = ModuleType("agent_sandbox")
agent_sandbox_stub.Sandbox = MagicMock
sys.modules.setdefault("agent_sandbox", agent_sandbox_stub)

AioSandboxProvider = importlib.import_module("src.community.aio_sandbox.aio_sandbox_provider").AioSandboxProvider


def test_paths_create_separate_managed_knowledge_directory(tmp_path):
    paths = Paths(str(tmp_path))

    paths.ensure_thread_dirs("thread-1")

    assert paths.sandbox_knowledge_dir("thread-1").is_dir()
    assert paths.sandbox_knowledge_dir("thread-1") != paths.sandbox_uploads_dir("thread-1")


def test_local_virtual_path_maps_managed_knowledge(tmp_path):
    knowledge_dir = tmp_path / "knowledge"
    thread_data = {
        "workspace_path": str(tmp_path / "workspace"),
        "uploads_path": str(tmp_path / "uploads"),
        "knowledge_path": str(knowledge_dir),
        "outputs_path": str(tmp_path / "outputs"),
    }

    actual = replace_virtual_path(
        "/mnt/user-data/knowledge/reference.md",
        thread_data,
    )

    assert actual == str(knowledge_dir / "reference.md")


def test_aio_thread_mounts_make_managed_knowledge_read_only(monkeypatch, tmp_path):
    runtime_paths = Paths(str(tmp_path / "runtime"))
    host_base = tmp_path / "host"
    monkeypatch.setenv("LUMEN_HOST_BASE_DIR", str(host_base))
    monkeypatch.setattr(
        "src.community.aio_sandbox.aio_sandbox_provider.get_paths",
        lambda: runtime_paths,
    )

    mounts = AioSandboxProvider._get_thread_mounts("thread-1")

    knowledge_mount = next(mount for mount in mounts if mount[1] == "/mnt/user-data/knowledge")
    assert knowledge_mount == (
        str(Paths(host_base).sandbox_knowledge_dir("thread-1")),
        "/mnt/user-data/knowledge",
        True,
    )


def test_sandbox_id_includes_read_only_mount_generation():
    thread_id = "thread-1"

    current = AioSandboxProvider._deterministic_sandbox_id(thread_id)

    assert current == hashlib.sha256(f"mount-v2:{thread_id}".encode()).hexdigest()[:32]
    assert current != hashlib.sha256(thread_id.encode()).hexdigest()[:32]
