"""Tests for SkillLoader runtime field parsing (Task 2).

We import directly from the loader module to avoid pulling in langchain
via the skills package __init__.py.
"""
import sys
import textwrap
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Stub out langchain modules so __init__.py doesn't fail on import
for _mod in ("langchain", "langchain.tools", "langchain_core", "langchain_core.callbacks"):
    sys.modules.setdefault(_mod, MagicMock())

from src.skills.loader import SkillRuntime, SkillMetadata, _load_single_skill


# ── SkillRuntime dataclass defaults ──────────────────────────────────────

class TestSkillRuntimeDefaults:
    def test_all_defaults(self):
        rt = SkillRuntime(image="node:20-slim")
        assert rt.image == "node:20-slim"
        assert rt.network == "none"
        assert rt.memory == "512m"
        assert rt.timeout == 120

    def test_custom_values(self):
        rt = SkillRuntime(image="python:3.12", network="bridge", memory="1g", timeout=300)
        assert rt.image == "python:3.12"
        assert rt.network == "bridge"
        assert rt.memory == "1g"
        assert rt.timeout == 300


# ── Helper ───────────────────────────────────────────────────────────────

def _make_skill_dir(tmp_path: Path, frontmatter: str) -> Path:
    """Create a skill directory with a SKILL.md containing given frontmatter."""
    skill_dir = tmp_path / "test-skill"
    skill_dir.mkdir(exist_ok=True)
    (skill_dir / "SKILL.md").write_text(frontmatter, encoding="utf-8")
    return skill_dir


# ── _load_single_skill runtime parsing ───────────────────────────────────

class TestLoadSingleSkillRuntime:
    def test_runtime_with_all_fields(self, tmp_path):
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
            # Instructions
        """)
        skill = _load_single_skill(_make_skill_dir(tmp_path, fm))
        assert skill is not None
        assert skill.runtime is not None
        assert skill.runtime.image == "node:20-slim"
        assert skill.runtime.network == "bridge"
        assert skill.runtime.memory == "1g"
        assert skill.runtime.timeout == 300

    def test_runtime_with_only_image_uses_defaults(self, tmp_path):
        fm = textwrap.dedent("""\
            ---
            name: pptx-gen
            description: Generate PPTX files.
            runtime:
              image: node:20-slim
            ---
            # Instructions
        """)
        skill = _load_single_skill(_make_skill_dir(tmp_path, fm))
        assert skill is not None
        assert skill.runtime is not None
        assert skill.runtime.image == "node:20-slim"
        assert skill.runtime.network == "none"
        assert skill.runtime.memory == "512m"
        assert skill.runtime.timeout == 120

    def test_no_runtime_field_gives_none(self, tmp_path):
        fm = textwrap.dedent("""\
            ---
            name: basic-skill
            description: A basic skill without runtime.
            ---
            # Instructions
        """)
        skill = _load_single_skill(_make_skill_dir(tmp_path, fm))
        assert skill is not None
        assert skill.runtime is None

    def test_runtime_without_image_gives_none(self, tmp_path):
        fm = textwrap.dedent("""\
            ---
            name: bad-runtime
            description: Runtime missing image field.
            runtime:
              network: bridge
              memory: 1g
            ---
            # Instructions
        """)
        skill = _load_single_skill(_make_skill_dir(tmp_path, fm))
        assert skill is not None
        assert skill.runtime is None

    def test_runtime_as_non_dict_gives_none(self, tmp_path):
        fm = textwrap.dedent("""\
            ---
            name: string-runtime
            description: Runtime is a string, not a dict.
            runtime: "some-string"
            ---
            # Instructions
        """)
        skill = _load_single_skill(_make_skill_dir(tmp_path, fm))
        assert skill is not None
        assert skill.runtime is None

    def test_skill_metadata_runtime_default_is_none(self):
        meta = SkillMetadata(
            name="test",
            description="test",
            skill_dir=Path("/tmp"),
            skill_file=Path("/tmp/SKILL.md"),
        )
        assert meta.runtime is None
