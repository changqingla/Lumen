"""Unit tests for AppendToFileTool (Task 5).

Validates Requirements 4.1–4.6: file append with path safety,
auto-creation of files/directories, and Python IO (no shell).

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
from pathlib import Path

import pytest

from src.tools.file_tools import AppendToFileTool, WriteFileTool


@pytest.fixture
def tool(tmp_path):
    """Create an AppendToFileTool with a temporary workspace."""
    t = AppendToFileTool()
    t.workspace_path = str(tmp_path)
    return t


@pytest.fixture
def write_tool(tmp_path):
    """Create a WriteFileTool with a temporary workspace."""
    t = WriteFileTool()
    t.workspace_path = str(tmp_path)
    return t


# ------------------------------------------------------------------
# _validate_path
# ------------------------------------------------------------------

class TestValidatePath:
    """Validates: Requirement 4.4 — path traversal prevention."""

    def test_relative_path_accepted(self, tool):
        ok, msg = tool._validate_path("scripts/generate.js")
        assert ok is True
        assert msg == ""

    def test_simple_filename_accepted(self, tool):
        ok, msg = tool._validate_path("output.txt")
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

    def test_deep_nested_path_accepted(self, tool):
        ok, msg = tool._validate_path("a/b/c/d/e.txt")
        assert ok is True
        assert msg == ""


# ------------------------------------------------------------------
# _arun — JSON parsing and file operations
# ------------------------------------------------------------------

class TestArun:
    """Validates: Requirements 4.1, 4.2, 4.3, 4.5, 4.6."""

    @pytest.mark.asyncio
    async def test_append_creates_file_if_not_exists(self, tool, tmp_path):
        """Req 4.2: auto-create file when it doesn't exist."""
        query = json.dumps({"path": "new_file.txt", "content": "hello"})
        result = await tool._arun(query)

        assert "已追加 5 字符到 new_file.txt" == result
        assert (tmp_path / "new_file.txt").read_text(encoding="utf-8") == "hello"

    @pytest.mark.asyncio
    async def test_append_to_existing_file(self, tool, tmp_path):
        """Req 4.1: append to end of existing file."""
        target = tmp_path / "existing.txt"
        target.write_text("first", encoding="utf-8")

        query = json.dumps({"path": "existing.txt", "content": " second"})
        result = await tool._arun(query)

        assert "已追加 7 字符到 existing.txt" == result
        assert target.read_text(encoding="utf-8") == "first second"

    @pytest.mark.asyncio
    async def test_auto_create_parent_directories(self, tool, tmp_path):
        """Req 4.3: auto-create parent directories."""
        query = json.dumps({"path": "deep/nested/dir/file.js", "content": "code"})
        result = await tool._arun(query)

        assert "已追加 4 字符到 deep/nested/dir/file.js" == result
        assert (tmp_path / "deep/nested/dir/file.js").read_text(encoding="utf-8") == "code"

    @pytest.mark.asyncio
    async def test_special_characters_preserved(self, tool, tmp_path):
        """Req 4.5: Python IO supports arbitrary characters."""
        content = 'const x = `hello ${"world"}`;\necho "test" && rm -rf /\n'
        query = json.dumps({"path": "script.js", "content": content})
        result = await tool._arun(query)

        assert "已追加" in result
        assert (tmp_path / "script.js").read_text(encoding="utf-8") == content

    @pytest.mark.asyncio
    async def test_returns_char_count(self, tool, tmp_path):
        """Req 4.6: return character count on success."""
        content = "x" * 100
        query = json.dumps({"path": "count.txt", "content": content})
        result = await tool._arun(query)

        assert result == "已追加 100 字符到 count.txt"

    @pytest.mark.asyncio
    async def test_path_traversal_rejected(self, tool):
        """Req 4.4: reject paths outside workspace."""
        query = json.dumps({"path": "../escape.txt", "content": "bad"})
        result = await tool._arun(query)

        assert result.startswith("[ERROR] append_to_file:")

    @pytest.mark.asyncio
    async def test_absolute_path_rejected(self, tool):
        query = json.dumps({"path": "/tmp/evil.txt", "content": "bad"})
        result = await tool._arun(query)

        assert result.startswith("[ERROR] append_to_file:")

    @pytest.mark.asyncio
    async def test_invalid_json_returns_error(self, tool):
        result = await tool._arun("not json at all")

        assert result.startswith("[ERROR] append_to_file:")

    @pytest.mark.asyncio
    async def test_missing_path_returns_error(self, tool):
        query = json.dumps({"content": "no path"})
        result = await tool._arun(query)

        assert result.startswith("[ERROR] append_to_file:")

    @pytest.mark.asyncio
    async def test_empty_content_allowed(self, tool, tmp_path):
        """Empty content is valid — creates file with nothing appended."""
        query = json.dumps({"path": "empty.txt", "content": ""})
        result = await tool._arun(query)

        assert result == "已追加 0 字符到 empty.txt"
        assert (tmp_path / "empty.txt").read_text(encoding="utf-8") == ""

    @pytest.mark.asyncio
    async def test_multiple_appends_concatenate(self, tool, tmp_path):
        """Multiple appends should concatenate content in order."""
        for chunk in ["aaa", "bbb", "ccc"]:
            query = json.dumps({"path": "multi.txt", "content": chunk})
            await tool._arun(query)

        assert (tmp_path / "multi.txt").read_text(encoding="utf-8") == "aaabbbccc"

    @pytest.mark.asyncio
    async def test_utf8_content(self, tool, tmp_path):
        """UTF-8 content including CJK characters."""
        content = "你好世界 🌍"
        query = json.dumps({"path": "utf8.txt", "content": content})
        result = await tool._arun(query)

        assert (tmp_path / "utf8.txt").read_text(encoding="utf-8") == content

    @pytest.mark.asyncio
    async def test_error_format_consistency(self, tool):
        """Req 9.3: all errors start with [ERROR] append_to_file:"""
        # Trigger various error paths
        bad_inputs = [
            "not json",
            json.dumps({"path": "", "content": "x"}),
            json.dumps({"path": "/abs/path", "content": "x"}),
            json.dumps({"path": "../../escape", "content": "x"}),
        ]
        for inp in bad_inputs:
            result = await tool._arun(inp)
            assert result.startswith("[ERROR] append_to_file:"), f"Failed for input: {inp}"


# ------------------------------------------------------------------
# WriteFileTool tests
# ------------------------------------------------------------------

class TestWriteFileArun:
    """Validates WriteFileTool: overwrite semantics, path safety, error handling."""

    @pytest.mark.asyncio
    async def test_creates_new_file(self, write_tool, tmp_path):
        query = json.dumps({"path": "new.js", "content": "console.log('hi');"})
        result = await write_tool._arun(query)

        assert "已写入" in result
        assert (tmp_path / "new.js").read_text(encoding="utf-8") == "console.log('hi');"

    @pytest.mark.asyncio
    async def test_overwrites_existing_file(self, write_tool, tmp_path):
        target = tmp_path / "overwrite.txt"
        target.write_text("old content", encoding="utf-8")

        query = json.dumps({"path": "overwrite.txt", "content": "new content"})
        result = await write_tool._arun(query)

        assert "已写入 11 字符到 overwrite.txt" == result
        assert target.read_text(encoding="utf-8") == "new content"

    @pytest.mark.asyncio
    async def test_auto_create_parent_directories(self, write_tool, tmp_path):
        query = json.dumps({"path": "deep/dir/file.js", "content": "code"})
        result = await write_tool._arun(query)

        assert "已写入" in result
        assert (tmp_path / "deep/dir/file.js").read_text(encoding="utf-8") == "code"

    @pytest.mark.asyncio
    async def test_path_traversal_rejected(self, write_tool):
        query = json.dumps({"path": "../escape.txt", "content": "bad"})
        result = await write_tool._arun(query)

        assert result.startswith("[ERROR] write_file:")

    @pytest.mark.asyncio
    async def test_absolute_path_rejected(self, write_tool):
        query = json.dumps({"path": "/tmp/evil.txt", "content": "bad"})
        result = await write_tool._arun(query)

        assert result.startswith("[ERROR] write_file:")

    @pytest.mark.asyncio
    async def test_invalid_json_returns_error(self, write_tool):
        result = await write_tool._arun("not json")

        assert result.startswith("[ERROR] write_file:")

    @pytest.mark.asyncio
    async def test_missing_path_returns_error(self, write_tool):
        query = json.dumps({"content": "no path"})
        result = await write_tool._arun(query)

        assert result.startswith("[ERROR] write_file:")

    @pytest.mark.asyncio
    async def test_special_characters_preserved(self, write_tool, tmp_path):
        content = 'const x = `hello ${"world"}`;\necho "test";\n'
        query = json.dumps({"path": "script.js", "content": content})
        await write_tool._arun(query)

        assert (tmp_path / "script.js").read_text(encoding="utf-8") == content

    @pytest.mark.asyncio
    async def test_utf8_content(self, write_tool, tmp_path):
        content = "你好世界 🌍"
        query = json.dumps({"path": "utf8.txt", "content": content})
        await write_tool._arun(query)

        assert (tmp_path / "utf8.txt").read_text(encoding="utf-8") == content

    @pytest.mark.asyncio
    async def test_second_write_replaces_first(self, write_tool, tmp_path):
        """Key difference from append: second write replaces, not concatenates."""
        await write_tool._arun(json.dumps({"path": "f.txt", "content": "first"}))
        await write_tool._arun(json.dumps({"path": "f.txt", "content": "second"}))

        assert (tmp_path / "f.txt").read_text(encoding="utf-8") == "second"
