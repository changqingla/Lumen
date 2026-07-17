import zipfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.config.paths import Paths
from src.gateway.routers import artifacts


def _create_client() -> TestClient:
    app = FastAPI()
    app.include_router(artifacts.router)
    return TestClient(app)


def test_get_artifact_download_false_does_not_force_attachment(tmp_path, monkeypatch):
    paths = Paths(tmp_path)
    file_path = paths.sandbox_outputs_dir("thread1") / "note.txt"
    file_path.parent.mkdir(parents=True)
    file_path.write_text("hello", encoding="utf-8")
    client = _create_client()
    monkeypatch.setattr(artifacts, "get_paths", lambda: paths)

    response = client.get("/api/threads/thread1/artifacts/mnt/user-data/outputs/note.txt?download=false")

    assert response.status_code == 200
    content_disposition = response.headers.get("content-disposition", "")
    assert "attachment" not in content_disposition.lower()
    assert response.text == "hello"


def test_get_artifact_download_true_forces_attachment(tmp_path, monkeypatch):
    paths = Paths(tmp_path)
    file_path = paths.sandbox_outputs_dir("thread1") / "note.txt"
    file_path.parent.mkdir(parents=True)
    file_path.write_text("hello", encoding="utf-8")
    client = _create_client()
    monkeypatch.setattr(artifacts, "get_paths", lambda: paths)

    response = client.get("/api/threads/thread1/artifacts/mnt/user-data/outputs/note.txt?download=true")

    assert response.status_code == 200
    content_disposition = response.headers.get("content-disposition", "")
    assert "attachment" in content_disposition.lower()


def test_get_artifact_rejects_symlink_swap_after_resolution(tmp_path, monkeypatch):
    paths = Paths(tmp_path)
    file_path = paths.sandbox_outputs_dir("thread1") / "note.txt"
    file_path.parent.mkdir(parents=True)
    file_path.write_text("allowed", encoding="utf-8")
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("must not be returned", encoding="utf-8")
    original_resolve = artifacts.resolve_thread_file

    def resolve_then_swap(*args, **kwargs):
        resolved = original_resolve(*args, **kwargs)
        file_path.unlink()
        file_path.symlink_to(outside)
        return resolved

    monkeypatch.setattr(artifacts, "get_paths", lambda: paths)
    monkeypatch.setattr(artifacts, "resolve_thread_file", resolve_then_swap)

    response = _create_client().get(
        "/api/threads/thread1/artifacts/mnt/user-data/outputs/note.txt"
    )

    assert response.status_code == 403
    assert "must not be returned" not in response.text


def test_skill_archive_preview_is_bounded(tmp_path):
    archive_path = tmp_path / "example.skill"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("example/asset.bin", b"12345")

    with pytest.raises(artifacts._SkillArchiveMemberTooLarge):
        artifacts._extract_file_from_skill_archive(
            archive_path,
            "asset.bin",
            max_bytes=4,
        )
