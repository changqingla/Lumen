"""Security and behavior tests for the view_image tool."""

import importlib
from types import SimpleNamespace

import pytest

from src.config.paths import Paths
from src.utils.image_files import (
    MAX_VIEW_IMAGE_BYTES,
    ImageFileError,
    load_image_file,
    resolve_image_path,
)

view_image_tool_module = importlib.import_module("src.tools.builtins.view_image_tool")


def _make_runtime(thread_id: str = "thread-1") -> SimpleNamespace:
    return SimpleNamespace(state={}, context={"thread_id": thread_id})


def _message(result) -> str:
    return result.update["messages"][0].content


def test_view_image_reads_image_from_current_thread(tmp_path, monkeypatch):
    paths = Paths(tmp_path)
    image_path = paths.sandbox_uploads_dir("thread-1") / "pixel.png"
    image_path.parent.mkdir(parents=True)
    image_bytes = b"small image payload"
    image_path.write_bytes(image_bytes)
    monkeypatch.setattr(view_image_tool_module, "get_paths", lambda: paths)

    result = view_image_tool_module.view_image_tool.func(
        runtime=_make_runtime(),
        image_path="/mnt/user-data/uploads/pixel.png",
        tool_call_id="tc-1",
    )

    assert _message(result) == "Successfully read image"
    assert result.update["messages"][0].status == "success"
    assert set(result.update) == {"messages"}


def test_view_image_rejects_path_traversal(tmp_path, monkeypatch):
    paths = Paths(tmp_path)
    monkeypatch.setattr(view_image_tool_module, "get_paths", lambda: paths)

    result = view_image_tool_module.view_image_tool.func(
        runtime=_make_runtime(),
        image_path="/mnt/user-data/uploads/../../other-thread/secret.png",
        tool_call_id="tc-2",
    )

    assert _message(result).startswith("Error: Image path must be inside the current thread's /mnt/user-data directory")
    assert result.update["messages"][0].status == "error"


def test_view_image_rejects_absolute_host_path_even_inside_thread(tmp_path, monkeypatch):
    paths = Paths(tmp_path)
    host_image = paths.sandbox_uploads_dir("thread-1") / "host-path.png"
    host_image.parent.mkdir(parents=True)
    host_image.write_bytes(b"not accessible")
    monkeypatch.setattr(view_image_tool_module, "get_paths", lambda: paths)

    result = view_image_tool_module.view_image_tool.func(
        runtime=_make_runtime(),
        image_path=str(host_image),
        tool_call_id="tc-3",
    )

    assert _message(result).startswith("Error: Image path must be inside the current thread's /mnt/user-data directory")


def test_view_image_rejects_symlink_escape(tmp_path, monkeypatch):
    paths = Paths(tmp_path)
    uploads_dir = paths.sandbox_uploads_dir("thread-1")
    uploads_dir.mkdir(parents=True)
    host_image = tmp_path / "host-secret.png"
    host_image.write_bytes(b"not accessible")
    (uploads_dir / "escape.png").symlink_to(host_image)
    monkeypatch.setattr(view_image_tool_module, "get_paths", lambda: paths)

    result = view_image_tool_module.view_image_tool.func(
        runtime=_make_runtime(),
        image_path="/mnt/user-data/uploads/escape.png",
        tool_call_id="tc-4",
    )

    assert _message(result).startswith("Error reading image file")


def test_image_open_rejects_file_replaced_by_symlink_after_resolution(tmp_path):
    paths = Paths(tmp_path)
    image_path = paths.sandbox_uploads_dir("thread-1") / "pixel.png"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"initial image")
    resolved = resolve_image_path(
        paths,
        "thread-1",
        "/mnt/user-data/uploads/pixel.png",
    )
    outside = tmp_path / "outside-secret.png"
    outside.write_bytes(b"must not be read")

    image_path.unlink()
    image_path.symlink_to(outside)

    with pytest.raises(ImageFileError, match="securely read"):
        load_image_file(resolved)


def test_image_open_rejects_directory_replaced_by_symlink_after_resolution(
    tmp_path,
):
    paths = Paths(tmp_path)
    nested_dir = paths.sandbox_outputs_dir("thread-1") / "charts"
    nested_dir.mkdir(parents=True)
    (nested_dir / "pixel.png").write_bytes(b"initial image")
    resolved = resolve_image_path(
        paths,
        "thread-1",
        "/mnt/user-data/outputs/charts/pixel.png",
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "pixel.png").write_bytes(b"must not be read")

    (nested_dir / "pixel.png").unlink()
    nested_dir.rmdir()
    nested_dir.symlink_to(outside, target_is_directory=True)

    with pytest.raises(ImageFileError, match="securely read"):
        load_image_file(resolved)


def test_view_image_rejects_oversized_files_without_putting_bytes_in_state(tmp_path, monkeypatch):
    paths = Paths(tmp_path)
    image_path = paths.sandbox_uploads_dir("thread-1") / "large.png"
    image_path.parent.mkdir(parents=True)
    with image_path.open("wb") as stream:
        stream.truncate(MAX_VIEW_IMAGE_BYTES + 1)
    monkeypatch.setattr(view_image_tool_module, "get_paths", lambda: paths)

    result = view_image_tool_module.view_image_tool.func(
        runtime=_make_runtime(),
        image_path="/mnt/user-data/uploads/large.png",
        tool_call_id="tc-large",
    )

    assert "size limit" in _message(result)
    assert result.update["messages"][0].status == "error"
    assert set(result.update) == {"messages"}
