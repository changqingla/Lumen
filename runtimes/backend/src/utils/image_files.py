"""Bounded access to images stored in a Runtime thread directory."""

from __future__ import annotations

import mimetypes
import os
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from src.config.paths import VIRTUAL_PATH_PREFIX, Paths

MAX_VIEW_IMAGE_BYTES = 10 * 1024 * 1024
MAX_VIEW_IMAGES_PER_REQUEST = 4
MAX_VIEW_IMAGES_TOTAL_BYTES = 20 * 1024 * 1024
SUPPORTED_IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp"})
VIEW_IMAGE_SUCCESS_MESSAGE = "Successfully read image"


class ImageFileError(ValueError):
    """Base class for safe, user-facing image access failures."""


class ImageTooLargeError(ImageFileError):
    """Raised when an image exceeds the per-image byte limit."""


@dataclass(frozen=True)
class ImageFile:
    path: Path
    mime_type: str
    size: int


@dataclass(frozen=True)
class LoadedImage:
    mime_type: str
    data: bytes


@dataclass(frozen=True)
class ResolvedImagePath:
    """A lexical path anchored to one trusted thread user-data directory."""

    root: Path
    parts: tuple[str, ...]

    @property
    def path(self) -> Path:
        return self.root.joinpath(*self.parts)


def resolve_image_path(
    paths: Paths,
    thread_id: str,
    image_path: str,
) -> ResolvedImagePath:
    """Resolve a virtual image path without following user-controlled links."""

    virtual_path = str(image_path or "")
    if "\\" in virtual_path:
        raise ImageFileError(f"Path must be inside {VIRTUAL_PATH_PREFIX}")
    try:
        path = PurePosixPath(virtual_path)
        relative = path.relative_to(PurePosixPath(VIRTUAL_PATH_PREFIX))
        parts = relative.parts
        if not path.is_absolute() or not parts or any(
            part in {"", ".", ".."} for part in parts
        ):
            raise ValueError
        root = paths.sandbox_user_data_dir(thread_id).resolve()
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ImageFileError(f"Path must be inside {VIRTUAL_PATH_PREFIX}") from exc
    return ResolvedImagePath(root=root, parts=tuple(parts))


def _open_image_descriptor(image_path: ResolvedImagePath) -> tuple[int, os.stat_result]:
    """Open a regular file beneath ``root`` without following any symlink."""

    if not hasattr(os, "O_NOFOLLOW"):
        raise ImageFileError("Secure image access is unavailable")

    common_flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
    directory_flags = common_flags | os.O_DIRECTORY
    directory_fd: int | None = None
    file_fd: int | None = None
    return_file_fd = False
    try:
        directory_fd = os.open(image_path.root, directory_flags)
        for component in image_path.parts[:-1]:
            next_fd = os.open(component, directory_flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
        file_fd = os.open(
            image_path.parts[-1],
            common_flags,
            dir_fd=directory_fd,
        )
        file_stat = os.fstat(file_fd)
        if not stat.S_ISREG(file_stat.st_mode):
            raise ImageFileError("Image path is not a regular file")
        return_file_fd = True
        return file_fd, file_stat
    except FileNotFoundError as exc:
        raise ImageFileError("Image file not found") from exc
    except ImageFileError:
        raise
    except OSError as exc:
        raise ImageFileError("Unable to securely read image file") from exc
    finally:
        if directory_fd is not None:
            os.close(directory_fd)
        if file_fd is not None and not return_file_fd:
            os.close(file_fd)


def _inspect_open_image(
    image_path: ResolvedImagePath,
    file_stat: os.stat_result,
    *,
    max_bytes: int,
) -> ImageFile:
    extension = image_path.path.suffix.lower()
    if extension not in SUPPORTED_IMAGE_EXTENSIONS:
        raise ImageFileError("Unsupported image format")

    size = file_stat.st_size
    if size <= 0:
        raise ImageFileError("Image file is empty")
    if size > max_bytes:
        raise ImageTooLargeError("Image file exceeds the size limit")

    mime_type, _ = mimetypes.guess_type(image_path.parts[-1])
    if mime_type is None:
        mime_type = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
        }[extension]
    return ImageFile(path=image_path.path, mime_type=mime_type, size=size)


def inspect_image_file(
    path: ResolvedImagePath,
    *,
    max_bytes: int = MAX_VIEW_IMAGE_BYTES,
) -> ImageFile:
    """Validate type, readability, and size without retaining image bytes."""

    file_fd, file_stat = _open_image_descriptor(path)
    try:
        return _inspect_open_image(path, file_stat, max_bytes=max_bytes)
    finally:
        os.close(file_fd)


def load_image_file(
    path: ResolvedImagePath,
    *,
    max_bytes: int = MAX_VIEW_IMAGE_BYTES,
) -> LoadedImage:
    """Securely open and bounded-read an image from one descriptor snapshot."""

    file_fd, file_stat = _open_image_descriptor(path)
    try:
        image = _inspect_open_image(path, file_stat, max_bytes=max_bytes)
        with os.fdopen(file_fd, "rb") as stream:
            file_fd = -1
            data = stream.read(max_bytes + 1)
    except OSError as exc:
        raise ImageFileError("Unable to read image file") from exc
    finally:
        if file_fd >= 0:
            os.close(file_fd)
    if len(data) > max_bytes:
        raise ImageTooLargeError("Image file exceeds the size limit")
    if not data:
        raise ImageFileError("Image file is empty")
    return LoadedImage(mime_type=image.mime_type, data=data)
