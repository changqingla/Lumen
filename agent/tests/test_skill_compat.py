"""Tests for Anthropic Agent Skills compatibility features.

Tests the three key compatibility mechanisms:
1. Auto-expansion of .md sub-document references in SKILL.md body
2. Resource file staging into workspace directory
3. LoadSkillTool integration (expanded body + staging)
"""
import sys
import textwrap
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ── Stub langchain before importing our modules ─────────────────────────
_lc = types.ModuleType("langchain")
_lc_tools = types.ModuleType("langchain.tools")
_lc_core = types.ModuleType("langchain_core")
_lc_core_cb = types.ModuleType("langchain_core.callbacks")
_lc_core_tools = types.ModuleType("langchain_core.tools")


class _StubBaseTool:
    def __init__(self, **kwargs):
        pass

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)


_lc_tools.BaseTool = _StubBaseTool
_lc.tools = _lc_tools
_lc_core_cb.CallbackManagerForToolRun = type("CBM", (), {})
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
    _find_and_expand_md_references,
    _load_single_skill,
    stage_skill_resources,
    reset_skill_loader,
)
from src.skills.tools import LoadSkillTool
import src.skills.loader as loader_mod


# ── Helpers ──────────────────────────────────────────────────────────────

def _make_anthropic_style_skill(tmp_path: Path) -> Path:
    """Create a skill directory mimicking the Anthropic pptx skill structure.

    Structure:
        pptx/
        ├── SKILL.md          (references editing.md and pptxgenjs.md)
        ├── editing.md
        ├── pptxgenjs.md
        └── scripts/
            ├── thumbnail.py
            └── office/
                ├── soffice.py
                └── unpack.py
    """
    skill_dir = tmp_path / "pptx"
    skill_dir.mkdir()

    (skill_dir / "SKILL.md").write_text(textwrap.dedent("""\
        ---
        name: pptx
        description: "Use this skill any time a .pptx file is involved."
        ---
        # PPTX Skill

        | Task | Guide |
        |------|-------|
        | Edit or create from template | Read [editing.md](editing.md) |
        | Create from scratch | Read [pptxgenjs.md](pptxgenjs.md) |

        ## Reading Content

        ```bash
        python scripts/thumbnail.py presentation.pptx
        python scripts/office/unpack.py presentation.pptx unpacked/
        ```
    """), encoding="utf-8")

    (skill_dir / "editing.md").write_text(textwrap.dedent("""\
        # Editing Workflow

        1. Unpack the PPTX
        2. Edit XML slides
        3. Repack
    """), encoding="utf-8")

    (skill_dir / "pptxgenjs.md").write_text(textwrap.dedent("""\
        # Creating from Scratch with pptxgenjs

        ```javascript
        const PptxGenJS = require("pptxgenjs");
        const pptx = new PptxGenJS();
        ```
    """), encoding="utf-8")

    scripts_dir = skill_dir / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "thumbnail.py").write_text("# thumbnail script\nprint('hello')\n")

    office_dir = scripts_dir / "office"
    office_dir.mkdir()
    (office_dir / "soffice.py").write_text("# soffice wrapper\nprint('soffice')\n")
    (office_dir / "unpack.py").write_text("# unpack script\nprint('unpack')\n")

    return skill_dir


@pytest.fixture(autouse=True)
def _reset_loader():
    reset_skill_loader()
    yield
    reset_skill_loader()


# ── Test: _find_and_expand_md_references ─────────────────────────────────

class TestFindAndExpandMdReferences:
    def test_expands_referenced_md_files(self, tmp_path):
        skill_dir = _make_anthropic_style_skill(tmp_path)
        skill = _load_single_skill(skill_dir)
        assert skill is not None

        body = skill.body
        expanded = _find_and_expand_md_references(body, skill.skill_dir, skill.resource_files)

        assert len(expanded) == 2
        paths = [p for p, _ in expanded]
        assert "editing.md" in paths
        assert "pptxgenjs.md" in paths

    def test_expanded_content_is_correct(self, tmp_path):
        skill_dir = _make_anthropic_style_skill(tmp_path)
        skill = _load_single_skill(skill_dir)

        expanded = _find_and_expand_md_references(skill.body, skill.skill_dir, skill.resource_files)
        content_map = dict(expanded)

        assert "Editing Workflow" in content_map["editing.md"]
        assert "pptxgenjs" in content_map["pptxgenjs.md"]

    def test_deduplicates_references(self, tmp_path):
        """If the same .md is referenced twice, it should only appear once."""
        skill_dir = tmp_path / "dup-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(textwrap.dedent("""\
            ---
            name: dup
            description: Duplicate refs test.
            ---
            See [guide](guide.md) and also [guide again](guide.md).
        """), encoding="utf-8")
        (skill_dir / "guide.md").write_text("# Guide content\n")

        skill = _load_single_skill(skill_dir)
        expanded = _find_and_expand_md_references(skill.body, skill.skill_dir, skill.resource_files)
        assert len(expanded) == 1

    def test_ignores_nonexistent_md_references(self, tmp_path):
        """References to .md files that don't exist as resources are ignored."""
        skill_dir = tmp_path / "missing-ref"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(textwrap.dedent("""\
            ---
            name: missing
            description: Missing ref test.
            ---
            See [nonexistent](nonexistent.md).
        """), encoding="utf-8")

        skill = _load_single_skill(skill_dir)
        expanded = _find_and_expand_md_references(skill.body, skill.skill_dir, skill.resource_files)
        assert len(expanded) == 0

    def test_ignores_non_md_links(self, tmp_path):
        """Links to non-.md files (e.g. .py, .js) are not expanded."""
        skill_dir = tmp_path / "non-md"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(textwrap.dedent("""\
            ---
            name: nonmd
            description: Non-md link test.
            ---
            See [script](script.py) and [doc](doc.md).
        """), encoding="utf-8")
        (skill_dir / "script.py").write_text("print('hi')\n")
        (skill_dir / "doc.md").write_text("# Doc\n")

        skill = _load_single_skill(skill_dir)
        expanded = _find_and_expand_md_references(skill.body, skill.skill_dir, skill.resource_files)
        assert len(expanded) == 1
        assert expanded[0][0] == "doc.md"

    def test_no_references_returns_empty(self, tmp_path):
        skill_dir = tmp_path / "no-refs"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(textwrap.dedent("""\
            ---
            name: norefs
            description: No references.
            ---
            # Just plain content, no links.
        """), encoding="utf-8")

        skill = _load_single_skill(skill_dir)
        expanded = _find_and_expand_md_references(skill.body, skill.skill_dir, skill.resource_files)
        assert len(expanded) == 0

    def test_strips_frontmatter_from_subdocs(self, tmp_path):
        """Sub-documents with their own frontmatter should have it stripped."""
        skill_dir = tmp_path / "fm-sub"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(textwrap.dedent("""\
            ---
            name: fmsub
            description: Frontmatter sub-doc test.
            ---
            Read [sub](sub.md).
        """), encoding="utf-8")
        (skill_dir / "sub.md").write_text(textwrap.dedent("""\
            ---
            title: Sub Document
            ---
            # Actual Content Here
        """), encoding="utf-8")

        skill = _load_single_skill(skill_dir)
        expanded = _find_and_expand_md_references(skill.body, skill.skill_dir, skill.resource_files)
        assert len(expanded) == 1
        _, content = expanded[0]
        assert "Actual Content Here" in content
        assert "title: Sub Document" not in content


# ── Test: get_expanded_body ──────────────────────────────────────────────

class TestGetExpandedBody:
    def test_includes_original_body(self, tmp_path):
        skill_dir = _make_anthropic_style_skill(tmp_path)
        skill = _load_single_skill(skill_dir)
        expanded = skill.get_expanded_body()
        assert "PPTX Skill" in expanded

    def test_includes_inlined_subdocs(self, tmp_path):
        skill_dir = _make_anthropic_style_skill(tmp_path)
        skill = _load_single_skill(skill_dir)
        expanded = skill.get_expanded_body()
        assert "Editing Workflow" in expanded
        assert "pptxgenjs" in expanded
        assert "PptxGenJS" in expanded

    def test_no_subdocs_returns_plain_body(self, tmp_path):
        skill_dir = tmp_path / "plain"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(textwrap.dedent("""\
            ---
            name: plain
            description: Plain skill.
            ---
            # Just content
        """), encoding="utf-8")

        skill = _load_single_skill(skill_dir)
        assert skill.get_expanded_body() == skill.body


# ── Test: stage_skill_resources ──────────────────────────────────────────

class TestStageSkillResources:
    def test_copies_all_resources(self, tmp_path):
        skill_dir = _make_anthropic_style_skill(tmp_path)
        skill = _load_single_skill(skill_dir)
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        copied = stage_skill_resources(skill, str(workspace))

        # Should copy: editing.md, pptxgenjs.md, scripts/thumbnail.py,
        # scripts/office/soffice.py, scripts/office/unpack.py = 5 files
        assert copied == 5

    def test_preserves_directory_structure(self, tmp_path):
        skill_dir = _make_anthropic_style_skill(tmp_path)
        skill = _load_single_skill(skill_dir)
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        stage_skill_resources(skill, str(workspace))

        assert (workspace / "editing.md").is_file()
        assert (workspace / "pptxgenjs.md").is_file()
        assert (workspace / "scripts" / "thumbnail.py").is_file()
        assert (workspace / "scripts" / "office" / "soffice.py").is_file()
        assert (workspace / "scripts" / "office" / "unpack.py").is_file()

    def test_file_content_matches(self, tmp_path):
        skill_dir = _make_anthropic_style_skill(tmp_path)
        skill = _load_single_skill(skill_dir)
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        stage_skill_resources(skill, str(workspace))

        original = (skill_dir / "scripts" / "thumbnail.py").read_text()
        staged = (workspace / "scripts" / "thumbnail.py").read_text()
        assert staged == original

    def test_does_not_copy_skill_md(self, tmp_path):
        skill_dir = _make_anthropic_style_skill(tmp_path)
        skill = _load_single_skill(skill_dir)
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        stage_skill_resources(skill, str(workspace))

        assert not (workspace / "SKILL.md").exists()

    def test_empty_resources_returns_zero(self, tmp_path):
        skill_dir = tmp_path / "empty-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(textwrap.dedent("""\
            ---
            name: empty
            description: No resources.
            ---
            # Content
        """), encoding="utf-8")

        skill = _load_single_skill(skill_dir)
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        assert stage_skill_resources(skill, str(workspace)) == 0

    def test_does_not_overwrite_existing_workspace_files(self, tmp_path):
        """If workspace already has a file at the same path, staging overwrites it
        (this is expected — skill resources take precedence)."""
        skill_dir = _make_anthropic_style_skill(tmp_path)
        skill = _load_single_skill(skill_dir)
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        # Pre-create a file that will be overwritten
        scripts_dir = workspace / "scripts"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "thumbnail.py").write_text("# old content\n")

        stage_skill_resources(skill, str(workspace))

        content = (workspace / "scripts" / "thumbnail.py").read_text()
        assert "# thumbnail script" in content  # skill version, not old


# ── Test: LoadSkillTool integration ──────────────────────────────────────

class TestLoadSkillToolCompat:
    def _setup_loader(self, tmp_path):
        _make_anthropic_style_skill(tmp_path)
        loader = SkillLoader(skills_dir=str(tmp_path))
        loader.discover()
        return loader

    def test_load_skill_returns_expanded_body(self, tmp_path):
        loader = self._setup_loader(tmp_path)
        original = loader_mod._global_loader
        loader_mod._global_loader = loader

        try:
            tool = LoadSkillTool()
            result = tool._run("pptx")

            # Should contain inlined sub-documents
            assert "Editing Workflow" in result
            assert "pptxgenjs" in result
            assert "PptxGenJS" in result
        finally:
            loader_mod._global_loader = original

    def test_load_skill_stages_resources_to_workspace(self, tmp_path):
        loader = self._setup_loader(tmp_path)
        original = loader_mod._global_loader
        loader_mod._global_loader = loader

        workspace = tmp_path / "workspace"
        workspace.mkdir()

        try:
            tool = LoadSkillTool()
            tool.workspace_path = str(workspace)
            result = tool._run("pptx")

            # Resources should be staged
            assert (workspace / "scripts" / "thumbnail.py").is_file()
            assert (workspace / "scripts" / "office" / "soffice.py").is_file()
            # Result should mention staging
            assert "资源文件" in result
        finally:
            loader_mod._global_loader = original

    def test_load_skill_without_workspace_skips_staging(self, tmp_path):
        loader = self._setup_loader(tmp_path)
        original = loader_mod._global_loader
        loader_mod._global_loader = loader

        try:
            tool = LoadSkillTool()
            # workspace_path is empty string (default)
            result = tool._run("pptx")

            # Should still work, just no staging message
            assert "PPTX Skill" in result
        finally:
            loader_mod._global_loader = original

    def test_non_md_resources_listed_for_read_skill_resource(self, tmp_path):
        """Non-.md resource files should still be listed for read_skill_resource."""
        loader = self._setup_loader(tmp_path)
        original = loader_mod._global_loader
        loader_mod._global_loader = loader

        try:
            tool = LoadSkillTool()
            result = tool._run("pptx")

            # .py scripts should be listed as available resources
            assert "scripts/thumbnail.py" in result
            assert "scripts/office/soffice.py" in result
            # .md files should NOT be listed (they were inlined)
            assert "read_skill_resource" in result
        finally:
            loader_mod._global_loader = original

    def test_load_skill_no_runtime_sets_none(self, tmp_path):
        """Anthropic skills without runtime field should set active_skill_runtime to None."""
        loader = self._setup_loader(tmp_path)
        original = loader_mod._global_loader
        loader_mod._global_loader = loader

        try:
            tool = LoadSkillTool()
            state = {"active_skill_runtime": {"image": "old"}}
            tool.set_state_ref(state)
            tool._run("pptx")

            assert state["active_skill_runtime"] is None
        finally:
            loader_mod._global_loader = original
