"""Avatar upload validation helpers."""

from fastapi import HTTPException, status


AVATAR_READ_CHUNK_SIZE = 1024 * 1024
ALLOWED_AVATAR_CONTENT_TYPES = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}
ALLOWED_AVATAR_EXTENSIONS = {
    "jpg": "jpg",
    "jpeg": "jpg",
    "png": "png",
    "webp": "webp",
}


def _invalid_avatar_error(message: str = "仅支持 JPG、PNG、WEBP 格式") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={"error": {"code": "INVALID_FILE_TYPE", "message": message}},
    )


def _avatar_too_large_error(max_bytes: int) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={
            "error": {
                "code": "FILE_TOO_LARGE",
                "message": f"文件大小不能超过{max_bytes // 1024 // 1024}MB",
            }
        },
    )


def _detect_avatar_extension(file_data: bytes) -> str | None:
    if file_data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if file_data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if len(file_data) >= 12 and file_data[:4] == b"RIFF" and file_data[8:12] == b"WEBP":
        return "webp"
    return None


def _filename_extension(filename: str | None) -> str | None:
    if not filename or "." not in filename:
        return None
    return filename.rsplit(".", 1)[-1].strip().lower() or None


def validate_avatar_metadata(
    filename: str | None,
    content_type: str | None,
) -> tuple[str, str]:
    """Validate declared avatar metadata and return normalized extension/content type."""
    normalized_content_type = str(content_type or "").strip().lower()
    content_type_extension = ALLOWED_AVATAR_CONTENT_TYPES.get(normalized_content_type)
    if content_type_extension is None:
        raise _invalid_avatar_error()

    declared_extension = _filename_extension(filename)
    normalized_declared_extension = ALLOWED_AVATAR_EXTENSIONS.get(declared_extension or "")
    if normalized_declared_extension is None:
        raise _invalid_avatar_error()

    if content_type_extension != normalized_declared_extension:
        raise _invalid_avatar_error()

    if content_type_extension == "jpg":
        return "jpg", "image/jpeg"
    return content_type_extension, f"image/{content_type_extension}"


async def read_avatar_upload_file(file, max_bytes: int) -> bytes:
    """Read an avatar upload in chunks and fail as soon as it exceeds max_bytes."""
    validate_avatar_metadata(file.filename, file.content_type)

    declared_size = getattr(file, "size", None)
    if isinstance(declared_size, int) and declared_size > max_bytes:
        raise _avatar_too_large_error(max_bytes)

    chunks: list[bytes] = []
    total_size = 0
    while True:
        chunk = await file.read(AVATAR_READ_CHUNK_SIZE)
        if not chunk:
            break

        total_size += len(chunk)
        if total_size > max_bytes:
            raise _avatar_too_large_error(max_bytes)
        chunks.append(chunk)

    return b"".join(chunks)


def validate_avatar_upload(
    *,
    file_data: bytes,
    filename: str | None,
    content_type: str | None,
    max_bytes: int,
) -> tuple[str, str]:
    """Validate avatar bytes and return normalized extension and content type."""
    expected_extension, normalized_content_type = validate_avatar_metadata(filename, content_type)

    if len(file_data) > max_bytes:
        raise _avatar_too_large_error(max_bytes)

    detected_extension = _detect_avatar_extension(file_data)
    if detected_extension is None:
        raise _invalid_avatar_error()

    if detected_extension != expected_extension:
        raise _invalid_avatar_error()

    return expected_extension, normalized_content_type
