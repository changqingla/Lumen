"""Security and ownership regressions for the bootstrap setup_agent tool."""

from types import SimpleNamespace

from src.config.paths import Paths
from src.tools.builtins import setup_agent_tool


def _runtime(agent_name: str | None):
    return SimpleNamespace(
        context={"agent_name": agent_name},
        tool_call_id="tool-call-1",
    )


def _message(result) -> str:
    return result.update["messages"][0].content


def test_setup_agent_rejects_path_components(tmp_path, monkeypatch):
    paths = Paths(tmp_path)
    monkeypatch.setattr(setup_agent_tool, "get_paths", lambda: paths)

    result = setup_agent_tool.setup_agent.func(
        soul="private prompt",
        description="unsafe",
        runtime=_runtime("../../outside"),
    )

    assert _message(result) == "Error: agent creation failed"
    assert not (tmp_path.parent / "outside").exists()


def test_setup_agent_creates_valid_definition(tmp_path, monkeypatch):
    paths = Paths(tmp_path)
    monkeypatch.setattr(setup_agent_tool, "get_paths", lambda: paths)

    result = setup_agent_tool.setup_agent.func(
        soul="You are focused.",
        description="Focused agent",
        runtime=_runtime("Focused-Agent"),
    )

    agent_dir = paths.agent_dir("focused-agent")
    assert "created successfully" in _message(result)
    assert (agent_dir / "SOUL.md").read_text(encoding="utf-8") == (
        "You are focused."
    )
    assert "name: focused-agent" in (agent_dir / "config.yaml").read_text(
        encoding="utf-8"
    )


def test_setup_agent_failure_preserves_preexisting_directory(tmp_path, monkeypatch):
    paths = Paths(tmp_path)
    agent_dir = paths.agent_dir("existing-agent")
    agent_dir.mkdir(parents=True)
    marker = agent_dir / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    monkeypatch.setattr(setup_agent_tool, "get_paths", lambda: paths)

    result = setup_agent_tool.setup_agent.func(
        soul="replacement",
        description="must not overwrite",
        runtime=_runtime("existing-agent"),
    )

    assert _message(result) == "Error: agent creation failed"
    assert marker.read_text(encoding="utf-8") == "keep"
    assert not (agent_dir / "SOUL.md").exists()
