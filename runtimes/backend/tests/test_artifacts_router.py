from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.gateway.routers import artifacts


def _create_client() -> TestClient:
    app = FastAPI()
    app.include_router(artifacts.router)
    return TestClient(app)


def test_get_artifact_download_false_does_not_force_attachment(tmp_path):
    file_path = tmp_path / "note.txt"
    file_path.write_text("hello", encoding="utf-8")
    client = _create_client()

    with patch.object(artifacts, "resolve_thread_virtual_path", return_value=file_path):
        response = client.get("/api/threads/thread1/artifacts/mnt/user-data/outputs/note.txt?download=false")

    assert response.status_code == 200
    content_disposition = response.headers.get("content-disposition", "")
    assert "attachment" not in content_disposition.lower()
    assert response.text == "hello"


def test_get_artifact_download_true_forces_attachment(tmp_path):
    file_path = tmp_path / "note.txt"
    file_path.write_text("hello", encoding="utf-8")
    client = _create_client()

    with patch.object(artifacts, "resolve_thread_virtual_path", return_value=file_path):
        response = client.get("/api/threads/thread1/artifacts/mnt/user-data/outputs/note.txt?download=true")

    assert response.status_code == 200
    content_disposition = response.headers.get("content-disposition", "")
    assert "attachment" in content_disposition.lower()
