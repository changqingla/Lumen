"""lumen runtime preparation endpoints."""

from __future__ import annotations

import os
from typing import Any
from pathlib import Path
from typing import Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from config.database import get_db
from config.settings import settings
from middlewares.auth import get_current_user
from models.user import User


router = APIRouter(prefix="/chat-runtime", tags=["Chat Runtime"])


def _create_chat_service(db: AsyncSession):
    from modules.chat.repositories.chat_repository import ChatRepository
    from modules.chat.services.chat_service import ChatService

    return ChatService(ChatRepository(db))


def _create_document_service(db: AsyncSession):
    from modules.knowledge.services.document_service import DocumentService

    return DocumentService(db)


def _create_workspace_service(session_id: str, user_id: str):
    from modules.chat.services.workspace_service import WorkspaceService

    return WorkspaceService(session_id=session_id, user_id=user_id)


def _create_model_config_service(db: AsyncSession):
    from modules.model_config.services.model_config_service import ModelConfigService

    return ModelConfigService(db)


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
    assistant_id: str
    gateway_base_url: str
    langgraph_base_url: str
    run_stream_path: str
    uploads_path: str
    uploads_list_path: str
    artifacts_base_path: str
    suggestions_path: str
    run_request_template: dict
    session_config: dict
    workspace_summary: str
    workspace_assets: list[dict]
    materialized_files: list[dict]
    kb_materialized_files: list[dict]


class ThreadUploadsResponse(BaseModel):
    """Thread uploads view for frontend attachment panels."""

    session_id: str
    thread_id: str
    runtime: Literal["lumen"] = "lumen"
    uploads: list[dict]
    count: int
    workspace_assets: list[dict]
    materialized_files: list[dict]
    kb_materialized_files: list[dict]


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


async def _resolve_selected_kb_documents(
    *,
    session_config: dict[str, Any],
    current_user: User,
    db: AsyncSession,
) -> list[dict[str, Any]]:
    kb_ids = _normalize_config_id_list(session_config.get("kbIds"))
    if not kb_ids:
        return []

    requested_doc_ids = _normalize_config_id_list(session_config.get("docIds"))
    document_service = _create_document_service(db)

    selected_documents: list[dict[str, Any]] = []
    seen_doc_ids: set[str] = set()

    for kb_id in kb_ids:
        target_doc_ids = requested_doc_ids
        if not target_doc_ids:
            page = 1
            page_size = 100
            collected_doc_ids: list[str] = []
            while True:
                documents, total = await document_service.list_documents(
                    kb_id,
                    str(current_user.id),
                    page=page,
                    page_size=page_size,
                )
                ready_ids = [
                    str(item.get("id", "")).strip()
                    for item in documents
                    if str(item.get("status", "")).strip().lower() == "ready"
                    and str(item.get("id", "")).strip()
                ]
                collected_doc_ids.extend(ready_ids)
                if page * page_size >= total or not documents:
                    break
                page += 1
            target_doc_ids = list(dict.fromkeys(collected_doc_ids))

        if not target_doc_ids:
            continue

        batch = await document_service.get_documents_markdown_batch(
            list(target_doc_ids),
            kb_id,
            str(current_user.id),
        )
        documents_payload = batch.get("documents") if isinstance(batch, dict) else {}
        names_payload = batch.get("document_names") if isinstance(batch, dict) else {}
        if not isinstance(documents_payload, dict):
            continue
        if not isinstance(names_payload, dict):
            names_payload = {}

        for doc_id, content in documents_payload.items():
            normalized_doc_id = str(doc_id or "").strip()
            if (
                not normalized_doc_id
                or normalized_doc_id in seen_doc_ids
                or not isinstance(content, str)
                or not content.strip()
            ):
                continue

            seen_doc_ids.add(normalized_doc_id)
            raw_name = str(names_payload.get(doc_id) or normalized_doc_id).strip()
            normalized_name = raw_name if raw_name.lower().endswith(".md") else f"{Path(raw_name).stem}.md"
            selected_documents.append(
                {
                    "kb_id": kb_id,
                    "doc_id": normalized_doc_id,
                    "name": normalized_name,
                    "content": content,
                }
            )

    return selected_documents


@router.post("/sessions/{session_id}/thread/prepare", response_model=ThreadPrepareResponse)
async def prepare_session_thread(
    session_id: UUID,
    request: ThreadPrepareRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Prepare an lumen thread and project session materials into it."""

    insight_runtime_service = _get_insight_runtime_service()
    chat_service = _create_chat_service(db)
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
    dynamic_model_token = model_resolution.get("dynamic_model_token")

    workspace_service = _create_workspace_service(
        session_id=str(session_id),
        user_id=str(current_user.id),
    )
    manifest = await workspace_service.load_manifest()
    workspace_assets = list(manifest.assets)
    workspace_summary = workspace_service.build_agent_workspace_brief(workspace_assets)

    thread_materialization_service = _get_thread_materialization_service()
    materialized_files: list[dict] = []
    if request.sync_workspace_assets:
        materialized_files = await thread_materialization_service.sync_session_workspace(
            session_id=str(session_id),
            user_id=str(current_user.id),
            thread_id=normalized_thread_id,
        )

    kb_materialized_files: list[dict] = []
    if request.sync_kb_documents:
        selected_kb_documents = await _resolve_selected_kb_documents(
            session_config=session_config,
            current_user=current_user,
            db=db,
        )
        kb_materialized_files = await thread_materialization_service.sync_knowledge_documents(
            thread_id=normalized_thread_id,
            knowledge_documents=selected_kb_documents,
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
    if dynamic_model_token:
        context_payload = run_request_template.get("context")
        if not isinstance(context_payload, dict):
            context_payload = {}
            run_request_template["context"] = context_payload
        context_payload["dynamic_model_token"] = dynamic_model_token

    return ThreadPrepareResponse(
        session_id=str(session_id),
        thread_id=normalized_thread_id,
        assistant_id=resolved_assistant_id,
        gateway_base_url=insight_runtime_service.gateway_public_base_url,
        langgraph_base_url=insight_runtime_service.langgraph_public_base_url,
        run_stream_path=insight_runtime_service.build_run_stream_path(normalized_thread_id),
        uploads_path=insight_runtime_service.build_thread_uploads_path(normalized_thread_id),
        uploads_list_path=insight_runtime_service.build_thread_uploads_list_path(normalized_thread_id),
        artifacts_base_path=insight_runtime_service.build_thread_artifacts_base_path(normalized_thread_id),
        suggestions_path=insight_runtime_service.build_thread_suggestions_path(normalized_thread_id),
        run_request_template=run_request_template,
        session_config=session_config,
        workspace_summary=workspace_summary,
        workspace_assets=[
            asset.to_metadata_payload()
            for asset in workspace_assets
        ],
        materialized_files=materialized_files,
        kb_materialized_files=kb_materialized_files,
    )


@router.post("/sessions/{session_id}/thread/uploads", response_model=ThreadUploadMutationResponse)
async def upload_session_thread_files(
    session_id: UUID,
    files: list[UploadFile] = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Upload chat composer files directly into the runtime thread uploads area."""

    if not files:
        raise HTTPException(status_code=400, detail="No files provided")

    insight_runtime_service = _get_insight_runtime_service()
    chat_service = _create_chat_service(db)
    session = await chat_service.get_session(session_id, current_user.id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    normalized_thread_id = _resolve_session_thread_id(session, session_id)
    uploaded_files: list[dict[str, Any]] = []
    for upload in files:
        if not upload.filename:
            continue
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
            await insight_runtime_service.upload_file_object(
                thread_id=normalized_thread_id,
                filename=upload.filename,
                file_object=upload.file,
                content_type=upload.content_type,
            )
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
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a chat composer file from the runtime thread uploads area."""

    insight_runtime_service = _get_insight_runtime_service()
    chat_service = _create_chat_service(db)
    session = await chat_service.get_session(session_id, current_user.id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    normalized_thread_id = _resolve_session_thread_id(session, session_id)
    result = await insight_runtime_service.delete_thread_upload(
        thread_id=normalized_thread_id,
        filename=filename,
    )
    return ThreadUploadDeleteResponse(
        session_id=str(session_id),
        thread_id=normalized_thread_id,
        success=bool(result.get("success", True)),
        message=str(result.get("message") or f"Deleted {filename}"),
    )


@router.get("/sessions/{session_id}/thread/uploads", response_model=ThreadUploadsResponse)
async def list_session_thread_uploads(
    session_id: UUID,
    sync_workspace_assets: bool = True,
    sync_kb_documents: bool = False,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List Insight thread uploads for a chat session."""

    insight_runtime_service = _get_insight_runtime_service()
    chat_service = _create_chat_service(db)
    session = await chat_service.get_session(session_id, current_user.id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    session_config = dict(session.config or {})
    normalized_thread_id = _resolve_session_thread_id(session, session_id)

    workspace_service = _create_workspace_service(
        session_id=str(session_id),
        user_id=str(current_user.id),
    )
    manifest = await workspace_service.load_manifest()
    workspace_assets = list(manifest.assets)

    thread_materialization_service = _get_thread_materialization_service()
    materialized_files: list[dict] = []
    if sync_workspace_assets:
        materialized_files = await thread_materialization_service.sync_session_workspace(
            session_id=str(session_id),
            user_id=str(current_user.id),
            thread_id=normalized_thread_id,
        )

    kb_materialized_files: list[dict] = []
    if sync_kb_documents:
        selected_kb_documents = await _resolve_selected_kb_documents(
            session_config=session_config,
            current_user=current_user,
            db=db,
        )
        kb_materialized_files = await thread_materialization_service.sync_knowledge_documents(
            thread_id=normalized_thread_id,
            knowledge_documents=selected_kb_documents,
        )

    uploads = await insight_runtime_service.list_thread_uploads(normalized_thread_id)
    return ThreadUploadsResponse(
        session_id=str(session_id),
        thread_id=normalized_thread_id,
        uploads=uploads,
        count=len(uploads),
        workspace_assets=[
            asset.to_metadata_payload()
            for asset in workspace_assets
        ],
        materialized_files=materialized_files,
        kb_materialized_files=kb_materialized_files,
    )
