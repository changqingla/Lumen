import asyncio
import hashlib
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, UploadFile

from src.config.uploads_config import UploadsConfig
from src.gateway.routers import uploads

_MANAGED_FILENAME = "kb__11111111-1111-1111-1111-111111111111__22222222-2222-2222-2222-222222222222__0123456789abcdef__notes.md"


@pytest.fixture(autouse=True)
def managed_uploads_dir(tmp_path, monkeypatch):
    directory = tmp_path / "knowledge"
    directory.mkdir(parents=True)
    monkeypatch.setattr(
        uploads,
        "get_managed_uploads_dir",
        lambda _thread_id: directory,
    )
    return directory


def test_upload_files_writes_thread_storage_without_sandbox_sync(tmp_path):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)

    with (
        patch.object(uploads, "get_uploads_dir", return_value=thread_uploads_dir),
    ):
        file = UploadFile(filename="notes.txt", file=BytesIO(b"hello uploads"))
        result = asyncio.run(uploads.upload_files("thread-local", files=[file]))

    assert result.success is True
    assert len(result.files) == 1
    assert result.files[0].filename == "notes.txt"
    assert result.files[0].size == len(b"hello uploads")
    assert (thread_uploads_dir / "notes.txt").read_bytes() == b"hello uploads"


def test_upload_files_marks_markdown_file_without_sandbox_sync(tmp_path):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)

    async def fake_convert(file_path: Path) -> Path:
        md_path = file_path.with_suffix(".md")
        md_path.write_text("converted", encoding="utf-8")
        return md_path

    with (
        patch.object(uploads, "get_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "convert_file_to_markdown", AsyncMock(side_effect=fake_convert)),
    ):
        file = UploadFile(filename="report.pdf", file=BytesIO(b"pdf-bytes"))
        result = asyncio.run(uploads.upload_files("thread-aio", files=[file]))

    assert result.success is True
    assert len(result.files) == 1
    file_info = result.files[0]
    assert file_info.filename == "report.pdf"
    assert file_info.size == len(b"pdf-bytes")
    assert file_info.markdown_file == "report.md"

    assert (thread_uploads_dir / "report.pdf").read_bytes() == b"pdf-bytes"
    assert (thread_uploads_dir / "report.md").read_text(encoding="utf-8") == "converted"
    assert (thread_uploads_dir / "report.pdf").stat().st_mode & 0o777 == 0o666
    assert (thread_uploads_dir / "report.md").stat().st_mode & 0o777 == 0o666


def test_upload_conversion_rejects_symlink_swap_before_snapshot(tmp_path):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)
    outside = tmp_path / "outside-secret.pdf"
    outside.write_bytes(b"must not be converted")
    convert = AsyncMock()
    original_snapshot = uploads.snapshot_thread_file_async

    async def swap_then_snapshot(resolved, **kwargs):
        upload_path = thread_uploads_dir / "report.pdf"
        upload_path.unlink()
        upload_path.symlink_to(outside)
        return await original_snapshot(resolved, **kwargs)

    with (
        patch.object(uploads, "get_uploads_dir", return_value=thread_uploads_dir),
        patch.object(
            uploads,
            "snapshot_thread_file_async",
            side_effect=swap_then_snapshot,
        ),
        patch.object(uploads, "convert_file_to_markdown", convert),
        pytest.raises(HTTPException) as exc_info,
    ):
        asyncio.run(
            uploads.upload_files(
                "thread-local",
                files=[
                    UploadFile(
                        filename="report.pdf",
                        file=BytesIO(b"uploaded"),
                    )
                ],
            )
        )

    assert exc_info.value.status_code == 409
    assert outside.read_bytes() == b"must not be converted"
    assert not (thread_uploads_dir / "report.pdf").exists()
    convert.assert_not_called()


def test_upload_files_stores_managed_knowledge_outside_uploads(
    tmp_path,
    managed_uploads_dir,
):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)

    with patch.object(uploads, "get_uploads_dir", return_value=thread_uploads_dir):
        result = asyncio.run(
            uploads.upload_files(
                "thread-local",
                files=[UploadFile(filename=_MANAGED_FILENAME, file=BytesIO(b"managed"))],
            )
        )

    assert result.files[0].filename == _MANAGED_FILENAME
    assert result.files[0].virtual_path == (f"/mnt/user-data/knowledge/{_MANAGED_FILENAME}")
    assert not (thread_uploads_dir / _MANAGED_FILENAME).exists()
    managed_file = managed_uploads_dir / _MANAGED_FILENAME
    assert managed_file.read_bytes() == b"managed"
    assert managed_file.stat().st_mode & 0o777 == 0o444


def test_upload_files_rolls_back_managed_file_when_read_only_chmod_fails(
    tmp_path,
    managed_uploads_dir,
):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)

    with (
        patch.object(uploads, "get_uploads_dir", return_value=thread_uploads_dir),
        patch.object(
            uploads.os,
            "fchmod",
            side_effect=PermissionError("chmod denied"),
        ),
        pytest.raises(HTTPException) as exc_info,
    ):
        asyncio.run(
            uploads.upload_files(
                "thread-local",
                files=[UploadFile(filename=_MANAGED_FILENAME, file=BytesIO(b"managed"))],
            )
        )

    assert exc_info.value.status_code == 500
    assert list(thread_uploads_dir.iterdir()) == []
    assert list(managed_uploads_dir.iterdir()) == []


@pytest.mark.parametrize(
    "filename",
    ["kb__not-canonical.md", "../" + _MANAGED_FILENAME],
)
def test_upload_files_rejects_noncanonical_managed_names(tmp_path, filename):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)

    with (
        patch.object(uploads, "get_uploads_dir", return_value=thread_uploads_dir),
        pytest.raises(HTTPException) as exc_info,
    ):
        asyncio.run(
            uploads.upload_files(
                "thread-local",
                files=[UploadFile(filename=filename, file=BytesIO(b"managed"))],
            )
        )

    assert exc_info.value.status_code == 400


def test_upload_files_atomically_replaces_corrupt_managed_copy(
    tmp_path,
    managed_uploads_dir,
):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)
    target = managed_uploads_dir / _MANAGED_FILENAME
    target.write_bytes(b"corrupt")

    with patch.object(uploads, "get_uploads_dir", return_value=thread_uploads_dir):
        result = asyncio.run(
            uploads.upload_files(
                "thread-local",
                files=[UploadFile(filename=_MANAGED_FILENAME, file=BytesIO(b"expected"))],
            )
        )

    assert result.files[0].filename == _MANAGED_FILENAME
    assert target.read_bytes() == b"expected"
    assert not list(managed_uploads_dir.glob(".managed-upload-*.tmp"))


def test_upload_files_respects_configured_markdown_extensions(tmp_path):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)

    fake_config = MagicMock()
    fake_config.uploads = UploadsConfig(markdown_extensions={".docx"})

    with (
        patch.object(uploads, "get_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "get_app_config", return_value=fake_config),
        patch.object(uploads, "convert_file_to_markdown", AsyncMock()),
    ):
        file = UploadFile(filename="report.pdf", file=BytesIO(b"pdf-bytes"))
        result = asyncio.run(uploads.upload_files("thread-local", files=[file]))

    assert result.success is True
    assert len(result.files) == 1
    assert result.files[0].markdown_file is None
    assert not (thread_uploads_dir / "report.md").exists()


class _ChunkCheckedStream(BytesIO):
    def __init__(self, content: bytes, max_read_size: int):
        super().__init__(content)
        self.max_read_size = max_read_size
        self.read_sizes: list[int] = []

    def read(self, size: int = -1) -> bytes:
        assert 0 < size <= self.max_read_size
        self.read_sizes.append(size)
        return super().read(size)


def test_upload_files_streams_in_configured_chunks(tmp_path):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)
    stream = _ChunkCheckedStream(b"abcdefghij", max_read_size=4)
    config = UploadsConfig(
        max_file_size_bytes=20,
        max_request_size_bytes=20,
        stream_chunk_size_bytes=4,
        markdown_extensions=set(),
    )

    with (
        patch.object(uploads, "get_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "get_uploads_config", return_value=config),
        patch.object(uploads, "get_markdown_extensions", return_value=set()),
    ):
        result = asyncio.run(uploads.upload_files("thread-local", files=[UploadFile(filename="data.bin", file=stream)]))

    assert result.files[0].size == 10
    assert len(stream.read_sizes) == 4
    assert (thread_uploads_dir / "data.bin").read_bytes() == b"abcdefghij"


def test_upload_files_rejects_oversized_file_and_removes_partial_write(tmp_path):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)
    config = UploadsConfig(
        max_file_size_bytes=5,
        max_request_size_bytes=20,
        stream_chunk_size_bytes=4,
        markdown_extensions=set(),
    )

    with (
        patch.object(uploads, "get_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "get_uploads_config", return_value=config),
        patch.object(uploads, "get_markdown_extensions", return_value=set()),
        pytest.raises(HTTPException) as exc_info,
    ):
        asyncio.run(
            uploads.upload_files(
                "thread-local",
                files=[UploadFile(filename="large.bin", file=BytesIO(b"123456"))],
            )
        )

    assert exc_info.value.status_code == 413
    assert not (thread_uploads_dir / "large.bin").exists()


def test_upload_files_enforces_total_limit_and_rolls_back_request(tmp_path):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)
    config = UploadsConfig(
        max_file_size_bytes=10,
        max_request_size_bytes=7,
        stream_chunk_size_bytes=3,
        markdown_extensions=set(),
    )

    with (
        patch.object(uploads, "get_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "get_uploads_config", return_value=config),
        patch.object(uploads, "get_markdown_extensions", return_value=set()),
        pytest.raises(HTTPException) as exc_info,
    ):
        asyncio.run(
            uploads.upload_files(
                "thread-local",
                files=[
                    UploadFile(filename="first.bin", file=BytesIO(b"1234")),
                    UploadFile(filename="second.bin", file=BytesIO(b"5678")),
                ],
            )
        )

    assert exc_info.value.status_code == 413
    assert list(thread_uploads_dir.iterdir()) == []


def test_upload_conversion_does_not_overwrite_existing_markdown(tmp_path):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)
    existing_markdown = thread_uploads_dir / "report.md"
    existing_markdown.write_text("keep me", encoding="utf-8")

    async def fake_convert(file_path: Path) -> Path:
        md_path = file_path.with_suffix(".md")
        md_path.write_text("converted", encoding="utf-8")
        return md_path

    with (
        patch.object(uploads, "get_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "convert_file_to_markdown", AsyncMock(side_effect=fake_convert)),
    ):
        result = asyncio.run(
            uploads.upload_files(
                "thread-local",
                files=[UploadFile(filename="report.pdf", file=BytesIO(b"pdf"))],
            )
        )

    assert result.files[0].filename == "report-2.pdf"
    assert result.files[0].markdown_file == "report-2.md"
    assert existing_markdown.read_text(encoding="utf-8") == "keep me"


def test_get_markdown_extensions_normalizes_configured_values():
    fake_config = MagicMock()
    fake_config.uploads = UploadsConfig(markdown_extensions={"PDF", ".Docx", " pptx "})

    with patch.object(uploads, "get_app_config", return_value=fake_config):
        assert uploads.get_markdown_extensions() == {".pdf", ".docx", ".pptx"}


def test_upload_files_rejects_dotdot_and_dot_filenames(tmp_path):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)

    with (
        patch.object(uploads, "get_uploads_dir", return_value=thread_uploads_dir),
    ):
        # These filenames must be rejected outright
        for bad_name in ["..", "."]:
            file = UploadFile(filename=bad_name, file=BytesIO(b"data"))
            result = asyncio.run(uploads.upload_files("thread-local", files=[file]))
            assert result.success is True
            assert result.files == [], f"Expected no files for unsafe filename {bad_name!r}"

        # Path-traversal prefixes are stripped to the basename and accepted safely
        file = UploadFile(filename="../etc/passwd", file=BytesIO(b"data"))
        result = asyncio.run(uploads.upload_files("thread-local", files=[file]))
        assert result.success is True
        assert len(result.files) == 1
        assert result.files[0].filename == "passwd"

    # Only the safely normalised file should exist
    assert [f.name for f in thread_uploads_dir.iterdir()] == ["passwd"]


@pytest.mark.parametrize(
    "filename",
    ["bad\x00name.txt", "bad\nname.txt", "x" * 241],
)
def test_upload_files_rejects_control_and_oversized_filenames(filename):
    assert uploads._normalize_filename(filename) is None


def test_upload_files_rejects_invalid_thread_id():
    file = UploadFile(filename="notes.txt", file=BytesIO(b"hello"))
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(uploads.upload_files("../bad-thread", files=[file]))
    assert exc_info.value.status_code == 400
    assert "Invalid thread_id" in str(exc_info.value.detail)


def test_delete_uploaded_file_removes_thread_storage(tmp_path):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)
    target = thread_uploads_dir / "report.pdf"
    target.write_bytes(b"hello")

    with (
        patch.object(uploads, "get_uploads_dir", return_value=thread_uploads_dir),
    ):
        result = asyncio.run(uploads.delete_uploaded_file("thread-aio", "report.pdf"))

    assert result.success is True
    assert result.message == "Deleted report.pdf"
    assert not target.exists()


def test_delete_uploaded_file_masks_filesystem_exception(
    tmp_path,
    caplog,
):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)
    target = thread_uploads_dir / "report.pdf"
    target.write_bytes(b"hello")
    secret = "filesystem-secret-marker"

    with (
        patch.object(uploads, "get_uploads_dir", return_value=thread_uploads_dir),
        patch.object(Path, "unlink", side_effect=OSError(secret)),
        pytest.raises(HTTPException) as exc_info,
    ):
        asyncio.run(uploads.delete_uploaded_file("thread-aio", "report.pdf"))

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Failed to delete uploaded file"
    assert secret not in str(exc_info.value.detail)
    assert secret not in caplog.text


def test_delete_uploaded_file_local_thread_storage(tmp_path):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)
    target = thread_uploads_dir / "notes.txt"
    target.write_bytes(b"hello")

    with (
        patch.object(uploads, "get_uploads_dir", return_value=thread_uploads_dir),
    ):
        result = asyncio.run(uploads.delete_uploaded_file("thread-local", "notes.txt"))

    assert result.success is True
    assert result.message == "Deleted notes.txt"
    assert not target.exists()


def test_get_managed_upload_metadata_streams_sha256_and_size(
    monkeypatch,
    tmp_path,
    managed_uploads_dir,
):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)
    content = b"abcdefghij"
    (managed_uploads_dir / _MANAGED_FILENAME).write_bytes(content)
    config = UploadsConfig(stream_chunk_size_bytes=4)
    original_read = uploads.os.read
    read_sizes: list[int] = []

    def tracked_read(file_descriptor: int, size: int) -> bytes:
        read_sizes.append(size)
        return original_read(file_descriptor, size)

    monkeypatch.setattr(uploads.os, "read", tracked_read)
    with (
        patch.object(uploads, "get_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "get_uploads_config", return_value=config),
    ):
        result = asyncio.run(uploads.get_managed_upload_metadata("thread-local", _MANAGED_FILENAME))

    assert result.filename == _MANAGED_FILENAME
    assert result.size == len(content)
    assert result.sha256 == hashlib.sha256(content).hexdigest()
    assert read_sizes == [4, 4, 4, 4]


def test_managed_upload_metadata_route_is_hidden_from_public_schema():
    route = next(route for route in uploads.router.routes if route.path == "/api/threads/{thread_id}/uploads/metadata")

    assert route.methods == {"GET"}
    assert route.include_in_schema is False


@pytest.mark.parametrize(
    "filename",
    [
        "../" + _MANAGED_FILENAME,
        "kb__not-a-managed-file.md",
        "notes.md",
    ],
)
def test_get_managed_upload_metadata_rejects_noncanonical_names(tmp_path, filename):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)

    with (
        patch.object(uploads, "get_uploads_dir", return_value=thread_uploads_dir),
        pytest.raises(HTTPException) as exc_info,
    ):
        asyncio.run(uploads.get_managed_upload_metadata("thread-local", filename))

    assert exc_info.value.status_code == 400


def test_get_managed_upload_metadata_rejects_symlink(
    tmp_path,
    managed_uploads_dir,
):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)
    outside = tmp_path / "outside.md"
    outside.write_text("secret", encoding="utf-8")
    (managed_uploads_dir / _MANAGED_FILENAME).symlink_to(outside)

    with (
        patch.object(uploads, "get_uploads_dir", return_value=thread_uploads_dir),
        pytest.raises(HTTPException) as exc_info,
    ):
        asyncio.run(uploads.get_managed_upload_metadata("thread-local", _MANAGED_FILENAME))

    assert exc_info.value.status_code == 403


def test_get_managed_upload_metadata_returns_not_found(tmp_path):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)

    with (
        patch.object(uploads, "get_uploads_dir", return_value=thread_uploads_dir),
        pytest.raises(HTTPException) as exc_info,
    ):
        asyncio.run(uploads.get_managed_upload_metadata("thread-local", _MANAGED_FILENAME))

    assert exc_info.value.status_code == 404


def test_list_uploaded_files_merges_uploads_and_managed_knowledge(
    tmp_path,
    managed_uploads_dir,
):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)
    (thread_uploads_dir / "notes.txt").write_text("notes", encoding="utf-8")
    (managed_uploads_dir / _MANAGED_FILENAME).write_text("knowledge", encoding="utf-8")

    with patch.object(uploads, "get_uploads_dir", return_value=thread_uploads_dir):
        result = asyncio.run(uploads.list_uploaded_files("thread-local"))

    by_name = {item.filename: item for item in result.files}
    assert result.count == 2
    assert by_name["notes.txt"].virtual_path == "/mnt/user-data/uploads/notes.txt"
    assert by_name[_MANAGED_FILENAME].virtual_path == (f"/mnt/user-data/knowledge/{_MANAGED_FILENAME}")


def test_list_uploaded_files_migrates_legacy_managed_copy(
    tmp_path,
    managed_uploads_dir,
):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)
    legacy = thread_uploads_dir / _MANAGED_FILENAME
    legacy.write_bytes(b"legacy")

    with patch.object(uploads, "get_uploads_dir", return_value=thread_uploads_dir):
        result = asyncio.run(uploads.list_uploaded_files("thread-local"))

    assert result.count == 1
    assert not legacy.exists()
    assert (managed_uploads_dir / _MANAGED_FILENAME).read_bytes() == b"legacy"


def test_list_uploaded_files_collapses_identical_legacy_and_managed_copies(
    tmp_path,
    managed_uploads_dir,
):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)
    legacy = thread_uploads_dir / _MANAGED_FILENAME
    managed = managed_uploads_dir / _MANAGED_FILENAME
    legacy.write_bytes(b"same")
    managed.write_bytes(b"same")

    with patch.object(uploads, "get_uploads_dir", return_value=thread_uploads_dir):
        result = asyncio.run(uploads.list_uploaded_files("thread-local"))

    assert result.count == 1
    assert not legacy.exists()
    assert managed.read_bytes() == b"same"


def test_list_uploaded_files_fails_closed_on_conflicting_managed_copies(
    tmp_path,
    managed_uploads_dir,
):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)
    (thread_uploads_dir / _MANAGED_FILENAME).write_bytes(b"legacy")
    (managed_uploads_dir / _MANAGED_FILENAME).write_bytes(b"managed")

    with (
        patch.object(uploads, "get_uploads_dir", return_value=thread_uploads_dir),
        pytest.raises(HTTPException) as exc_info,
    ):
        asyncio.run(uploads.list_uploaded_files("thread-local"))

    assert exc_info.value.status_code == 409


def test_delete_uploaded_file_removes_managed_knowledge(
    tmp_path,
    managed_uploads_dir,
):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)
    target = managed_uploads_dir / _MANAGED_FILENAME
    target.write_bytes(b"managed")

    with patch.object(uploads, "get_uploads_dir", return_value=thread_uploads_dir):
        result = asyncio.run(uploads.delete_uploaded_file("thread-local", _MANAGED_FILENAME))

    assert result.success is True
    assert not target.exists()


def test_delete_uploaded_file_rejects_managed_companion(
    tmp_path,
    managed_uploads_dir,
):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)
    target = managed_uploads_dir / _MANAGED_FILENAME
    target.write_bytes(b"managed")

    with (
        patch.object(uploads, "get_uploads_dir", return_value=thread_uploads_dir),
        pytest.raises(HTTPException) as exc_info,
    ):
        asyncio.run(
            uploads.delete_uploaded_file(
                "thread-local",
                _MANAGED_FILENAME,
                companion_filename="notes.md",
            )
        )

    assert exc_info.value.status_code == 400
    assert target.exists()


def test_delete_uploaded_file_removes_registered_markdown_companion(tmp_path):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)
    source = thread_uploads_dir / "report.pdf"
    companion = thread_uploads_dir / "report.md"
    unrelated = thread_uploads_dir / "notes.md"
    source.write_bytes(b"pdf")
    companion.write_text("converted", encoding="utf-8")
    unrelated.write_text("keep", encoding="utf-8")

    with patch.object(uploads, "get_uploads_dir", return_value=thread_uploads_dir):
        result = asyncio.run(
            uploads.delete_uploaded_file(
                "thread-local",
                "report.pdf",
                companion_filename="report.md",
            )
        )

    assert result.success is True
    assert not source.exists()
    assert not companion.exists()
    assert unrelated.read_text(encoding="utf-8") == "keep"


def test_delete_uploaded_file_rejects_unrelated_companion(tmp_path):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)
    (thread_uploads_dir / "report.pdf").write_bytes(b"pdf")
    unrelated = thread_uploads_dir / "notes.md"
    unrelated.write_text("keep", encoding="utf-8")

    with (
        patch.object(uploads, "get_uploads_dir", return_value=thread_uploads_dir),
        pytest.raises(HTTPException) as exc_info,
    ):
        asyncio.run(
            uploads.delete_uploaded_file(
                "thread-local",
                "report.pdf",
                companion_filename="notes.md",
            )
        )

    assert exc_info.value.status_code == 400
    assert unrelated.read_text(encoding="utf-8") == "keep"
