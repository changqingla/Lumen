"""Security regressions for bounded thread-file access and snapshots."""

import asyncio
import os
import threading

import pytest

from src.config.paths import Paths
from src.utils import thread_files
from src.utils.thread_files import (
    ThreadFileAccessError,
    ThreadFileChangedError,
    ThreadFileSnapshot,
    ThreadFileTooLargeError,
    resolve_thread_file,
    snapshot_thread_file,
)


def _resolved_output(paths: Paths, filename: str = "report.txt"):
    return resolve_thread_file(
        paths,
        "thread-1",
        f"/mnt/user-data/outputs/{filename}",
        allowed_subdirs=frozenset({"outputs"}),
    )


def test_snapshot_thread_file_copies_private_bounded_content(tmp_path):
    paths = Paths(tmp_path)
    source = paths.sandbox_outputs_dir("thread-1") / "report.txt"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"report content")

    snapshot = snapshot_thread_file(
        _resolved_output(paths),
        max_bytes=1024,
        suffix=".txt",
    )
    try:
        assert snapshot.path != source
        assert snapshot.path.read_bytes() == b"report content"
        assert snapshot.size == len(b"report content")
        assert snapshot.path.stat().st_mode & 0o777 == 0o400
    finally:
        snapshot.cleanup()
    assert not snapshot.path.exists()


def test_snapshot_rejects_final_file_replaced_by_symlink_after_resolution(tmp_path):
    paths = Paths(tmp_path)
    source = paths.sandbox_outputs_dir("thread-1") / "report.txt"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"initial")
    resolved = _resolved_output(paths)
    outside = tmp_path / "outside-secret.txt"
    outside.write_bytes(b"must not be copied")

    source.unlink()
    source.symlink_to(outside)

    with pytest.raises(ThreadFileAccessError):
        snapshot_thread_file(resolved, max_bytes=1024)


def test_snapshot_rejects_directory_replaced_by_symlink_after_resolution(tmp_path):
    paths = Paths(tmp_path)
    nested = paths.sandbox_outputs_dir("thread-1") / "nested"
    nested.mkdir(parents=True)
    source = nested / "report.txt"
    source.write_bytes(b"initial")
    resolved = resolve_thread_file(
        paths,
        "thread-1",
        "/mnt/user-data/outputs/nested/report.txt",
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "report.txt").write_bytes(b"must not be copied")

    source.unlink()
    nested.rmdir()
    nested.symlink_to(outside, target_is_directory=True)

    with pytest.raises(ThreadFileAccessError):
        snapshot_thread_file(resolved, max_bytes=1024)


def test_snapshot_detects_source_mutation_and_removes_partial_copy(
    tmp_path,
    monkeypatch,
):
    paths = Paths(tmp_path)
    source = paths.sandbox_outputs_dir("thread-1") / "report.txt"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"original content")
    resolved = _resolved_output(paths)
    original_read = os.read
    original_mkstemp = thread_files.tempfile.mkstemp
    changed = False

    def mutate_after_read(file_fd: int, size: int) -> bytes:
        nonlocal changed
        chunk = original_read(file_fd, size)
        if chunk and not changed:
            changed = True
            source.write_bytes(b"modified content with a different size")
        return chunk

    def local_mkstemp(*args, **kwargs):
        return original_mkstemp(*args, dir=tmp_path, **kwargs)

    monkeypatch.setattr(thread_files.os, "read", mutate_after_read)
    monkeypatch.setattr(thread_files.tempfile, "mkstemp", local_mkstemp)

    with pytest.raises(ThreadFileChangedError):
        snapshot_thread_file(resolved, max_bytes=1024)

    assert not list(tmp_path.glob("lumen-thread-file-*"))


def test_snapshot_rejects_oversized_source_before_copy(tmp_path):
    paths = Paths(tmp_path)
    source = paths.sandbox_outputs_dir("thread-1") / "report.txt"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"too large")

    with pytest.raises(ThreadFileTooLargeError):
        snapshot_thread_file(_resolved_output(paths), max_bytes=4)


def test_async_snapshot_reclaims_completed_copy_when_cancelled(
    tmp_path,
    monkeypatch,
):
    snapshot_path = tmp_path / "cancelled-snapshot"
    started = threading.Event()
    release = threading.Event()

    def delayed_snapshot(*_args, **_kwargs):
        snapshot_path.write_bytes(b"private copy")
        started.set()
        release.wait(timeout=5)
        return ThreadFileSnapshot(path=snapshot_path, size=12)

    monkeypatch.setattr(thread_files, "snapshot_thread_file", delayed_snapshot)

    async def cancel_during_copy():
        task = asyncio.create_task(
            thread_files.snapshot_thread_file_async(
                _resolved_output(Paths(tmp_path)),
                max_bytes=1024,
            )
        )
        while not started.is_set():
            await asyncio.sleep(0)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(cancel_during_copy())

    assert not snapshot_path.exists()


@pytest.mark.parametrize(
    "virtual_path",
    [
        "/mnt/user-data/outputs/../uploads/secret.txt",
        "/mnt/user-data/outputs//secret.txt",
        "/mnt/user-data/outputs\\secret.txt",
        "/etc/passwd",
    ],
)
def test_resolve_thread_file_rejects_ambiguous_or_escaping_paths(
    tmp_path,
    virtual_path,
):
    with pytest.raises(ThreadFileAccessError):
        resolve_thread_file(Paths(tmp_path), "thread-1", virtual_path)
