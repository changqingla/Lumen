"""Secure, bounded access to files inside a Runtime thread directory."""

from __future__ import annotations

import asyncio
import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path

from src.config.paths import VIRTUAL_PATH_PREFIX, Paths

THREAD_FILE_COPY_CHUNK_SIZE = 64 * 1024


class ThreadFileError(ValueError):
    """Base class for stable thread-file access failures."""


class ThreadFileNotFoundError(ThreadFileError):
    """Raised when a requested thread file does not exist."""


class ThreadFileAccessError(ThreadFileError):
    """Raised when a path cannot be opened without following links."""


class ThreadFileNotRegularError(ThreadFileError):
    """Raised when a requested path is not a regular file."""


class ThreadFileTooLargeError(ThreadFileError):
    """Raised when a file exceeds the caller's byte limit."""


class ThreadFileChangedError(ThreadFileError):
    """Raised when a file changes while an immutable snapshot is copied."""


@dataclass(frozen=True)
class ResolvedThreadFile:
    """A lexical path anchored to one trusted thread user-data directory."""

    root: Path
    parts: tuple[str, ...]

    @property
    def path(self) -> Path:
        return self.root.joinpath(*self.parts)


@dataclass(frozen=True)
class ThreadFileSnapshot:
    """A private immutable-on-disk copy owned by the current process."""

    path: Path
    size: int

    def cleanup(self) -> None:
        self.path.unlink(missing_ok=True)


def resolve_thread_file(
    paths: Paths,
    thread_id: str,
    virtual_path: str,
    *,
    allowed_subdirs: frozenset[str] | None = None,
) -> ResolvedThreadFile:
    """Resolve a virtual path lexically without following user-controlled links."""

    raw_path = str(virtual_path or "")
    if "\\" in raw_path or "\x00" in raw_path:
        raise ThreadFileAccessError(f"Path must be inside {VIRTUAL_PATH_PREFIX}")

    stripped = raw_path.lstrip("/")
    prefix = VIRTUAL_PATH_PREFIX.lstrip("/")
    if not stripped.startswith(prefix + "/"):
        raise ThreadFileAccessError(f"Path must be inside {VIRTUAL_PATH_PREFIX}")

    relative = stripped[len(prefix) + 1 :]
    parts = tuple(relative.split("/"))
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ThreadFileAccessError(f"Path must be inside {VIRTUAL_PATH_PREFIX}")
    if allowed_subdirs is not None and parts[0] not in allowed_subdirs:
        raise ThreadFileAccessError("Thread file is outside the allowed directory")

    try:
        root = paths.sandbox_user_data_dir(thread_id).resolve()
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ThreadFileAccessError("Invalid thread file path") from exc
    return ResolvedThreadFile(root=root, parts=parts)


def open_thread_file(
    resolved: ResolvedThreadFile,
    *,
    max_bytes: int | None = None,
) -> tuple[int, os.stat_result]:
    """Open a regular thread file without following any path-component symlink."""

    if not hasattr(os, "O_NOFOLLOW"):
        raise ThreadFileAccessError("Secure thread file access is unavailable")
    if max_bytes is not None and max_bytes < 0:
        raise ValueError("max_bytes must be non-negative")

    common_flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
    directory_flags = common_flags | os.O_DIRECTORY
    directory_fd: int | None = None
    file_fd: int | None = None
    return_file_fd = False
    try:
        directory_fd = os.open(resolved.root, directory_flags)
        for component in resolved.parts[:-1]:
            next_fd = os.open(component, directory_flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
        file_fd = os.open(
            resolved.parts[-1],
            common_flags,
            dir_fd=directory_fd,
        )
        file_stat = os.fstat(file_fd)
        if not stat.S_ISREG(file_stat.st_mode):
            raise ThreadFileNotRegularError("Thread path is not a regular file")
        if max_bytes is not None and file_stat.st_size > max_bytes:
            raise ThreadFileTooLargeError("Thread file exceeds the size limit")
        return_file_fd = True
        return file_fd, file_stat
    except FileNotFoundError as exc:
        raise ThreadFileNotFoundError("Thread file not found") from exc
    except ThreadFileError:
        raise
    except OSError as exc:
        raise ThreadFileAccessError("Unable to securely open thread file") from exc
    finally:
        if directory_fd is not None:
            os.close(directory_fd)
        if file_fd is not None and not return_file_fd:
            os.close(file_fd)


def _snapshot_identity(file_stat: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        file_stat.st_dev,
        file_stat.st_ino,
        file_stat.st_size,
        file_stat.st_mtime_ns,
        file_stat.st_ctime_ns,
    )


def snapshot_thread_file(
    resolved: ResolvedThreadFile,
    *,
    max_bytes: int,
    suffix: str = "",
) -> ThreadFileSnapshot:
    """Copy one thread file to a private bounded snapshot and detect mutations."""

    source_fd, before = open_thread_file(resolved, max_bytes=max_bytes)
    snapshot_fd: int | None = None
    snapshot_path: Path | None = None
    try:
        snapshot_fd, raw_snapshot_path = tempfile.mkstemp(
            prefix="lumen-thread-file-",
            suffix=suffix,
        )
        snapshot_path = Path(raw_snapshot_path)
        copied = 0
        while chunk := os.read(
            source_fd,
            min(THREAD_FILE_COPY_CHUNK_SIZE, max_bytes - copied + 1),
        ):
            copied += len(chunk)
            if copied > max_bytes:
                raise ThreadFileTooLargeError("Thread file exceeds the size limit")
            view = memoryview(chunk)
            while view:
                written = os.write(snapshot_fd, view)
                view = view[written:]

        after = os.fstat(source_fd)
        if copied != before.st_size or _snapshot_identity(before) != _snapshot_identity(after):
            raise ThreadFileChangedError("Thread file changed while it was being read")

        os.fchmod(snapshot_fd, 0o400)
        os.close(snapshot_fd)
        snapshot_fd = None
        return ThreadFileSnapshot(path=snapshot_path, size=copied)
    except OSError as exc:
        raise ThreadFileAccessError("Unable to create a thread file snapshot") from exc
    finally:
        os.close(source_fd)
        if snapshot_fd is not None:
            os.close(snapshot_fd)
        if snapshot_path is not None and snapshot_fd is not None:
            snapshot_path.unlink(missing_ok=True)


async def snapshot_thread_file_async(
    resolved: ResolvedThreadFile,
    *,
    max_bytes: int,
    suffix: str = "",
) -> ThreadFileSnapshot:
    """Create a snapshot off-loop and reclaim it if the caller is cancelled."""

    task = asyncio.create_task(
        asyncio.to_thread(
            snapshot_thread_file,
            resolved,
            max_bytes=max_bytes,
            suffix=suffix,
        )
    )
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            snapshot = await task
        except Exception:
            pass
        else:
            try:
                snapshot.cleanup()
            except OSError:
                pass
        raise
