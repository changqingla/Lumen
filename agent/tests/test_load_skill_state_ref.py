"""Tests for LoadSkillTool state_ref injection (Task 3).

Validates Requirements 3.3 and 3.4:
- Loading a skill WITH runtime writes runtime config to state["active_skill_runtime"]
- Loading a skill WITHOUT runtime sets state["active_skill_runtime"] to None

We test the core logic by directly calling _run on the LoadSkillTool,
with the global skill loader patched to return controlled SkillMetadata.
"""
import sys
import textwrap
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ── Stub langchain before importing our modules ─────────────────────────
# We need BaseTool to be a real class (not MagicMock) so LoadSkillTool
# can be instantiated properly.

_lc = types.ModuleType("langchain")
_lc_tools = types.ModuleType("langchain.tools")
_lc_core = types.ModuleType("langchain_core")
_lc_core_cb = types.ModuleType("langchain_core.callbacks")
_lc_core_tools = types.ModuleType("langchain_core.tools")


class _StubBaseTool:
    """Minimal BaseTool stub that allows subclass instantiation."""
    def __init__(self, **kwargs):
        # Apply class-level defaults
        for attr in dir(type(self)):
            if attr.startswith("_"):
                continue
            val = getattr(type(self), attr, None)
            if not callable(val) or isinstance(val, property):
                pass  # keep class attrs accessible

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

from src.skills.loader import (
    SkillLoader,
    SkillMetadata,
    SkillRuntime,
    reset_skill_loader,
)
from src.skills.tools import LoadSkillTool
import src.skills.loader as loader_mod


# ── Helpers ──────────────────────────────────────────────────────────────

def _make_skill_dir(tmp_path: Path, name: str, frontmatter: str) -> Path:
    skill_dir = tmp_path / name
    skill_dir.mkdir(exist_ok=True)
    (skill_dir / "SKILL.md").write_text(frontmatter, encoding="utf-8")
    return skill_dir


@pytest.fixture(autouse=True)
def _reset_loader():
    reset_skill_loader()
    yield
    reset_skill_loader()


def _setup_loader(tmp_path, name, frontmatter):
    """Create a skill dir, discover it, and patch the global loader."""
    _make_skill_dir(tmp_path, name, frontmatter)
    loader = SkillLoader(skills_dir=str(tmp_path))
    loader.discover()
    return loader


# ── Tests ────────────────────────────────────────────────────────────────

class TestLoadSkillToolStateRef:
    """Test that LoadSkillTool correctly writes runtime info to state."""

    def test_set_state_ref_stores_reference(self):
        tool = LoadSkillTool()
        state = {"some": "data"}
        tool.set_state_ref(state)
        assert tool._state_ref is state

    def test_state_ref_default_is_none(self):
        tool = LoadSkillTool()
        assert tool._state_ref is None

    def test_load_skill_with_runtime_writes_to_state(self, tmp_path):
        """Req 3.3: loading a skill with runtime writes config to active_skill_runtime."""
        fm = textwrap.dedent("""\
            ---
            name: pptx-gen
            description: Generate PPTX files.
            runtime:
              image: node:20-slim
              network: bridge
              memory: 1g
              timeout: 300
            ---
            # Instructions here
        """)
        loader = _setup_loader(tmp_path, "pptx-gen", fm)
        original = loader_mod._global_loader
        loader_mod._global_loader = loader

        try:
            tool = LoadSkillTool()
            state = {"active_skill_runtime": None}
            tool.set_state_ref(state)

            result = tool._run("pptx-gen")

            assert "pptx-gen" in result
            assert state["active_skill_runtime"] == {
                "image": "node:20-slim",
                "network": "bridge",
                "memory": "1g",
                "timeout": 300,
            }
        finally:
            loader_mod._global_loader = original

    def test_load_skill_without_runtime_sets_none(self, tmp_path):
        """Req 3.4: loading a skill without runtime sets active_skill_runtime to None."""
        fm = textwrap.dedent("""\
            ---
            name: basic-skill
            description: A basic skill.
            ---
            # Basic instructions
        """)
        loader = _setup_loader(tmp_path, "basic-skill", fm)
        original = loader_mod._global_loader
        loader_mod._global_loader = loader

        try:
            tool = LoadSkillTool()
            state = {"active_skill_runtime": {"image": "old:image"}}
            tool.set_state_ref(state)

            tool._run("basic-skill")

            assert state["active_skill_runtime"] is None
        finally:
            loader_mod._global_loader = original

    def test_load_skill_without_state_ref_does_not_crash(self, tmp_path):
        """When _state_ref is None, loading a skill should still work normally."""
        fm = textwrap.dedent("""\
            ---
            name: safe-skill
            description: Should not crash.
            runtime:
              image: node:20-slim
            ---
            # Instructions
        """)
        loader = _setup_loader(tmp_path, "safe-skill", fm)
        original = loader_mod._global_loader
        loader_mod._global_loader = loader

        try:
            tool = LoadSkillTool()
            # Don't call set_state_ref — _state_ref stays None
            result = tool._run("safe-skill")
            assert "safe-skill" in result
        finally:
            loader_mod._global_loader = original

    def test_load_skill_runtime_defaults(self, tmp_path):
        """Runtime with only image should use default values for other fields."""
        fm = textwrap.dedent("""\
            ---
            name: minimal-rt
            description: Minimal runtime skill.
            runtime:
              image: python:3.12
            ---
            # Instructions
        """)
        loader = _setup_loader(tmp_path, "minimal-rt", fm)
        original = loader_mod._global_loader
        loader_mod._global_loader = loader

        try:
            tool = LoadSkillTool()
            state = {}
            tool.set_state_ref(state)

            tool._run("minimal-rt")

            assert state["active_skill_runtime"] == {
                "image": "python:3.12",
                "network": "none",
                "memory": "512m",
                "timeout": 120,
            }
        finally:
            loader_mod._global_loader = original

    def test_load_nonexistent_skill_does_not_write_state(self, tmp_path):
        """Loading a skill that doesn't exist should not modify state."""
        loader = SkillLoader(skills_dir=str(tmp_path))
        loader.discover()
        original = loader_mod._global_loader
        loader_mod._global_loader = loader

        try:
            tool = LoadSkillTool()
            state = {}
            tool.set_state_ref(state)

            result = tool._run("nonexistent")

            assert "未找到" in result
            assert "active_skill_runtime" not in state
        finally:
            loader_mod._global_loader = original
