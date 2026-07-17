"""lumen runtime preparation endpoints."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any
from pathlib import Path
from typing import Literal, Optional
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from config.database import get_db
from config.settings import settings
from middlewares.auth import AuthenticatedIdentity, get_current_chat_identity
from models.user import User
from modules.chat.runtime_thread_guard import runtime_thread_guard


router = APIRouter(prefix="/chat-runtime", tags=["Chat Runtime"])


def _create_chat_service(db: AsyncSession):
    from modules.chat.repositories.chat_repository import ChatRepository
    from modules.chat.services.chat_service import ChatService

    return ChatService(ChatRepository(db))


def _create_document_service(db: AsyncSession):
    from modules.knowledge.services.document_service import DocumentService

    return DocumentService(db)


def _create_model_config_service(db: AsyncSession):
    from modules.model_config.services.model_config_service import ModelConfigService

    return ModelConfigService(db)


def _create_runtime_knowledge_scope_service(db: AsyncSession):
    from modules.chat.services.runtime_knowledge_scope_service import (
        RuntimeKnowledgeScopeService,
    )

    return RuntimeKnowledgeScopeService(db)


def _get_thread_materialization_service():
    from modules.chat.services.thread_materialization_service import thread_materialization_service

    return thread_materialization_service


def _get_insight_runtime_service():
    from modules.chat.services.insight_runtime_service import insight_runtime_service

    return insight_runtime_service


class ThreadPrepareRequest(BaseModel):
    """Prepare an lumen thread for a chat session."""

    model_config = ConfigDict(extra="forbid")

    model_name: Optional[str] = None
    thinking_enabled: Optional[bool] = None
    plan_mode: Optional[bool] = None
    sync_workspace_assets: bool = Field(default=True)
    sync_kb_documents: bool = Field(default=True)
    persist_session_config: bool = Field(default=True)


class ThreadPrepareResponse(BaseModel):
    """Prepared runtime metadata for the frontend chat runtime."""

    session_id: str
    thread_id: str
    runtime: Literal["lumen"] = "lumen"
    runs_path: str
    run_stream_path: str
    run_request_template: dict


class ThreadUploadMutationResponse(BaseModel):
    """Thread upload mutation response for chat composer uploads."""

    session_id: str
    thread_id: str
    runtime: Literal["lumen"] = "lumen"
    success: bool
    files: list[dict]
    count: int
    message: str


class ThreadUploadDeleteResponse(BaseModel):
    """Thread upload delete response for chat composer uploads."""

    session_id: str
    thread_id: str
    runtime: Literal["lumen"] = "lumen"
    success: bool
    message: str


_UPLOAD_RESPONSE_FIELDS = (
    "filename",
    "size",
    "virtual_path",
    "extension",
    "modified",
    "markdown_file",
    "markdown_virtual_path",
)


def _serialize_runtime_upload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: payload[key]
        for key in _UPLOAD_RESPONSE_FIELDS
        if key in payload
    }


def _normalize_config_id_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return normalized


def _normalize_config_uuid_list(value: Any, *, field_name: str) -> list[str]:
    normalized: list[str] = []
    for raw_value in _normalize_config_id_list(value):
        try:
            canonical = str(UUID(raw_value))
        except ValueError as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": {
                        "code": "KNOWLEDGE_SCOPE_NOT_READY",
                        "message": f"Session {field_name} contains an invalid identifier",
                    }
                },
            ) from exc
        if canonical not in normalized:
            normalized.append(canonical)
    return normalized


def _resolve_session_thread_id(session: Any, session_id: UUID) -> str:
    session_config = dict(getattr(session, "config", {}) or {})
    configured_thread_id = str(session_config.get("threadId") or session_id).strip()
    insight_runtime_service = _get_insight_runtime_service()
    return insight_runtime_service.build_thread_id(configured_thread_id)


async def _get_upload_file_size(upload: UploadFile) -> int:
    if isinstance(upload.size, int) and upload.size >= 0:
        return upload.size

    file_object = upload.file
    current_position = file_object.tell()
    file_object.seek(0, os.SEEK_END)
    size = file_object.tell()
    file_object.seek(current_position, os.SEEK_SET)
    return size


_KB_MARKDOWN_BATCH_SIZE = 20


async def _load_kb_document_batch(
    *,
    document_service: Any,
    kb_id: str,
    doc_ids: list[str],
    user_id: str,
    allow_not_found: bool = False,
) -> list[dict[str, Any]]:
    requested_doc_ids = list(dict.fromkeys(doc_ids))
    batch = await document_service.get_documents_markdown_batch(
        requested_doc_ids,
        kb_id,
        user_id,
    )
    documents_payload = batch.get("documents") if isinstance(batch, dict) else {}
    names_payload = batch.get("document_names") if isinstance(batch, dict) else {}
    versions_payload = batch.get("document_versions") if isinstance(batch, dict) else {}
    failed_payload = batch.get("failed") if isinstance(batch, dict) else None
    failure_reasons = batch.get("failure_reasons") if isinstance(batch, dict) else None
    if (
        not isinstance(documents_payload, dict)
        or not isinstance(names_payload, dict)
        or not isinstance(versions_payload, dict)
        or not isinstance(failed_payload, list)
        or not isinstance(failure_reasons, dict)
    ):
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "code": "KNOWLEDGE_MATERIALIZATION_UNAVAILABLE",
                    "message": "Knowledge document loading returned an invalid response",
                }
            },
        )

    blocking_failures = [
        str(doc_id)
        for doc_id in failed_payload
        if not (
            allow_not_found
            and str(failure_reasons.get(str(doc_id), "")).strip() == "not_found"
        )
    ]
    if blocking_failures:
        reasons = {
            str(failure_reasons.get(doc_id, "unknown")).strip() or "unknown"
            for doc_id in blocking_failures
        }
        transient = bool(reasons & {"storage_error"})
        raise HTTPException(
            status_code=503 if transient else 409,
            detail={
                "error": {
                    "code": (
                        "KNOWLEDGE_MATERIALIZATION_UNAVAILABLE"
                        if transient
                        else "KNOWLEDGE_SCOPE_NOT_READY"
                    ),
                    "message": "One or more selected knowledge documents could not be prepared",
                    "failed_doc_ids": blocking_failures,
                    "failure_reasons": {
                        doc_id: str(failure_reasons.get(doc_id, "unknown"))
                        for doc_id in blocking_failures
                    },
                }
            },
        )

    loaded: list[dict[str, Any]] = []
    for doc_id, content in documents_payload.items():
        normalized_doc_id = str(doc_id or "").strip()
        if not normalized_doc_id or not isinstance(content, str) or not content.strip():
            continue
        raw_name = str(names_payload.get(doc_id) or normalized_doc_id).strip()
        document_revision = str(versions_payload.get(doc_id) or "").strip()
        if not document_revision:
            raise HTTPException(
                status_code=503,
                detail={
                    "error": {
                        "code": "KNOWLEDGE_MATERIALIZATION_UNAVAILABLE",
                        "message": "Knowledge document loading omitted its revision",
                    }
                },
            )
        normalized_name = (
            raw_name
            if raw_name.lower().endswith(".md")
            else f"{Path(raw_name).stem}.md"
        )
        loaded.append(
            {
                "kb_id": kb_id,
                "doc_id": normalized_doc_id,
                "name": normalized_name,
                "content": content,
                "document_revision": document_revision,
            }
        )

    successful_ids = {item["doc_id"] for item in loaded}
    requested_id_set = set(requested_doc_ids)
    failed_ids = {str(doc_id) for doc_id in failed_payload}
    if (
        successful_ids - requested_id_set
        or failed_ids - requested_id_set
        or successful_ids & failed_ids
    ):
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "code": "KNOWLEDGE_MATERIALIZATION_UNAVAILABLE",
                    "message": "Knowledge document loading returned an inconsistent batch",
                }
            },
        )
    unresolved = [
        doc_id
        for doc_id in requested_doc_ids
        if doc_id not in successful_ids
        and not (
            allow_not_found
            and str(failure_reasons.get(doc_id, "")).strip() == "not_found"
        )
    ]
    if unresolved:
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "code": "KNOWLEDGE_MATERIALIZATION_UNAVAILABLE",
                    "message": "Knowledge document loading returned an incomplete batch",
                }
            },
        )
    return loaded


async def _iter_selected_kb_documents(
    *,
    session_config: dict[str, Any],
    current_user: User,
    db: AsyncSession,
) -> AsyncIterator[dict[str, Any]]:
    kb_ids = _normalize_config_uuid_list(
        session_config.get("kbIds"),
        field_name="kbIds",
    )
    if not kb_ids:
        return

    requested_doc_ids = _normalize_config_uuid_list(
        session_config.get("docIds"),
        field_name="docIds",
    )
    document_service = _create_document_service(db)

    seen_doc_ids: set[str] = set()

    for kb_id in kb_ids:
        if requested_doc_ids:
            for index in range(0, len(requested_doc_ids), _KB_MARKDOWN_BATCH_SIZE):
                loaded = await _load_kb_document_batch(
                    document_service=document_service,
                    kb_id=kb_id,
                    doc_ids=requested_doc_ids[index : index + _KB_MARKDOWN_BATCH_SIZE],
                    user_id=str(current_user.id),
                    allow_not_found=True,
                )
                for document in loaded:
                    doc_id = document["doc_id"]
                    if doc_id in seen_doc_ids:
                        continue
                    seen_doc_ids.add(doc_id)
                    yield document
            continue

        page = 1
        page_size = 100
        while True:
            materialized_ids, total = (
                await document_service.list_materialized_document_ids(
                    kb_id,
                    str(current_user.id),
                    page=page,
                    page_size=page_size,
                )
            )
            document_ids = [
                str(doc_id).strip()
                for doc_id in materialized_ids
                if str(doc_id).strip()
            ]
            for index in range(0, len(document_ids), _KB_MARKDOWN_BATCH_SIZE):
                loaded = await _load_kb_document_batch(
                    document_service=document_service,
                    kb_id=kb_id,
                    doc_ids=document_ids[index : index + _KB_MARKDOWN_BATCH_SIZE],
                    user_id=str(current_user.id),
                )
                for document in loaded:
                    doc_id = document["doc_id"]
                    if doc_id in seen_doc_ids:
                        continue
                    seen_doc_ids.add(doc_id)
                    yield document
            if page * page_size >= total or not materialized_ids:
                break
            page += 1

    if requested_doc_ids and seen_doc_ids != set(requested_doc_ids):
        raise HTTPException(
            status_code=409,
            detail={
                "error": {
                    "code": "KNOWLEDGE_SCOPE_NOT_READY",
                    "message": "One or more selected documents no longer exist in the selected knowledge bases",
                }
            },
        )


@router.post("/sessions/{session_id}/thread/prepare", response_model=ThreadPrepareResponse)
async def prepare_session_thread(
    session_id: UUID,
    request: ThreadPrepareRequest,
    identity: AuthenticatedIdentity = Depends(get_current_chat_identity),
    db: AsyncSession = Depends(get_db),
):
    """Prepare an lumen thread and project session materials into it."""

    insight_runtime_service = _get_insight_runtime_service()
    chat_service = _create_chat_service(db)
    current_user = identity.user
    session = await chat_service.get_session(session_id, current_user.id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    session_config = dict(session.config or {})
    resolved_ui_mode = str(session_config.get("uiMode") or "normal").strip().lower()
    is_plan_mode = (
        request.plan_mode
        if request.plan_mode is not None
        else resolved_ui_mode == "plan"
    )
    configured_model_name = (
        request.model_name
        or str(session_config.get("modelName") or "").strip()
        or None
    )
    resolved_thinking_enabled = (
        request.thinking_enabled
        if request.thinking_enabled is not None
        else bool(session_config.get("deepThinking", False))
    )

    normalized_thread_id = _resolve_session_thread_id(session, session_id)
    await insight_runtime_service.ensure_thread_exists(normalized_thread_id)
    resolved_assistant_id = await insight_runtime_service.resolve_assistant_id()
    runtime_models = await insight_runtime_service.list_runtime_models()
    model_config_service = _create_model_config_service(db)
    model_resolution = await model_config_service.resolve_selected_model(
        user_id=current_user.id,
        selected_model_name=configured_model_name,
        runtime_models=runtime_models,
        thread_id=normalized_thread_id,
    )
    resolved_runtime_model_name = str(model_resolution["runtime_model_name"]).strip()

    thread_materialization_service = _get_thread_materialization_service()
    runtime_knowledge_files: list[dict[str, Any]] | None = None
    if request.sync_workspace_assets or request.sync_kb_documents:
        async with runtime_thread_guard(
            thread_materialization_service,
            normalized_thread_id,
        ):
            try:
                has_active_run = await insight_runtime_service.has_active_thread_run(
                    normalized_thread_id
                )
            except httpx.HTTPError as exc:
                raise HTTPException(
                    status_code=503,
                    detail={
                        "error": {
                            "code": "RUNTIME_UNAVAILABLE",
                            "message": "Runtime run state could not be validated",
                        }
                    },
                ) from exc
            if has_active_run:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": {
                            "code": "THREAD_RUN_ACTIVE",
                            "message": "Thread files cannot be prepared while the thread has an active run",
                        }
                    },
                )

            latest_session = await chat_service.get_session(
                session_id,
                current_user.id,
            )
            if latest_session is None:
                raise HTTPException(status_code=404, detail="Session not found")
            latest_session_config = dict(latest_session.config or {})
            session_config.update(latest_session_config)

            if request.sync_workspace_assets:
                await thread_materialization_service.sync_session_workspace(
                    session_id=str(session_id),
                    user_id=str(current_user.id),
                    thread_id=normalized_thread_id,
                    guard_acquired=True,
                )

            if request.sync_kb_documents:
                previous_runtime_knowledge_files = (
                    latest_session_config.get("runtimeKnowledgeFiles")
                    if "runtimeKnowledgeFiles" in latest_session_config
                    else None
                )
                if previous_runtime_knowledge_files is not None and not isinstance(
                    previous_runtime_knowledge_files,
                    list,
                ):
                    previous_runtime_knowledge_files = []
                selected_kb_documents = _iter_selected_kb_documents(
                    session_config=latest_session_config,
                    current_user=current_user,
                    db=db,
                )
                materialized_kb_documents = (
                    await thread_materialization_service.sync_knowledge_documents(
                        thread_id=normalized_thread_id,
                        knowledge_documents=selected_kb_documents,
                        previous_materialized=previous_runtime_knowledge_files,
                        guard_acquired=True,
                        defer_cleanup=True,
                    )
                )
                runtime_knowledge_files = [
                    {
                        key: (
                            int(item[key])
                            if key == "size_bytes"
                            else str(item[key])
                        )
                        for key in (
                            "kb_id",
                            "doc_id",
                            "document_revision",
                            "content_sha256",
                            "thread_filename",
                            "size_bytes",
                        )
                        if item.get(key) is not None
                    }
                    for item in materialized_kb_documents
                ]
                scope_service = _create_runtime_knowledge_scope_service(db)
                current_scope = await scope_service.resolve_current_scope(
                    session_config=latest_session_config,
                    current_user=current_user,
                )
                scope_service.validate_manifest(
                    scope=current_scope,
                    raw_manifest=runtime_knowledge_files,
                )
                # The manifest is an internal authorization record and must be
                # durable before stale Runtime files are removed.
                await chat_service.update_session_config(
                    session_id=session_id,
                    user_id=current_user.id,
                    config_updates={"runtimeKnowledgeFiles": runtime_knowledge_files},
                )
                session_config["runtimeKnowledgeFiles"] = runtime_knowledge_files
                await thread_materialization_service.cleanup_stale_knowledge_uploads(
                    thread_id=normalized_thread_id,
                    desired_filenames=[
                        item["thread_filename"] for item in runtime_knowledge_files
                    ],
                    guard_acquired=True,
                )

    runtime_config_updates = {
        "runtime": "lumen",
        "threadId": normalized_thread_id,
        "assistantId": resolved_assistant_id,
        "modelName": resolved_runtime_model_name,
        "deepThinking": resolved_thinking_enabled,
    }
    session_config.update(runtime_config_updates)
    if request.persist_session_config:
        await chat_service.update_session_config(
            session_id=session_id,
            user_id=current_user.id,
            config_updates=runtime_config_updates,
        )

    run_request_template = insight_runtime_service.build_run_request_template(
        thread_id=normalized_thread_id,
        assistant_id=resolved_assistant_id,
        model_name=resolved_runtime_model_name,
        thinking_enabled=resolved_thinking_enabled,
        is_plan_mode=is_plan_mode,
    )
    return ThreadPrepareResponse(
        session_id=str(session_id),
        thread_id=normalized_thread_id,
        runs_path=f"/chat-runtime/sessions/{session_id}/runs",
        run_stream_path=f"/chat-runtime/sessions/{session_id}/runs/stream",
        run_request_template=run_request_template,
    )


@router.post("/sessions/{session_id}/thread/uploads", response_model=ThreadUploadMutationResponse)
async def upload_session_thread_files(
    session_id: UUID,
    files: list[UploadFile] = File(...),
    identity: AuthenticatedIdentity = Depends(get_current_chat_identity),
    db: AsyncSession = Depends(get_db),
):
    """Upload chat composer files directly into the runtime thread uploads area."""
    if identity.is_guest:
        raise HTTPException(
            status_code=403,
            detail={"error": {"code": "GUEST_LOGIN_REQUIRED", "message": "游客模式下暂不支持上传文件，请先登录。"}},
        )

    if not files:
        raise HTTPException(status_code=400, detail="No files provided")

    insight_runtime_service = _get_insight_runtime_service()
    chat_service = _create_chat_service(db)
    current_user = identity.user
    session = await chat_service.get_session(session_id, current_user.id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    normalized_thread_id = _resolve_session_thread_id(session, session_id)
    uploaded_files: list[dict[str, Any]] = []
    for upload in files:
        if not upload.filename:
            continue
        if Path(upload.filename).name.lower().startswith("kb__"):
            raise HTTPException(
                status_code=400,
                detail={
                    "error": {
                        "code": "RESERVED_FILENAME",
                        "message": "File name uses a reserved Runtime namespace",
                    }
                },
            )
        file_size = await _get_upload_file_size(upload)
        if file_size > settings.MAX_UPLOAD_SIZE:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": {
                        "code": "FILE_TOO_LARGE",
                        "message": f"File too large. Max size is {settings.MAX_UPLOAD_SIZE // 1024 // 1024}MB",
                    }
                },
            )
        await upload.seek(0)
        uploaded_files.append(
            _serialize_runtime_upload(await insight_runtime_service.upload_file_object(
                thread_id=normalized_thread_id,
                filename=upload.filename,
                file_object=upload.file,
                content_type=upload.content_type,
            ))
        )

    return ThreadUploadMutationResponse(
        session_id=str(session_id),
        thread_id=normalized_thread_id,
        success=True,
        files=uploaded_files,
        count=len(uploaded_files),
        message=f"Successfully uploaded {len(uploaded_files)} file(s)",
    )


@router.delete("/sessions/{session_id}/thread/uploads/{filename}", response_model=ThreadUploadDeleteResponse)
async def delete_session_thread_file(
    session_id: UUID,
    filename: str,
    companion_filename: str | None = None,
    identity: AuthenticatedIdentity = Depends(get_current_chat_identity),
    db: AsyncSession = Depends(get_db),
):
    """Delete a chat composer file from the runtime thread uploads area."""
    if identity.is_guest:
        raise HTTPException(
            status_code=403,
            detail={"error": {"code": "GUEST_LOGIN_REQUIRED", "message": "游客模式下暂不支持上传文件，请先登录。"}},
        )

    if any(
        Path(candidate).name.lower().startswith("kb__")
        for candidate in (filename, companion_filename)
        if candidate
    ):
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": "RESERVED_FILENAME",
                    "message": "File name uses a reserved Runtime namespace",
                }
            },
        )

    insight_runtime_service = _get_insight_runtime_service()
    chat_service = _create_chat_service(db)
    current_user = identity.user
    session = await chat_service.get_session(session_id, current_user.id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    normalized_thread_id = _resolve_session_thread_id(session, session_id)
    result = await insight_runtime_service.delete_thread_upload(
        thread_id=normalized_thread_id,
        filename=filename,
        companion_filename=companion_filename,
    )
    return ThreadUploadDeleteResponse(
        session_id=str(session_id),
        thread_id=normalized_thread_id,
        success=bool(result.get("success", True)),
        message=str(result.get("message") or f"Deleted {filename}"),
    )
