import base64
import json

import pytest

from schemas.workspace import WorkspaceAttachmentInput, WorkspaceAttachmentRecord
from modules.chat.services import workspace_service as workspace_module
from modules.chat.services.workspace_service import WorkspaceService


def _build_data_url(payload: bytes, mime_type: str = "image/png") -> str:
    return f"data:{mime_type};base64,{base64.b64encode(payload).decode('ascii')}"


@pytest.mark.asyncio
async def test_workspace_service_registers_inline_image_and_manifest(monkeypatch):
    uploads = []

    async def fake_object_exists(object_name: str) -> bool:
        return False

    async def fake_upload_file(object_name: str, file_data: bytes, content_type: str = "application/octet-stream") -> str:
        uploads.append((object_name, file_data, content_type))
        return f"reader-uploads/{object_name}"

    monkeypatch.setattr(workspace_module, "object_exists", fake_object_exists)
    monkeypatch.setattr(workspace_module, "upload_file", fake_upload_file)

    service = WorkspaceService(
        session_id="11111111-1111-1111-1111-111111111111",
        user_id="22222222-2222-2222-2222-222222222222",
    )
    assets = await service.resolve_request_attachments(
        attachments=[],
        image_data_urls=[_build_data_url(b"fake-image-bytes")],
    )

    assert len(assets) == 1
    asset = assets[0]
    assert asset.workspace_path.startswith("input/image-")
    assert asset.object_path.endswith(asset.workspace_path)
    assert asset.input_mode == "both"
    assert asset.view_type == "image"
    assert asset.available_views == ["vision"]
    assert "vision_read" in asset.capabilities
    assert asset.parse_status == "ready"
    assert len(uploads) == 2
    assert uploads[0][0] == asset.object_path
    assert uploads[0][2] == "image/png"
    assert uploads[1][0] == service.manifest_object_path
    brief = service.build_agent_workspace_brief(assets)
    assert asset.workspace_path in brief
    assert "格式转换" in brief


@pytest.mark.asyncio
async def test_workspace_service_restores_existing_manifest(monkeypatch):
    service = WorkspaceService(
        session_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        user_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
    )
    manifest_payload = {
        "session_id": service.session_id,
        "user_id": service.user_id,
        "version": 1,
        "updated_at": "2026-03-15T00:00:00+00:00",
        "assets": [
            {
                "attachment_id": "att_existing",
                "session_id": service.session_id,
                "user_id": service.user_id,
                "name": "report.docx",
                "object_path": f"{service.files_prefix}/input/report.docx",
                "workspace_path": "input/report.docx",
                "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "source_kind": "user_upload",
                "role": "source",
                "input_mode": "workspace_file",
                "size_bytes": 1024,
                "created_at": "2026-03-15T00:00:00+00:00",
                "metadata": {},
            }
        ],
    }

    async def fake_object_exists(object_name: str) -> bool:
        return object_name == service.manifest_object_path

    async def fake_download_file(object_name: str) -> bytes:
        assert object_name == service.manifest_object_path
        return json.dumps(manifest_payload).encode("utf-8")

    monkeypatch.setattr(workspace_module, "object_exists", fake_object_exists)
    monkeypatch.setattr(workspace_module, "download_file", fake_download_file)

    assets = await service.resolve_request_attachments(attachments=[], image_data_urls=[])

    assert len(assets) == 1
    assert assets[0].workspace_path == "input/report.docx"


@pytest.mark.asyncio
async def test_workspace_service_avoids_overwriting_same_named_attachment(monkeypatch):
    service = WorkspaceService(
        session_id="cccccccc-cccc-cccc-cccc-cccccccccccc",
        user_id="dddddddd-dddd-dddd-dddd-dddddddddddd",
    )
    manifest_payload = {
        "session_id": service.session_id,
        "user_id": service.user_id,
        "version": 1,
        "updated_at": "2026-03-15T00:00:00+00:00",
        "assets": [
            {
                "attachment_id": "att_existing",
                "session_id": service.session_id,
                "user_id": service.user_id,
                "name": "report.docx",
                "object_path": f"{service.files_prefix}/input/existing-report.docx",
                "workspace_path": "input/report.docx",
                "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "source_kind": "user_upload",
                "role": "source",
                "input_mode": "workspace_file",
                "size_bytes": 1024,
                "created_at": "2026-03-15T00:00:00+00:00",
                "metadata": {},
            }
        ],
    }
    saved_manifests = []

    async def fake_object_exists(object_name: str) -> bool:
        return object_name in {
            service.manifest_object_path,
            f"{service.files_prefix}/input/report.docx",
        }

    async def fake_download_file(object_name: str) -> bytes:
        assert object_name == service.manifest_object_path
        return json.dumps(manifest_payload).encode("utf-8")

    async def fake_upload_file(object_name: str, file_data: bytes, content_type: str = "application/octet-stream") -> str:
        saved_manifests.append((object_name, file_data, content_type))
        return f"reader-uploads/{object_name}"

    monkeypatch.setattr(workspace_module, "object_exists", fake_object_exists)
    monkeypatch.setattr(workspace_module, "download_file", fake_download_file)
    monkeypatch.setattr(workspace_module, "upload_file", fake_upload_file)

    assets = await service.resolve_request_attachments(
        attachments=[
            WorkspaceAttachmentInput(
                name="report.docx",
                object_path=f"{service.files_prefix}/input/report.docx",
            )
        ],
        image_data_urls=[],
    )

    workspace_paths = sorted(asset.workspace_path for asset in assets)
    assert workspace_paths == ["input/report-2.docx", "input/report.docx"]
    assert saved_manifests


@pytest.mark.asyncio
async def test_workspace_service_preserves_asset_identity_for_same_object(monkeypatch):
    service = WorkspaceService(
        session_id="eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee",
        user_id="ffffffff-ffff-ffff-ffff-ffffffffffff",
    )
    manifest_payload = {
        "session_id": service.session_id,
        "user_id": service.user_id,
        "version": 1,
        "updated_at": "2026-03-15T00:00:00+00:00",
        "assets": [
            {
                "attachment_id": "att_stable",
                "session_id": service.session_id,
                "user_id": service.user_id,
                "name": "report.docx",
                "object_path": f"{service.files_prefix}/input/report.docx",
                "workspace_path": "input/report.docx",
                "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "source_kind": "user_upload",
                "role": "source",
                "input_mode": "workspace_file",
                "size_bytes": 1024,
                "sha256": "stable-sha",
                "created_at": "2026-03-15T00:00:00+00:00",
                "metadata": {"origin": "upload"},
            }
        ],
    }
    uploads = []

    async def fake_object_exists(object_name: str) -> bool:
        return object_name in {
            service.manifest_object_path,
            f"{service.files_prefix}/input/report.docx",
        }

    async def fake_download_file(object_name: str) -> bytes:
        return json.dumps(manifest_payload).encode("utf-8")

    async def fake_upload_file(object_name: str, file_data: bytes, content_type: str = "application/octet-stream") -> str:
        uploads.append((object_name, file_data, content_type))
        return f"reader-uploads/{object_name}"

    monkeypatch.setattr(workspace_module, "object_exists", fake_object_exists)
    monkeypatch.setattr(workspace_module, "download_file", fake_download_file)
    monkeypatch.setattr(workspace_module, "upload_file", fake_upload_file)

    assets = await service.resolve_request_attachments(
        attachments=[
            WorkspaceAttachmentInput(
                name="report.docx",
                object_path=f"{service.files_prefix}/input/report.docx",
                metadata={"source": "chat"},
            )
        ],
        image_data_urls=[],
    )

    assert len(assets) == 1
    asset = assets[0]
    assert asset.attachment_id == "att_stable"
    assert asset.created_at == "2026-03-15T00:00:00+00:00"
    assert asset.workspace_path == "input/report.docx"
    assert asset.view_type == "office_document"
    assert "style_inspect" in asset.capabilities
    assert asset.parse_status == "none"
    assert asset.metadata == {"origin": "upload", "source": "chat"}


@pytest.mark.asyncio
async def test_workspace_service_refreshes_inferred_capabilities_when_mime_becomes_known(monkeypatch):
    service = WorkspaceService(
        session_id="01010101-0101-0101-0101-010101010101",
        user_id="02020202-0202-0202-0202-020202020202",
    )
    manifest_payload = {
        "session_id": service.session_id,
        "user_id": service.user_id,
        "version": 1,
        "updated_at": "2026-03-15T00:00:00+00:00",
        "assets": [
            {
                "attachment_id": "att_plain",
                "session_id": service.session_id,
                "user_id": service.user_id,
                "name": "notes",
                "object_path": f"{service.files_prefix}/input/notes",
                "workspace_path": "input/notes",
                "mime_type": None,
                "source_kind": "user_upload",
                "role": "source",
                "input_mode": "workspace_file",
                "size_bytes": 20,
                "view_type": "binary",
                "capabilities": ["sandbox_process"],
                "parse_status": "none",
                "created_at": "2026-03-15T00:00:00+00:00",
                "metadata": {},
            }
        ],
    }

    async def fake_object_exists(object_name: str) -> bool:
        return object_name in {
            service.manifest_object_path,
            f"{service.files_prefix}/input/notes",
        }

    async def fake_download_file(object_name: str) -> bytes:
        return json.dumps(manifest_payload).encode("utf-8")

    async def fake_upload_file(object_name: str, file_data: bytes, content_type: str = "application/octet-stream") -> str:
        return f"reader-uploads/{object_name}"

    monkeypatch.setattr(workspace_module, "object_exists", fake_object_exists)
    monkeypatch.setattr(workspace_module, "download_file", fake_download_file)
    monkeypatch.setattr(workspace_module, "upload_file", fake_upload_file)

    assets = await service.resolve_request_attachments(
        attachments=[
            WorkspaceAttachmentInput(
                name="notes",
                object_path=f"{service.files_prefix}/input/notes",
                mime_type="text/plain",
            )
        ],
        image_data_urls=[],
    )

    assert len(assets) == 1
    asset = assets[0]
    assert asset.view_type == "text"
    assert asset.available_views == ["text"]
    assert asset.capabilities == ["read_text"]
    assert asset.parse_status == "ready"


@pytest.mark.asyncio
async def test_workspace_service_merges_latest_manifest_before_save(monkeypatch):
    service = WorkspaceService(
        session_id="12121212-1212-1212-1212-121212121212",
        user_id="34343434-3434-3434-3434-343434343434",
    )
    stale_manifest = {
        "session_id": service.session_id,
        "user_id": service.user_id,
        "version": 1,
        "updated_at": "2026-03-15T00:00:00+00:00",
        "assets": [
            {
                "attachment_id": "att_existing",
                "session_id": service.session_id,
                "user_id": service.user_id,
                "name": "report.docx",
                "object_path": f"{service.files_prefix}/input/report.docx",
                "workspace_path": "input/report.docx",
                "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "source_kind": "user_upload",
                "role": "source",
                "input_mode": "workspace_file",
                "size_bytes": 1024,
                "created_at": "2026-03-15T00:00:00+00:00",
                "metadata": {},
            }
        ],
    }
    latest_manifest = {
        "session_id": service.session_id,
        "user_id": service.user_id,
        "version": 1,
        "updated_at": "2026-03-15T00:00:05+00:00",
        "assets": [
            *stale_manifest["assets"],
            {
                "attachment_id": "att_remote",
                "session_id": service.session_id,
                "user_id": service.user_id,
                "name": "remote.txt",
                "object_path": f"{service.files_prefix}/derived/remote.txt",
                "workspace_path": "derived/remote.txt",
                "mime_type": "text/plain",
                "source_kind": "system_derived",
                "role": "derived",
                "input_mode": "workspace_file",
                "size_bytes": 64,
                "created_at": "2026-03-15T00:00:05+00:00",
                "metadata": {"origin": "remote"},
            },
        ],
    }
    saved_payloads = []
    manifest_reads = {"count": 0}

    async def fake_object_exists(object_name: str) -> bool:
        return object_name in {
            service.manifest_object_path,
            f"{service.files_prefix}/input/new.txt",
        }

    async def fake_download_file(object_name: str) -> bytes:
        assert object_name == service.manifest_object_path
        manifest_reads["count"] += 1
        payload = stale_manifest if manifest_reads["count"] == 1 else latest_manifest
        return json.dumps(payload).encode("utf-8")

    async def fake_upload_file(object_name: str, file_data: bytes, content_type: str = "application/octet-stream") -> str:
        saved_payloads.append(json.loads(file_data.decode("utf-8")))
        return f"reader-uploads/{object_name}"

    monkeypatch.setattr(workspace_module, "object_exists", fake_object_exists)
    monkeypatch.setattr(workspace_module, "download_file", fake_download_file)
    monkeypatch.setattr(workspace_module, "upload_file", fake_upload_file)

    assets = await service.resolve_request_attachments(
        attachments=[
            WorkspaceAttachmentInput(
                name="new.txt",
                object_path=f"{service.files_prefix}/input/new.txt",
                mime_type="text/plain",
            )
        ],
        image_data_urls=[],
    )

    assert len(assets) == 2
    assert saved_payloads
    saved_paths = {item["workspace_path"] for item in saved_payloads[-1]["assets"]}
    assert "derived/remote.txt" in saved_paths
    assert "input/new.txt" in saved_paths


@pytest.mark.asyncio
async def test_workspace_service_rejects_manifest_asset_outside_session_workspace(monkeypatch):
    service = WorkspaceService(
        session_id="99999999-9999-9999-9999-999999999999",
        user_id="88888888-8888-8888-8888-888888888888",
    )
    manifest_payload = {
        "session_id": service.session_id,
        "user_id": service.user_id,
        "version": 1,
        "updated_at": "2026-03-15T00:00:00+00:00",
        "assets": [
            {
                "attachment_id": "att_invalid",
                "session_id": service.session_id,
                "user_id": service.user_id,
                "name": "bad.txt",
                "object_path": "v2/tenants/other-user/sessions/other-session/files/input/bad.txt",
                "workspace_path": "input/bad.txt",
                "mime_type": "text/plain",
                "source_kind": "user_upload",
                "role": "source",
                "input_mode": "workspace_file",
                "size_bytes": 1,
                "created_at": "2026-03-15T00:00:00+00:00",
                "metadata": {},
            }
        ],
    }

    async def fake_object_exists(object_name: str) -> bool:
        return object_name == service.manifest_object_path

    async def fake_download_file(object_name: str) -> bytes:
        return json.dumps(manifest_payload).encode("utf-8")

    monkeypatch.setattr(workspace_module, "object_exists", fake_object_exists)
    monkeypatch.setattr(workspace_module, "download_file", fake_download_file)

    with pytest.raises(ValueError, match="workspace_manifest_invalid_asset"):
        await service.load_manifest()


@pytest.mark.asyncio
async def test_workspace_service_rejects_non_files_namespace_attachment(monkeypatch):
    service = WorkspaceService(
        session_id="56565656-5656-5656-5656-565656565656",
        user_id="78787878-7878-7878-7878-787878787878",
    )

    async def fake_object_exists(object_name: str) -> bool:
        return object_name == service.manifest_object_path

    async def fake_download_file(object_name: str) -> bytes:
        return json.dumps(
            {
                "session_id": service.session_id,
                "user_id": service.user_id,
                "version": 1,
                "updated_at": "2026-03-15T00:00:00+00:00",
                "assets": [],
            }
        ).encode("utf-8")

    monkeypatch.setattr(workspace_module, "object_exists", fake_object_exists)
    monkeypatch.setattr(workspace_module, "download_file", fake_download_file)

    with pytest.raises(ValueError, match="不属于当前会话工作区"):
        await service.resolve_request_attachments(
            attachments=[
                WorkspaceAttachmentInput(
                    name="task-state.json",
                    object_path=f"{service.base_prefix}/tasks/task-state.json",
                )
            ],
            image_data_urls=[],
        )


def test_workspace_service_builds_compact_workspace_brief():
    service = WorkspaceService(
        session_id="90909090-9090-9090-9090-909090909090",
        user_id="10101010-1010-1010-1010-101010101010",
    )
    assets = [
        WorkspaceAttachmentRecord(
            attachment_id=f"att_{index}",
            session_id=service.session_id,
            user_id=service.user_id,
            name=f"file-{index}.txt",
            object_path=f"{service.files_prefix}/input/file-{index}.txt",
            workspace_path=f"input/file-{index}.txt",
            mime_type="text/plain",
            source_kind="user_upload",
            role="source",
            input_mode="workspace_file",
            size_bytes=100 + index,
            sha256=f"sha-{index}",
            created_at="2026-03-15T00:00:00+00:00",
            metadata={},
        )
        for index in range(7)
    ]

    brief = service.build_agent_workspace_brief(assets)

    assert "当前会话工作区共有 7 个真实文件" in brief
    assert "其余 2 个文件未在此展开" in brief
    assert "input/file-0.txt" in brief
    assert "input/file-6.txt" not in brief
