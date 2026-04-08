"""Project session workspace assets into lumen thread uploads."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
import re
import tempfile
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None

from schemas.workspace import WorkspaceAttachmentRecord
from modules.chat.services.insight_runtime_service import InsightRuntimeService, insight_runtime_service
from modules.chat.services.workspace_service import WorkspaceService
from utils.minio_client import download_file

_thread_materialization_locks: dict[str, asyncio.Lock] = {}
_thread_materialization_lock_guard = asyncio.Lock()
_thread_materialization_lock_dir = Path(tempfile.gettempdir()) / "lumen-thread-locks"


async def _get_thread_materialization_lock(thread_id: str) -> asyncio.Lock:
    async with _thread_materialization_lock_guard:
        lock = _thread_materialization_locks.get(thread_id)
        if lock is None:
            lock = asyncio.Lock()
            _thread_materialization_locks[thread_id] = lock
        return lock


def _materialization_lock_path(thread_id: str) -> Path:
    safe_thread_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", thread_id).strip("._") or "thread"
    return _thread_materialization_lock_dir / f"materialization-{safe_thread_id}.lock"


def _acquire_process_lock(lock_path: Path):
    if fcntl is None:
        return None
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+", encoding="utf-8")
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    return handle


def _release_process_lock(handle) -> None:
    if handle is None or fcntl is None:
        return
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


@asynccontextmanager
async def _acquire_thread_materialization_guard(thread_id: str):
    lock = await _get_thread_materialization_lock(thread_id)
    async with lock:
        process_lock_handle = await asyncio.to_thread(
            _acquire_process_lock,
            _materialization_lock_path(thread_id),
        )
        try:
            yield
        finally:
            await asyncio.to_thread(_release_process_lock, process_lock_handle)


class ThreadMaterializationService:
    """Synchronize session-scoped workspace materials into an Insight thread."""

    def __init__(
        self,
        runtime_service: InsightRuntimeService | None = None,
    ) -> None:
        self.runtime_service = runtime_service or insight_runtime_service

    async def sync_session_workspace(
        self,
        *,
        session_id: str,
        user_id: str,
        thread_id: str,
    ) -> list[dict[str, Any]]:
        async with _acquire_thread_materialization_guard(thread_id):
            workspace_service = WorkspaceService(session_id=session_id, user_id=user_id)
            manifest = await workspace_service.load_manifest()
            assets = [
                asset
                for asset in manifest.assets
                if asset.role in {"source", "derived"}
            ]
            if not assets:
                return []

            existing_uploads = await self.runtime_service.list_thread_uploads(thread_id)
            existing_by_filename = {
                str(item.get("filename", "")).strip(): item
                for item in existing_uploads
                if str(item.get("filename", "")).strip()
            }

            materialized: list[dict[str, Any]] = []
            for asset in assets:
                target_filename = self._build_target_filename(asset)
                existing = existing_by_filename.get(target_filename)
                if existing is not None:
                    materialized.append(
                        self._build_materialized_payload(
                            asset,
                            target_filename=target_filename,
                            uploaded_file=existing,
                            synced=False,
                        )
                    )
                    continue

                file_bytes = await download_file(asset.object_path)
                uploaded = await self.runtime_service.upload_bytes(
                    thread_id=thread_id,
                    filename=target_filename,
                    data=file_bytes,
                    content_type=asset.mime_type,
                )
                materialized.append(
                    self._build_materialized_payload(
                        asset,
                        target_filename=target_filename,
                        uploaded_file=uploaded,
                        synced=True,
                    )
                )
            return materialized

    async def sync_knowledge_documents(
        self,
        *,
        thread_id: str,
        knowledge_documents: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if not knowledge_documents:
            return []

        async with _acquire_thread_materialization_guard(thread_id):
            existing_uploads = await self.runtime_service.list_thread_uploads(thread_id)
            existing_by_filename = {
                str(item.get("filename", "")).strip(): item
                for item in existing_uploads
                if str(item.get("filename", "")).strip()
            }

            materialized: list[dict[str, Any]] = []
            for document in knowledge_documents:
                kb_id = str(document.get("kb_id", "")).strip()
                doc_id = str(document.get("doc_id", "")).strip()
                name = str(document.get("name", "")).strip() or f"{doc_id}.md"
                markdown_content = document.get("content")
                if not kb_id or not doc_id or not isinstance(markdown_content, str):
                    continue

                target_filename = self._build_kb_target_filename(
                    kb_id=kb_id,
                    doc_id=doc_id,
                    name=name,
                )
                existing = existing_by_filename.get(target_filename)
                if existing is not None:
                    materialized.append(
                        self._build_kb_materialized_payload(
                            kb_id=kb_id,
                            doc_id=doc_id,
                            name=name,
                            target_filename=target_filename,
                            uploaded_file=existing,
                            synced=False,
                            size_bytes=len(markdown_content.encode("utf-8")),
                        )
                    )
                    continue

                uploaded = await self.runtime_service.upload_bytes(
                    thread_id=thread_id,
                    filename=target_filename,
                    data=markdown_content.encode("utf-8"),
                    content_type="text/markdown; charset=utf-8",
                )
                materialized.append(
                    self._build_kb_materialized_payload(
                        kb_id=kb_id,
                        doc_id=doc_id,
                        name=name,
                        target_filename=target_filename,
                        uploaded_file=uploaded,
                        synced=True,
                        size_bytes=len(markdown_content.encode("utf-8")),
                    )
                )
            return materialized

    @staticmethod
    def _build_target_filename(asset: WorkspaceAttachmentRecord) -> str:
        normalized_workspace_path = asset.workspace_path.replace("/", "__").strip("_")
        suffix = Path(asset.name).suffix
        if suffix and not normalized_workspace_path.endswith(suffix):
            normalized_workspace_path = f"{normalized_workspace_path}{suffix}"
        if not normalized_workspace_path:
            normalized_workspace_path = asset.name
        return f"{asset.attachment_id}__{normalized_workspace_path}"

    @staticmethod
    def _build_kb_target_filename(*, kb_id: str, doc_id: str, name: str) -> str:
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(name).stem.strip() or doc_id).strip("._")
        if not safe_name:
            safe_name = doc_id
        return f"kb__{kb_id}__{doc_id}__{safe_name}.md"

    @staticmethod
    def _build_materialized_payload(
        asset: WorkspaceAttachmentRecord,
        *,
        target_filename: str,
        uploaded_file: dict[str, Any],
        synced: bool,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "attachment_id": asset.attachment_id,
            "name": asset.name,
            "workspace_path": asset.workspace_path,
            "role": asset.role,
            "source_kind": asset.source_kind,
            "mime_type": asset.mime_type,
            "size_bytes": asset.size_bytes,
            "thread_filename": target_filename,
            "synced": synced,
        }
        for key in (
            "artifact_url",
            "virtual_path",
            "markdown_file",
            "markdown_path",
            "markdown_virtual_path",
            "markdown_artifact_url",
        ):
            value = uploaded_file.get(key)
            if isinstance(value, str) and value.strip():
                payload[key] = value.strip()
        size = uploaded_file.get("size")
        try:
            if size is not None:
                resolved_size = int(size)
                if resolved_size >= 0:
                    payload["thread_size_bytes"] = resolved_size
        except (TypeError, ValueError):
            pass
        return payload

    @staticmethod
    def _build_kb_materialized_payload(
        *,
        kb_id: str,
        doc_id: str,
        name: str,
        target_filename: str,
        uploaded_file: dict[str, Any],
        synced: bool,
        size_bytes: int,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "kb_id": kb_id,
            "doc_id": doc_id,
            "name": name,
            "thread_filename": target_filename,
            "source_kind": "knowledge_base_markdown",
            "mime_type": "text/markdown",
            "size_bytes": size_bytes,
            "synced": synced,
        }
        for key in (
            "artifact_url",
            "virtual_path",
            "markdown_file",
            "markdown_path",
            "markdown_virtual_path",
            "markdown_artifact_url",
        ):
            value = uploaded_file.get(key)
            if isinstance(value, str) and value.strip():
                payload[key] = value.strip()
        size = uploaded_file.get("size")
        try:
            if size is not None:
                resolved_size = int(size)
                if resolved_size >= 0:
                    payload["thread_size_bytes"] = resolved_size
        except (TypeError, ValueError):
            pass
        return payload


thread_materialization_service = ThreadMaterializationService()
