"""Unit tests for UploadToStorageTool (Task 7).

Validates Requirements 5.1–5.5: file upload to MinIO with path safety,
presigned URL generation, and error handling.

We stub langchain/recall_lib so the test can run outside the Docker container.
"""
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

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

# Stub minio so storage_tool can import without the real minio package
_minio_mod = types.ModuleType("minio")
_minio_mod.Minio = MagicMock
_minio_error = types.ModuleType("minio.error")
_minio_error.S3Error = type("S3Error", (Exception,), {})
sys.modules.setdefault("minio", _minio_mod)
sys.modules.setdefault("minio.error", _minio_error)

# Stub urllib3 if not available
sys.modules.setdefault("urllib3", MagicMock())

from pathlib import Path

import pytest

from src.tools.storage_tool import UploadToStorageTool


@pytest.fixture
def tool(tmp_path):
    """Create an UploadToStorageTool with a temporary workspace."""
    t = UploadToStorageTool()
    t.workspace_path = str(tmp_path)
    t.session_id = "test-session-123"
    return t


def _create_file(tmp_path: Path, name: str, content: bytes = b"fake pptx data") -> Path:
    """Helper to create a file in the workspace."""
    f = tmp_path / name
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(content)
    return f


# ------------------------------------------------------------------
# _validate_path
# ------------------------------------------------------------------

class TestValidatePath:
    """Validates: path safety for upload_to_storage."""

    def test_relative_path_accepted(self, tool):
        ok, msg = tool._validate_path("output.pptx")
        assert ok is True
        assert msg == ""

    def test_nested_relative_path_accepted(self, tool):
        ok, msg = tool._validate_path("subdir/output.pptx")
        assert ok is True
        assert msg == ""

    def test_absolute_path_rejected(self, tool):
        ok, msg = tool._validate_path("/etc/passwd")
        assert ok is False
        assert msg

    def test_dot_dot_traversal_rejected(self, tool):
        ok, msg = tool._validate_path("../outside.txt")
        assert ok is False
        assert msg

    def test_nested_dot_dot_rejected(self, tool):
        ok, msg = tool._validate_path("sub/../../outside.txt")
        assert ok is False
        assert msg

    def test_empty_path_rejected(self, tool):
        ok, msg = tool._validate_path("")
        assert ok is False
        assert msg

    def test_whitespace_only_rejected(self, tool):
        ok, msg = tool._validate_path("   ")
        assert ok is False
        assert msg


# ------------------------------------------------------------------
# _arun — upload flow
# ------------------------------------------------------------------

class TestArun:
    """Validates: Requirements 5.1, 5.2, 5.3, 5.4, 5.5."""

    @pytest.mark.asyncio
    async def test_successful_upload(self, tool, tmp_path):
        """Req 5.1, 5.2: upload file and return presigned URL."""
        _create_file(tmp_path, "output.pptx")

        mock_client = MagicMock()
        mock_client.upload_file = AsyncMock(return_value="agent-outputs/test-session-123/output.pptx")
        mock_client.get_presigned_url = MagicMock(return_value="https://minio.example.com/download/output.pptx")

        with patch("src.tools.storage_tool.get_agent_minio_client", return_value=mock_client):
            result = await tool._arun("output.pptx")

        assert "文件已上传" in result
        assert "https://minio.example.com/download/output.pptx" in result

    @pytest.mark.asyncio
    async def test_object_name_format(self, tool, tmp_path):
        """Req 5.3: file stored at agent-outputs/{session_id}/{filename}."""
        _create_file(tmp_path, "report.pptx")

        mock_client = MagicMock()
        mock_client.upload_file = AsyncMock(return_value="agent-outputs/test-session-123/report.pptx")
        mock_client.get_presigned_url = MagicMock(return_value="https://example.com/url")

        with patch("src.tools.storage_tool.get_agent_minio_client", return_value=mock_client):
            await tool._arun("report.pptx")

        # Verify upload_file was called with correct object_name
        mock_client.upload_file.assert_called_once()
        call_kwargs = mock_client.upload_file.call_kwargs if hasattr(mock_client.upload_file, 'call_kwargs') else {}
        args, kwargs = mock_client.upload_file.call_args
        assert kwargs.get("object_name") == "agent-outputs/test-session-123/report.pptx"

    @pytest.mark.asyncio
    async def test_file_not_found_error(self, tool, tmp_path):
        """Req 5.4: return error when file doesn't exist."""
        result = await tool._arun("nonexistent.pptx")

        assert result.startswith("[ERROR] upload_to_storage:")
        assert "不存在" in result

    @pytest.mark.asyncio
    async def test_minio_upload_failure(self, tool, tmp_path):
        """Req 5.5: return error with failure reason on MinIO failure."""
        _create_file(tmp_path, "fail.pptx")

        mock_client = MagicMock()
        mock_client.upload_file = AsyncMock(side_effect=Exception("Connection refused"))

        with patch("src.tools.storage_tool.get_agent_minio_client", return_value=mock_client):
            result = await tool._arun("fail.pptx")

        assert result.startswith("[ERROR] upload_to_storage:")
        assert "Connection refused" in result

    @pytest.mark.asyncio
    async def test_path_traversal_rejected(self, tool):
        """Path traversal attempts should be rejected."""
        result = await tool._arun("../escape.pptx")

        assert result.startswith("[ERROR] upload_to_storage:")

    @pytest.mark.asyncio
    async def test_absolute_path_rejected(self, tool):
        result = await tool._arun("/tmp/evil.pptx")

        assert result.startswith("[ERROR] upload_to_storage:")

    @pytest.mark.asyncio
    async def test_empty_input_rejected(self, tool):
        result = await tool._arun("")

        assert result.startswith("[ERROR] upload_to_storage:")

    @pytest.mark.asyncio
    async def test_whitespace_input_rejected(self, tool):
        result = await tool._arun("   ")

        assert result.startswith("[ERROR] upload_to_storage:")

    @pytest.mark.asyncio
    async def test_quoted_input_stripped(self, tool, tmp_path):
        """Input with quotes should be handled gracefully."""
        _create_file(tmp_path, "quoted.pptx")

        mock_client = MagicMock()
        mock_client.upload_file = AsyncMock(return_value="ok")
        mock_client.get_presigned_url = MagicMock(return_value="https://example.com/url")

        with patch("src.tools.storage_tool.get_agent_minio_client", return_value=mock_client):
            result = await tool._arun('"quoted.pptx"')

        assert "文件已上传" in result

    @pytest.mark.asyncio
    async def test_nested_file_upload(self, tool, tmp_path):
        """Files in subdirectories should upload with just the filename."""
        _create_file(tmp_path, "subdir/nested.pptx")

        mock_client = MagicMock()
        mock_client.upload_file = AsyncMock(return_value="ok")
        mock_client.get_presigned_url = MagicMock(return_value="https://example.com/url")

        with patch("src.tools.storage_tool.get_agent_minio_client", return_value=mock_client):
            result = await tool._arun("subdir/nested.pptx")

        assert "文件已上传" in result
        # object_name should use just the filename
        args, kwargs = mock_client.upload_file.call_args
        assert kwargs.get("object_name") == "agent-outputs/test-session-123/nested.pptx"

    @pytest.mark.asyncio
    async def test_presigned_url_called_with_correct_object_name(self, tool, tmp_path):
        """Verify get_presigned_url is called with the same object_name."""
        _create_file(tmp_path, "check.pptx")

        mock_client = MagicMock()
        mock_client.upload_file = AsyncMock(return_value="agent-outputs/test-session-123/check.pptx")
        mock_client.get_presigned_url = MagicMock(return_value="/minio/agent-outputs/test-session-123/check.pptx")

        with patch("src.tools.storage_tool.get_agent_minio_client", return_value=mock_client):
            result = await tool._arun("check.pptx")

        mock_client.get_presigned_url.assert_called_once_with("agent-outputs/test-session-123/check.pptx")

    @pytest.mark.asyncio
    async def test_error_format_consistency(self, tool, tmp_path):
        """Req 9.3: all errors start with [ERROR] upload_to_storage:"""
        bad_inputs = [
            "",
            "   ",
            "/absolute/path.pptx",
            "../../escape.pptx",
            "nonexistent.pptx",
        ]
        for inp in bad_inputs:
            result = await tool._arun(inp)
            assert result.startswith("[ERROR] upload_to_storage:"), f"Failed for input: {inp!r}"

    @pytest.mark.asyncio
    async def test_get_presigned_url_failure(self, tool, tmp_path):
        """Req 5.5: error when get_presigned_url fails."""
        _create_file(tmp_path, "url_fail.pptx")

        mock_client = MagicMock()
        mock_client.upload_file = AsyncMock(return_value="ok")
        mock_client.get_presigned_url = MagicMock(side_effect=Exception("URL generation failed"))

        with patch("src.tools.storage_tool.get_agent_minio_client", return_value=mock_client):
            result = await tool._arun("url_fail.pptx")

        assert result.startswith("[ERROR] upload_to_storage:")
        assert "URL generation failed" in result
