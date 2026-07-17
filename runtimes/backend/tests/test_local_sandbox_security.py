"""Command-boundary and path-mapping regressions for LocalSandbox."""

from types import SimpleNamespace
from unittest.mock import patch

from src.sandbox.local.local_sandbox import LocalSandbox
from src.sandbox.tools import restore_virtual_paths_in_output


def test_execute_command_uses_explicit_shell_argv_boundary():
    sandbox = LocalSandbox("local")
    completed = SimpleNamespace(stdout="ok", stderr="", returncode=0)

    with (
        patch.object(sandbox, "_get_shell", return_value="/bin/sh"),
        patch(
            "src.sandbox.local.local_sandbox.subprocess.run",
            return_value=completed,
        ) as run,
    ):
        result = sandbox.execute_command("printf ok")

    assert result == "ok"
    assert run.call_args.args[0] == ["/bin/sh", "-c", "printf ok"]
    assert "shell" not in run.call_args.kwargs
    assert "executable" not in run.call_args.kwargs


def test_path_mapping_requires_a_component_boundary(tmp_path):
    sandbox = LocalSandbox(
        "local",
        path_mappings={"/mnt/skills": str(tmp_path / "skills")},
    )

    assert sandbox._resolve_path("/mnt/skills/guide.md") == str(
        tmp_path / "skills" / "guide.md"
    )
    assert sandbox._resolve_path("/mnt/skills-private/secret") == (
        "/mnt/skills-private/secret"
    )
    assert sandbox._resolve_paths_in_command(
        "cat /mnt/skills-private/secret"
    ) == "cat /mnt/skills-private/secret"


def test_local_thread_paths_are_restored_before_returning_output(tmp_path):
    outputs = tmp_path / "threads" / "t1" / "user-data" / "outputs"
    output = f"created {outputs}/report.txt"

    restored = restore_virtual_paths_in_output(
        output,
        {
            "workspace_path": str(outputs.parent / "workspace"),
            "uploads_path": str(outputs.parent / "uploads"),
            "knowledge_path": str(outputs.parent / "knowledge"),
            "outputs_path": str(outputs),
        },
    )

    assert restored == "created /mnt/user-data/outputs/report.txt"
    assert str(tmp_path) not in restored
