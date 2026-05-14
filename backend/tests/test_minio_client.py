import os

os.environ["DEBUG"] = "false"

from types import SimpleNamespace

from utils import minio_client


def test_get_file_url_preserves_signature_when_using_nginx_proxy(monkeypatch):
    monkeypatch.setattr(
        minio_client,
        "settings",
        SimpleNamespace(
            MINIO_BUCKET="reader-uploads",
            MINIO_PUBLIC_ENDPOINT="nginx",
        ),
    )
    monkeypatch.setattr(
        minio_client.minio_client,
        "presigned_get_object",
        lambda bucket, object_name, expires: (
            f"http://minio:9000/{bucket}/{object_name}"
            "?X-Amz-Algorithm=AWS4-HMAC-SHA256"
            "&X-Amz-Expires=3600"
            "&X-Amz-Signature=abc123"
        ),
    )

    url = minio_client.get_file_url("kb/demo/report.pdf", expires_seconds=3600)

    assert url.startswith("/minio/reader-uploads/kb/demo/report.pdf?")
    assert "X-Amz-Expires=3600" in url
    assert "X-Amz-Signature=abc123" in url
