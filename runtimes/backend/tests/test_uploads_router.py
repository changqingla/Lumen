import asyncio
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, UploadFile

from src.config.uploads_config import UploadsConfig
from src.gateway.routers import uploads


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


def test_upload_files_respects_configured_markdown_extensions(tmp_path):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)

    fake_config = MagicMock()
    fake_config.uploads.markdown_extensions = {".docx"}

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
