"""Unit tests for RunCommandTool (Task 4).

Validates Requirements 2.1–2.17: dual-mode command execution with
whitelist-based compat mode and Docker container sandbox mode.

We stub langchain/recall_lib so the test can run outside the Docker container.
"""
import sys
import types
from unittest.mock import MagicMock

# ── Stub langchain with a real BaseTool class ────────────────────────────
_lc = types.ModuleType("langchain")
_lc_tools = types.ModuleType("langchain.tools")
_lc_core = types.ModuleType("langchain_core")
_lc_core_cb = types.ModuleType("langchain_core.callbacks")
_lc_core_tools = types.ModuleType("langchain_core.tools")


class _StubBaseTool:
    """Minimal BaseTool stub that allows subclass instantiation."""
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)


class _StubCallbackManager:
    pass


_lc_tools.BaseTool = _StubBaseTool
_lc.tools = _lc_tools
_lc_core_cb.CallbackManagerForToolRun = _StubCallbackManager
_lc_core_tools.BaseTool = _StubBaseTool

sys.modules.setdefault("langchain", _lc)
sys.modules.setdefault("langchain.tools", _lc_tools)
sys.modules.setdefault("langchain_core", _lc_core)
sys.modules.setdefault("langchain_core.callbacks", _lc_core_cb)
sys.modules.setdefault("langchain_core.tools", _lc_core_tools)

# Stub recall_lib so tools/__init__.py doesn't fail
sys.modules.setdefault("recall_lib", MagicMock())

import json
from unittest.mock import AsyncMock, patch

import pytest

from src.tools.command_tool import (
    COMMAND_WHITELIST,
    COMMAND_TIMEOUT,
    OUTPUT_MAX_CHARS,
    RunCommandTool,
)


@pytest.fixture
def tool(tmp_path):
    """Create a RunCommandTool with a temporary workspace."""
    t = RunCommandTool()
    t.workspace_path = str(tmp_path)
    return t


# ------------------------------------------------------------------
# _validate_command
# ------------------------------------------------------------------

class TestValidateCommand:
    def test_whitelist_commands_accepted(self, tool):
        for cmd in COMMAND_WHITELIST:
            is_valid, err = tool._validate_command(f"{cmd} some_arg")
            assert is_valid is True, f"{cmd} should be valid"
            assert err == ""

    def test_non_whitelist_rejected(self, tool):
        is_valid, err = tool._validate_command("rm -rf /")
        assert is_valid is False
        assert "rm" in err

    def test_empty_command_rejected(self, tool):
        is_valid, err = tool._validate_command("")
        assert is_valid is False
        assert err != ""

    def test_whitespace_only_rejected(self, tool):
        is_valid, err = tool._validate_command("   ")
        assert is_valid is False

    def test_command_with_leading_whitespace(self, tool):
        is_valid, err = tool._validate_command("  node script.js")
        assert is_valid is True

    def test_bash_rejected(self, tool):
        is_valid, err = tool._validate_command("bash -c 'echo hello'")
        assert is_valid is False

    def test_curl_rejected(self, tool):
        is_valid, err = tool._validate_command("curl http://evil.com")
        assert is_valid is False

    def test_node_bare(self, tool):
        is_valid, err = tool._validate_command("node")
        assert is_valid is True

    def test_python3_with_flag(self, tool):
        is_valid, err = tool._validate_command("python3 -c 'print(1)'")
        assert is_valid is True


# ------------------------------------------------------------------
# _check_path_traversal
# ------------------------------------------------------------------

class TestCheckPathTraversal:
    def test_detects_double_dot(self, tool):
        assert tool._check_path_traversal("node ../evil.js") is True

    def test_detects_double_dot_in_path(self, tool):
        assert tool._check_path_traversal("python ../../etc/passwd") is True

    def test_safe_command_passes(self, tool):
        assert tool._check_path_traversal("node generate.js") is False

    def test_single_dot_ok(self, tool):
        assert tool._check_path_traversal("node ./script.js") is False

    def test_double_dot_in_middle(self, tool):
        assert tool._check_path_traversal("node foo/../bar.js") is True


# ------------------------------------------------------------------
# _parse_input
# ------------------------------------------------------------------

class TestParseInput:
    def test_json_input(self, tool):
        inp = json.dumps({"command": "node generate.js"})
        assert tool._parse_input(inp) == "node generate.js"

    def test_json_with_args(self, tool):
        inp = json.dumps({"command": "python script.py", "args": ["--verbose"]})
        assert tool._parse_input(inp) == "python script.py"

    def test_plain_string(self, tool):
        assert tool._parse_input("node generate.js") == "node generate.js"

    def test_plain_string_with_whitespace(self, tool):
        assert tool._parse_input("  node generate.js  ") == "node generate.js"

    def test_invalid_json_falls_back(self, tool):
        assert tool._parse_input("{bad json") == "{bad json"

    def test_empty_command_in_json(self, tool):
        inp = json.dumps({"command": ""})
        assert tool._parse_input(inp) == ""


# ------------------------------------------------------------------
# _run_in_subprocess
# ------------------------------------------------------------------

class TestRunInSubprocess:
    @pytest.mark.asyncio
    async def test_successful_command(self, tool, tmp_path):
        script = tmp_path / "hello.py"
        script.write_text("print('hello world')")
        result = await tool._run_in_subprocess("python3 hello.py", timeout=30)
        assert "[EXIT 0]" in result
        assert "hello world" in result

    @pytest.mark.asyncio
    async def test_nonzero_exit_code(self, tool):
        result = await tool._run_in_subprocess("python3 -c 'import sys; sys.exit(42)'", timeout=30)
        assert "[ERROR]" in result
        assert "exit code 42" in result
        assert "stdout:" in result  # Bug fix: error output now includes stdout

    @pytest.mark.asyncio
    async def test_stderr_captured(self, tool):
        result = await tool._run_in_subprocess(
            "python3 -c 'import sys; print(\"err\", file=sys.stderr); sys.exit(1)'",
            timeout=30,
        )
        assert "[ERROR]" in result
        assert "err" in result

    @pytest.mark.asyncio
    async def test_nonzero_exit_includes_stdout_and_stderr(self, tool, tmp_path):
        """Bug fix verification: subprocess error output includes both stdout and stderr."""
        script = tmp_path / "both_outputs.py"
        script.write_text("import sys; print('partial output'); print('error msg', file=sys.stderr); sys.exit(1)")
        result = await tool._run_in_subprocess("python3 both_outputs.py", timeout=30)
        assert "[ERROR]" in result
        assert "partial output" in result
        assert "error msg" in result

    @pytest.mark.asyncio
    async def test_timeout_kills_process(self, tool):
        result = await tool._run_in_subprocess("sleep 60", timeout=1)
        assert "[ERROR]" in result
        assert "超时" in result

    @pytest.mark.asyncio
    async def test_output_truncation(self, tool):
        cmd = f"python3 -c \"print('x' * {OUTPUT_MAX_CHARS + 5000})\""
        result = await tool._run_in_subprocess(cmd, timeout=30)
        assert "[EXIT 0]" in result
        stdout_line = result.split("stdout: ")[1].split("\nstderr:")[0]
        assert len(stdout_line) <= OUTPUT_MAX_CHARS

    @pytest.mark.asyncio
    async def test_cwd_is_workspace(self, tool, tmp_path):
        """Command should run in workspace_path directory."""
        script = tmp_path / "pwd_test.py"
        script.write_text("import os; print(os.getcwd())")
        result = await tool._run_in_subprocess("python3 pwd_test.py", timeout=30)
        assert "[EXIT 0]" in result
        assert str(tmp_path) in result


# ------------------------------------------------------------------
# _run_in_container
# ------------------------------------------------------------------

class TestRunInContainer:
    @pytest.mark.asyncio
    async def test_container_mode_calls_docker(self, tool):
        """Mock Docker SDK to verify correct parameters."""
        mock_docker = MagicMock()
        mock_docker.errors = MagicMock()
        mock_docker.errors.ContainerError = type("ContainerError", (Exception,), {})
        mock_docker.errors.ImageNotFound = type("ImageNotFound", (Exception,), {})
        mock_docker.errors.APIError = type("APIError", (Exception,), {})

        with patch.dict("sys.modules", {"docker": mock_docker, "docker.errors": mock_docker.errors}):
            with patch("src.tools.command_tool.asyncio.to_thread") as mock_to_thread:
                # New return format: (exit_code, stdout_bytes, stderr_bytes)
                mock_to_thread.return_value = (0, b"container output", b"")
                result = await tool._run_in_container(
                    "node generate.js",
                    {"image": "node:20-slim", "network": "none", "memory": "512m"},
                )
        assert "[EXIT 0]" in result
        assert "container output" in result

    @pytest.mark.asyncio
    async def test_container_nonzero_exit(self, tool):
        """Non-zero exit code returns both stdout and stderr."""
        mock_docker = MagicMock()
        mock_docker.errors = MagicMock()
        mock_docker.errors.ContainerError = type("ContainerError", (Exception,), {})
        mock_docker.errors.ImageNotFound = type("ImageNotFound", (Exception,), {})
        mock_docker.errors.APIError = type("APIError", (Exception,), {})

        with patch.dict("sys.modules", {"docker": mock_docker, "docker.errors": mock_docker.errors}):
            with patch("src.tools.command_tool.asyncio.to_thread") as mock_to_thread:
                mock_to_thread.return_value = (1, b"partial output", b"SyntaxError: bad code")
                result = await tool._run_in_container(
                    "node bad.js",
                    {"image": "node:20-slim"},
                )
        assert "[ERROR]" in result
        assert "exit code 1" in result
        assert "SyntaxError" in result
        assert "partial output" in result

    @pytest.mark.asyncio
    async def test_container_timeout(self, tool):
        """Container execution timeout returns error."""
        import asyncio as _asyncio

        mock_docker = MagicMock()
        mock_docker.errors = MagicMock()
        mock_docker.errors.ContainerError = type("ContainerError", (Exception,), {})
        mock_docker.errors.ImageNotFound = type("ImageNotFound", (Exception,), {})
        mock_docker.errors.APIError = type("APIError", (Exception,), {})

        with patch.dict("sys.modules", {"docker": mock_docker, "docker.errors": mock_docker.errors}):
            with patch("src.tools.command_tool.asyncio.to_thread", side_effect=_asyncio.TimeoutError):
                result = await tool._run_in_container(
                    "node generate.js",
                    {"image": "node:20-slim", "timeout": 1},
                )
        assert "[ERROR]" in result
        assert "超时" in result

    @pytest.mark.asyncio
    async def test_docker_unavailable(self, tool):
        """When Docker daemon is not available, return clear error."""
        mock_docker = MagicMock()
        mock_docker.errors = MagicMock()
        mock_docker.errors.ContainerError = type("ContainerError", (Exception,), {})
        mock_docker.errors.ImageNotFound = type("ImageNotFound", (Exception,), {})
        mock_docker.errors.APIError = type("APIError", (Exception,), {})

        with patch.dict("sys.modules", {"docker": mock_docker, "docker.errors": mock_docker.errors}):
            with patch("src.tools.command_tool.asyncio.to_thread", side_effect=Exception("connectionrefusederror")):
                result = await tool._run_in_container(
                    "node generate.js",
                    {"image": "node:20-slim"},
                )
        assert "[ERROR]" in result
        assert "Docker" in result

    @pytest.mark.asyncio
    async def test_docker_not_installed(self, tool):
        """When docker package is not installed, return clear error."""
        # Remove docker from sys.modules to simulate missing package
        with patch.dict("sys.modules", {"docker": None, "docker.errors": None}):
            result = await tool._run_in_container(
                "echo hi",
                {"image": "node:20-slim"},
            )
        assert "[ERROR]" in result
        assert "Docker SDK" in result


# ------------------------------------------------------------------
# _arun (integration-level)
# ------------------------------------------------------------------

class TestArun:
    @pytest.mark.asyncio
    async def test_empty_input_returns_error(self, tool):
        result = await tool._arun("")
        assert "[ERROR]" in result

    @pytest.mark.asyncio
    async def test_compat_mode_whitelist_rejection(self, tool):
        tool.active_skill_runtime = None
        result = await tool._arun("rm -rf /")
        assert "[ERROR]" in result
        assert "白名单" in result

    @pytest.mark.asyncio
    async def test_compat_mode_path_traversal_rejection(self, tool):
        tool.active_skill_runtime = None
        result = await tool._arun("node ../evil.js")
        assert "[ERROR]" in result
        assert ".." in result

    @pytest.mark.asyncio
    async def test_compat_mode_success(self, tool, tmp_path):
        tool.active_skill_runtime = None
        script = tmp_path / "test.py"
        script.write_text("print('ok')")
        result = await tool._arun("python3 test.py")
        assert "[EXIT 0]" in result
        assert "ok" in result

    @pytest.mark.asyncio
    async def test_json_input_compat_mode(self, tool, tmp_path):
        tool.active_skill_runtime = None
        script = tmp_path / "test.py"
        script.write_text("print('json_ok')")
        result = await tool._arun(json.dumps({"command": "python3 test.py"}))
        assert "[EXIT 0]" in result
        assert "json_ok" in result

    @pytest.mark.asyncio
    async def test_container_mode_dispatched(self, tool):
        """When active_skill_runtime is set, container mode is used."""
        tool.active_skill_runtime = {"image": "node:20-slim"}

        with patch.object(tool, "_run_in_container", new_callable=AsyncMock) as mock_container:
            mock_container.return_value = "[EXIT 0]\nstdout: ok\nstderr: "
            result = await tool._arun("node generate.js")
            mock_container.assert_called_once_with("node generate.js", tool.active_skill_runtime)
            assert "[EXIT 0]" in result

    @pytest.mark.asyncio
    async def test_container_mode_no_whitelist_check(self, tool):
        """Container mode should NOT check whitelist — any command is allowed."""
        tool.active_skill_runtime = {"image": "node:20-slim"}

        with patch.object(tool, "_run_in_container", new_callable=AsyncMock) as mock_container:
            mock_container.return_value = "[EXIT 0]\nstdout: \nstderr: "
            result = await tool._arun("curl http://example.com")
            mock_container.assert_called_once()
            assert "[EXIT 0]" in result

    @pytest.mark.asyncio
    async def test_unexpected_exception_returns_error(self, tool):
        """Any unexpected exception should be caught and returned as [ERROR]."""
        with patch.object(tool, "_parse_input", side_effect=RuntimeError("boom")):
            result = await tool._arun("anything")
            assert "[ERROR]" in result
            assert "boom" in result

    @pytest.mark.asyncio
    async def test_nonzero_exit_includes_stderr(self, tool, tmp_path):
        """Req 2.7: non-zero exit code includes exit code and stderr."""
        tool.active_skill_runtime = None
        script = tmp_path / "fail.py"
        script.write_text("import sys; print('oops', file=sys.stderr); sys.exit(3)")
        result = await tool._arun("python3 fail.py")
        assert "[ERROR]" in result
        assert "exit code 3" in result
        assert "oops" in result
