"""
聊天会话控制器
"""
import hashlib
import mimetypes
from typing import List, Literal, Optional
from urllib.parse import quote
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask

from config.database import get_db
from middlewares.auth import get_current_user
from models.user import User
from schemas.workspace import WorkspaceAttachmentInput


router = APIRouter(prefix="/chat", tags=["Chat"])
_MAX_IMAGE_BYTES = 5 * 1024 * 1024
_ARTIFACT_URL_EXPIRES_SECONDS = 3600


def _create_chat_service(db: AsyncSession):
    from modules.chat.repositories.chat_repository import ChatRepository
    from modules.chat.services.chat_service import ChatService

    return ChatService(ChatRepository(db))


def _create_workspace_service(session_id: str, user_id: str):
    from modules.chat.services.workspace_service import WorkspaceService

    return WorkspaceService(session_id=session_id, user_id=user_id)


def _get_minio_helpers():
    from utils.minio_client import get_file_url, object_exists

    return get_file_url, object_exists


def _get_insight_runtime_service():
    from modules.chat.services.insight_runtime_service import insight_runtime_service

    return insight_runtime_service


def _create_model_config_service(db: AsyncSession):
    from modules.model_config.services.model_config_service import ModelConfigService

    return ModelConfigService(db)


def _compute_tenant_key(user_id: UUID) -> str:
    return hashlib.blake2b(str(user_id).encode("utf-8"), digest_size=8).hexdigest()


def _build_session_prefix(user_id: UUID, session_id: UUID) -> str:
    tenant_key = _compute_tenant_key(user_id)
    return f"v2/tenants/{tenant_key}/sessions/{session_id}/"

def _is_allowed_artifact_object_path(user_id: UUID, session_id: UUID, object_path: str) -> bool:
    """
    校验 object_path 是否属于当前用户当前会话可访问的产物路径。

    允许：
    1) 主会话: .../sessions/{session_id}/files/*
    2) 子 Agent: .../sessions/{session_id}/agents/{sub_agent_id}/files/*
    """
    session_prefix = _build_session_prefix(user_id, session_id)
    if not object_path.startswith(session_prefix):
        return False

    remainder = object_path[len(session_prefix):]
    if not remainder:
        return False

    parts = [part for part in remainder.split("/") if part]
    if len(parts) < 2:
        return False

    # 主会话 files 路径
    if parts[0] == "files":
        return True

    # 子 Agent files 路径: agents/{sub_agent_id}/files/...
    if len(parts) >= 4 and parts[0] == "agents" and parts[2] == "files":
        return bool(parts[1].strip())

    return False


def _is_allowed_insight_artifact_path(session, object_path: str) -> bool:
    """允许 Lumen 线程虚拟产物路径。"""
    normalized_object_path = str(object_path or "").strip().lstrip("/")
    if not normalized_object_path:
        return False

    session_config = dict(getattr(session, "config", {}) or {})
    runtime = str(session_config.get("runtime") or "").strip().lower()
    if runtime != "lumen":
        return False

    return (
        normalized_object_path.startswith("mnt/user-data/outputs/")
        or normalized_object_path.startswith("mnt/user-data/uploads/")
    )


def _is_allowed_session_object_path(session, user_id: UUID, session_id: UUID, object_path: str) -> bool:
    return (
        _is_allowed_artifact_object_path(user_id, session_id, object_path)
        or _is_allowed_insight_artifact_path(session, object_path)
    )


def _build_insight_artifact_url(session, object_path: str) -> Optional[str]:
    session_config = dict(getattr(session, "config", {}) or {})
    if str(session_config.get("runtime") or "").strip().lower() != "lumen":
        return None

    thread_id = str(session_config.get("threadId") or "").strip()
    normalized_object_path = str(object_path or "").strip().lstrip("/")
    if not thread_id or not normalized_object_path:
        return None

    encoded_path = quote(normalized_object_path, safe="/")
    insight_runtime_service = _get_insight_runtime_service()
    gateway_base_url = insight_runtime_service.gateway_public_base_url
    return f"{gateway_base_url}/api/threads/{thread_id}/artifacts/{encoded_path}"


def _build_internal_insight_artifact_url(session, object_path: str) -> Optional[str]:
    session_config = dict(getattr(session, "config", {}) or {})
    if str(session_config.get("runtime") or "").strip().lower() != "lumen":
        return None

    thread_id = str(session_config.get("threadId") or "").strip()
    normalized_object_path = str(object_path or "").strip().lstrip("/")
    if not thread_id or not normalized_object_path:
        return None

    encoded_path = quote(normalized_object_path, safe="/")
    insight_runtime_service = _get_insight_runtime_service()
    return f"{insight_runtime_service.gateway_url}/api/threads/{thread_id}/artifacts/{encoded_path}?download=true"


def _build_download_headers(file_name: str) -> dict[str, str]:
    encoded_filename = quote(file_name)
    return {
        "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}"
    }


async def _validate_session_model_name(
    *,
    db: AsyncSession,
    current_user: User,
    model_name: str | None,
) -> None:
    normalized_model_name = str(model_name or "").strip()
    if not normalized_model_name:
        return

    insight_runtime_service = _get_insight_runtime_service()
    runtime_models = await insight_runtime_service.list_runtime_models()
    model_config_service = _create_model_config_service(db)
    await model_config_service.resolve_selected_model(
        user_id=current_user.id,
        selected_model_name=normalized_model_name,
        runtime_models=runtime_models,
    )


async def _close_httpx_stream(response: httpx.Response, client: httpx.AsyncClient) -> None:
    try:
        await response.aclose()
    finally:
        await client.aclose()


def _merge_message_attachments_with_workspace_assets(
    message_payloads: list[dict],
    workspace_assets: list,
) -> list[dict]:
    """用 manifest 中的最新附件状态覆盖历史消息里的旧附件快照。"""
    if not message_payloads or not workspace_assets:
        return message_payloads

    assets_by_attachment_id = {
        asset.attachment_id: asset.to_metadata_payload()
        for asset in workspace_assets
        if getattr(asset, "attachment_id", None)
    }
    assets_by_object_path = {
        asset.object_path: asset.to_metadata_payload()
        for asset in workspace_assets
        if getattr(asset, "object_path", None)
    }

    merged_messages: list[dict] = []
    for payload in message_payloads:
        attachments = payload.get("attachments")
        if not isinstance(attachments, list) or not attachments:
            merged_messages.append(payload)
            continue

        merged_attachments = []
        for item in attachments:
            if not isinstance(item, dict):
                continue
            attachment_id = str(item.get("attachment_id", "")).strip()
            object_path = str(item.get("object_path", "")).strip()
            latest = None
            if attachment_id:
                latest = assets_by_attachment_id.get(attachment_id)
            if latest is None and object_path:
                latest = assets_by_object_path.get(object_path)
            merged_attachments.append(latest or item)

        merged_messages.append({
            **payload,
            "attachments": merged_attachments,
        })

    return merged_messages


class ChatArtifactPayload(BaseModel):
    """聊天消息中的文件产物元数据。"""
    model_config = ConfigDict(extra="forbid")

    object_path: str = Field(..., min_length=1)
    name: Optional[str] = None
    path: Optional[str] = None
    size_bytes: Optional[int] = Field(default=None, ge=0)
    mime_type: Optional[str] = None
    session_id: Optional[str] = None

    @field_validator("object_path")
    @classmethod
    def validate_object_path(cls, value: str) -> str:
        normalized = (value or "").strip().lstrip("/")
        if ".." in normalized or "\\" in normalized:
            raise ValueError("object_path 包含非法路径字符")
        if not normalized:
            raise ValueError("object_path 不能为空")
        return normalized


class ChatToolTracePayload(BaseModel):
    """聊天消息中的工具执行轨迹。"""
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1)
    call_id: Optional[str] = None
    iteration: Optional[int] = Field(default=None, ge=1)
    args: Optional[object] = None
    result: Optional[object] = None
    success: Optional[bool] = None
    error: Optional[str] = None
    status: Optional[str] = None
    duration_ms: Optional[int] = Field(default=None, ge=0)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        normalized = (value or "").strip()
        if not normalized:
            raise ValueError("name 不能为空")
        return normalized

    @field_validator("call_id")
    @classmethod
    def validate_call_id(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("error")
    @classmethod
    def validate_error(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class ChatTupleToolCallPayload(BaseModel):
    """assistant tuple 中的 tool call。"""
    model_config = ConfigDict(extra="forbid")

    id: Optional[str] = None
    name: str = Field(..., min_length=1)
    args: Optional[object] = None

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        normalized = (value or "").strip()
        if not normalized:
            raise ValueError("name 不能为空")
        return normalized


class ChatTupleMessagePayload(BaseModel):
    """assistant tuple 消息（LangGraph 风格 ai/tool）。"""
    model_config = ConfigDict(extra="forbid")

    type: Literal["ai", "tool"]
    id: str = Field(..., min_length=1)
    content: Optional[str] = None
    tool_calls: List[ChatTupleToolCallPayload] = Field(default_factory=list)
    tool_call_id: Optional[str] = None
    name: Optional[str] = None

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        normalized = (value or "").strip()
        if not normalized:
            raise ValueError("id 不能为空")
        return normalized

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return value

    @field_validator("tool_call_id")
    @classmethod
    def validate_tool_call_id(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def validate_tuple_message(self):
        if self.type == "ai":
            has_content = bool((self.content or "").strip())
            has_tool_calls = bool(self.tool_calls)
            if not has_content and not has_tool_calls:
                raise ValueError("ai tuple 必须至少包含 content 或 tool_calls")
            return self

        if self.type == "tool":
            if not self.tool_call_id:
                raise ValueError("tool tuple 必须包含 tool_call_id")
            if not self.name:
                raise ValueError("tool tuple 必须包含 name")
        return self


class ChatInterruptionPayload(BaseModel):
    """聊天消息中的中途中止信息。"""
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(..., min_length=1)
    interrupted_at: Optional[str] = None
    retryable: bool = True

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        normalized = (value or "").strip()
        if not normalized:
            raise ValueError("reason 不能为空")
        return normalized

    @field_validator("interrupted_at")
    @classmethod
    def validate_interrupted_at(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


def _normalize_config_id_list(values: List[str], field_name: str) -> List[str]:
    normalized: List[str] = []
    seen = set()
    for raw in values:
        value = str(raw or "").strip()
        if not value:
            continue
        if value in seen:
            raise ValueError(f"{field_name} 存在重复值: {value}")
        seen.add(value)
        normalized.append(value)
    return normalized


class ChatSessionConfigPayload(BaseModel):
    """聊天会话配置。"""

    model_config = ConfigDict(extra="forbid")

    uiMode: Literal["normal", "plan"]
    sourceType: Literal["home", "knowledge", "favorites"]
    kbIds: List[str] = Field(default_factory=list)
    docIds: List[str] = Field(default_factory=list)
    isKBLocked: bool = False
    modelName: Optional[str] = None

    @field_validator("kbIds")
    @classmethod
    def validate_kb_ids(cls, value: List[str]) -> List[str]:
        return _normalize_config_id_list(value, "kbIds")

    @field_validator("docIds")
    @classmethod
    def validate_doc_ids(cls, value: List[str]) -> List[str]:
        return _normalize_config_id_list(value, "docIds")

    @field_validator("modelName")
    @classmethod
    def validate_model_name(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class ChatSessionConfigUpdatePayload(BaseModel):
    """聊天会话配置的部分更新。"""

    model_config = ConfigDict(extra="forbid")

    uiMode: Literal["normal", "plan"]
    sourceType: Optional[Literal["home", "knowledge", "favorites"]] = None
    kbIds: Optional[List[str]] = None
    docIds: Optional[List[str]] = None
    isKBLocked: Optional[bool] = None
    modelName: Optional[str] = None

    @field_validator("kbIds")
    @classmethod
    def validate_update_kb_ids(cls, value: Optional[List[str]]) -> Optional[List[str]]:
        if value is None:
            return None
        return _normalize_config_id_list(value, "kbIds")

    @field_validator("docIds")
    @classmethod
    def validate_update_doc_ids(cls, value: Optional[List[str]]) -> Optional[List[str]]:
        if value is None:
            return None
        return _normalize_config_id_list(value, "docIds")

    @field_validator("modelName")
    @classmethod
    def validate_update_model_name(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class CreateSessionRequest(BaseModel):
    """创建会话请求"""
    first_message: str
    config: ChatSessionConfigPayload

    @field_validator("first_message")
    @classmethod
    def validate_first_message(cls, value: str) -> str:
        text = (value or "").strip()
        if not text:
            raise ValueError("first_message 不能为空")
        return text


class CreateEmptySessionRequest(BaseModel):
    """创建空会话请求。"""

    model_config = ConfigDict(extra="forbid")

    config: ChatSessionConfigPayload
    title: Optional[str] = None

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class AddMessageRequest(BaseModel):
    """添加消息请求"""
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    message_id: Optional[UUID] = None
    content: str
    thinking: Optional[str] = None  # AI 的思考过程
    document_summaries: Optional[list] = None  # 文档总结信息
    image_data_urls: List[str] = Field(default_factory=list)  # 图片 data URL / URL 列表（可选）
    artifacts: List[ChatArtifactPayload] = Field(default_factory=list)  # 产物附件元数据
    attachments: List[WorkspaceAttachmentInput] = Field(default_factory=list)  # 工作区附件元数据
    tool_traces: List[ChatToolTracePayload] = Field(default_factory=list)  # 工具执行轨迹
    assistant_tuple_messages: List[ChatTupleMessagePayload] = Field(default_factory=list)
    was_truncated: bool = False
    truncated_at: Optional[str] = None
    interruption: Optional[ChatInterruptionPayload] = None

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str) -> str:
        return (value or "").strip()

    @field_validator("thinking")
    @classmethod
    def validate_thinking(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        text = value.strip()
        return text or None

    @field_validator("image_data_urls")
    @classmethod
    def validate_image_data_urls(cls, value: List[str]) -> List[str]:
        if len(value) > 4:
            raise ValueError("一次最多上传 4 张图片")
        normalized: List[str] = []
        for raw in value:
            url = (raw or "").strip()
            if not url:
                continue
            lowered = url.lower()
            if lowered.startswith("http://") or lowered.startswith("https://"):
                normalized.append(url)
                continue
            if (
                lowered.startswith("data:image/jpeg;base64,")
                or lowered.startswith("data:image/jpg;base64,")
                or lowered.startswith("data:image/png;base64,")
                or lowered.startswith("data:image/webp;base64,")
            ):
                b64_part = url.split(",", 1)[1] if "," in url else ""
                estimated_bytes = (len(b64_part) * 3) // 4
                if estimated_bytes > _MAX_IMAGE_BYTES:
                    raise ValueError("单张图片大小不能超过 5MB")
                normalized.append(url)
                continue
            raise ValueError("image_data_urls 仅支持 JPG/JPEG/PNG/WEBP")
        return normalized

    @model_validator(mode="after")
    def validate_message_payload(self):
        if self.role == "user" and not self.content:
            raise ValueError("用户消息 content 不能为空")
        if (
            self.role == "assistant"
            and not self.content
            and not self.thinking
            and not self.artifacts
            and not self.attachments
            and not self.tool_traces
            and not self.assistant_tuple_messages
            and self.interruption is None
        ):
            raise ValueError(
                "assistant 消息至少需要 content、thinking、artifacts、attachments、tool_traces、assistant_tuple_messages 或 interruption"
            )
        return self


class UpdateConfigRequest(BaseModel):
    """更新会话配置请求"""
    config: ChatSessionConfigUpdatePayload  # 配置更新（部分更新）


@router.get("/sessions")
async def list_sessions(
    page: int = 1,
    page_size: int = 50,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """获取用户的所有聊天会话"""
    chat_service = _create_chat_service(db)
    
    sessions = await chat_service.list_sessions(current_user.id, page, page_size)
    session_ids = [session.id for session in sessions]
    stats_by_session = await chat_service.chat_repo.get_sessions_stats_bulk(session_ids)
    
    # 批量挂载统计信息
    sessions_with_stats = []
    for session in sessions:
        session_dict = session.to_dict(include_messages=False)
        stats = stats_by_session.get(session.id, {"messageCount": 0, "lastMessage": ""})
        session_dict.update(stats)
        sessions_with_stats.append(session_dict)
    
    return {
        "sessions": sessions_with_stats,
        "page": page,
        "pageSize": page_size
    }


@router.post("/sessions")
async def create_session(
    request: CreateSessionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """创建新的聊天会话"""
    chat_service = _create_chat_service(db)
    await _validate_session_model_name(
        db=db,
        current_user=current_user,
        model_name=request.config.modelName,
    )
    
    session = await chat_service.create_or_get_session(
        current_user.id, 
        request.first_message,
        config=request.config.model_dump(exclude_none=True)
    )
    
    return session.to_dict()


@router.post("/sessions/empty")
async def create_empty_session(
    request: CreateEmptySessionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """创建空聊天会话，供文件先上传后提问等场景使用。"""
    chat_service = _create_chat_service(db)
    await _validate_session_model_name(
        db=db,
        current_user=current_user,
        model_name=request.config.modelName,
    )

    session = await chat_service.create_empty_session(
        current_user.id,
        config=request.config.model_dump(exclude_none=True),
        title=request.title or "新对话",
    )
    return session.to_dict()


@router.get("/sessions/{session_id}")
async def get_session(
    session_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """获取聊天会话详情"""
    chat_service = _create_chat_service(db)
    
    session = await chat_service.get_session(session_id, current_user.id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    return session.to_dict()


@router.patch("/sessions/{session_id}/config")
async def update_session_config(
    session_id: UUID,
    request: UpdateConfigRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """更新会话配置（部分更新）"""
    chat_service = _create_chat_service(db)
    await _validate_session_model_name(
        db=db,
        current_user=current_user,
        model_name=request.config.modelName,
    )

    session = await chat_service.update_session_config(
        session_id,
        current_user.id,
        request.config.model_dump(exclude_none=True)
    )

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    return session.to_dict()


@router.delete("/sessions/all")
async def delete_all_sessions(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """删除用户的所有聊天会话"""
    chat_service = _create_chat_service(db)

    deleted_count = await chat_service.delete_all_sessions(current_user.id)

    return {"success": True, "deleted_count": deleted_count}


@router.delete("/sessions/{session_id}")
async def delete_session(
    session_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """删除聊天会话"""
    chat_service = _create_chat_service(db)

    success = await chat_service.delete_session(session_id, current_user.id)
    if not success:
        raise HTTPException(status_code=404, detail="Session not found")

    return {"success": True}


@router.get("/sessions/{session_id}/messages")
async def get_messages(
    session_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """获取会话的所有消息"""
    chat_service = _create_chat_service(db)
    chat_repo = chat_service.chat_repo
    session = await chat_repo.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Forbidden")

    messages = await chat_repo.get_session_messages(session_id)
    workspace_assets = []
    try:
        workspace_service = _create_workspace_service(
            session_id=str(session_id),
            user_id=str(current_user.id),
        )
        workspace_assets = list((await workspace_service.load_manifest()).assets)
    except Exception:
        workspace_assets = []

    message_payloads = _merge_message_attachments_with_workspace_assets(
        [msg.to_dict() for msg in messages],
        workspace_assets,
    )

    return {
        "messages": message_payloads,
    }


@router.post("/sessions/{session_id}/messages")
async def add_message(
    session_id: UUID,
    request: AddMessageRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """添加消息到会话"""
    chat_service = _create_chat_service(db)
    session = await chat_service.get_session(session_id, current_user.id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    validated_artifacts = None
    validated_attachments = None
    validated_tool_traces = None
    validated_assistant_tuple_messages = None
    validated_interruption = None
    if request.artifacts:
        validated_artifacts = []
        for item in request.artifacts:
            payload = item.model_dump(exclude_none=True)
            object_path = payload["object_path"]
            if not _is_allowed_session_object_path(session, current_user.id, session_id, object_path):
                raise HTTPException(status_code=403, detail="Forbidden artifact path")
            validated_artifacts.append(payload)
    if request.attachments:
        validated_attachments = []
        for item in request.attachments:
            payload = item.model_dump(exclude_none=True)
            object_path = payload.get("object_path")
            if object_path and not _is_allowed_session_object_path(session, current_user.id, session_id, object_path):
                raise HTTPException(status_code=403, detail="Forbidden attachment path")
            validated_attachments.append(payload)
    resolved_image_workspace_attachments: list[dict] = []
    if request.role == "user" and request.image_data_urls:
        workspace_service = _create_workspace_service(
            session_id=str(session_id),
            user_id=str(current_user.id),
        )
        try:
            resolved_assets = await workspace_service.resolve_request_assets(
                image_data_urls=request.image_data_urls,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        for asset in resolved_assets:
            payload = asset.to_metadata_payload()
            object_path = payload.get("object_path")
            if object_path and not _is_allowed_session_object_path(session, current_user.id, session_id, object_path):
                raise HTTPException(status_code=403, detail="Forbidden attachment path")
            resolved_image_workspace_attachments.append(payload)
    if resolved_image_workspace_attachments:
        validated_attachments = validated_attachments or []
        seen_attachment_keys = {
            (
                str(item.get("attachment_id", "")).strip(),
                str(item.get("object_path", "")).strip(),
                str(item.get("workspace_path", "")).strip(),
            )
            for item in validated_attachments
            if isinstance(item, dict)
        }
        for payload in resolved_image_workspace_attachments:
            key = (
                str(payload.get("attachment_id", "")).strip(),
                str(payload.get("object_path", "")).strip(),
                str(payload.get("workspace_path", "")).strip(),
            )
            if key in seen_attachment_keys:
                continue
            seen_attachment_keys.add(key)
            validated_attachments.append(payload)
    if request.tool_traces:
        validated_tool_traces = [
            item.model_dump(exclude_none=True)
            for item in request.tool_traces
        ]
    if request.assistant_tuple_messages:
        validated_assistant_tuple_messages = [
            item.model_dump(exclude_none=True)
            for item in request.assistant_tuple_messages
        ]
    if request.interruption is not None:
        validated_interruption = request.interruption.model_dump(exclude_none=True)

    message = await chat_service.add_message(
        session_id,
        current_user.id,
        request.role,
        request.content,
        request.message_id,
        request.thinking,
        request.document_summaries,
        request.image_data_urls,
        validated_artifacts,
        validated_attachments,
        validated_tool_traces,
        validated_assistant_tuple_messages,
        (
            {"was_truncated": request.was_truncated, "truncated_at": request.truncated_at}
            if request.was_truncated
            else None
        ),
        validated_interruption,
    )

    if not message:
        raise HTTPException(status_code=404, detail="Session not found")

    return message.to_dict()


@router.get("/sessions/{session_id}/artifacts/url")
async def get_session_artifact_url(
    session_id: UUID,
    object_path: str = Query(..., min_length=1),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取当前会话产物文件下载地址（带权限校验）。"""
    chat_service = _create_chat_service(db)

    session = await chat_service.get_session(session_id, current_user.id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    normalized_object_path = object_path.strip().lstrip("/")
    if not normalized_object_path:
        raise HTTPException(status_code=422, detail="object_path 不能为空")
    if ".." in normalized_object_path or "\\" in normalized_object_path:
        raise HTTPException(status_code=422, detail="object_path 包含非法路径字符")

    if not _is_allowed_session_object_path(session, current_user.id, session_id, normalized_object_path):
        raise HTTPException(status_code=403, detail="Forbidden artifact path")

    insight_artifact_url = _build_insight_artifact_url(session, normalized_object_path)
    if insight_artifact_url:
        file_name = normalized_object_path.rsplit("/", 1)[-1]
        return {
            "objectPath": normalized_object_path,
            "name": file_name,
            "url": insight_artifact_url,
            "expiresIn": _ARTIFACT_URL_EXPIRES_SECONDS,
        }

    get_file_url, object_exists = _get_minio_helpers()
    exists = await object_exists(normalized_object_path)
    if not exists:
        raise HTTPException(status_code=404, detail="Artifact not found")

    file_url = get_file_url(normalized_object_path, expires_seconds=_ARTIFACT_URL_EXPIRES_SECONDS)
    file_name = normalized_object_path.rsplit("/", 1)[-1]

    return {
        "objectPath": normalized_object_path,
        "name": file_name,
        "url": file_url,
        "expiresIn": _ARTIFACT_URL_EXPIRES_SECONDS,
    }


@router.get("/sessions/{session_id}/artifacts/download")
async def download_session_artifact(
    session_id: UUID,
    object_path: str = Query(..., min_length=1),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """代理下载当前会话产物文件，避免浏览器直接跨域下载失败。"""
    chat_service = _create_chat_service(db)

    session = await chat_service.get_session(session_id, current_user.id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    normalized_object_path = object_path.strip().lstrip("/")
    if not normalized_object_path:
        raise HTTPException(status_code=422, detail="object_path 不能为空")
    if ".." in normalized_object_path or "\\" in normalized_object_path:
        raise HTTPException(status_code=422, detail="object_path 包含非法路径字符")

    if not _is_allowed_session_object_path(session, current_user.id, session_id, normalized_object_path):
        raise HTTPException(status_code=403, detail="Forbidden artifact path")

    file_name = normalized_object_path.rsplit("/", 1)[-1] or "artifact"
    fallback_media_type = mimetypes.guess_type(file_name)[0] or "application/octet-stream"
    headers = _build_download_headers(file_name)

    internal_insight_artifact_url = _build_internal_insight_artifact_url(session, normalized_object_path)
    if internal_insight_artifact_url:
        insight_runtime_service = _get_insight_runtime_service()
        client = httpx.AsyncClient(timeout=insight_runtime_service.request_timeout_seconds)
        try:
            artifact_request = client.build_request("GET", internal_insight_artifact_url)
            artifact_response = await client.send(artifact_request, stream=True)
            artifact_response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            await exc.response.aclose()
            await client.aclose()
            if exc.response.status_code == 404:
                raise HTTPException(status_code=404, detail="Artifact not found") from exc
            raise HTTPException(status_code=502, detail="Failed to fetch runtime artifact") from exc
        except httpx.HTTPError as exc:
            await client.aclose()
            raise HTTPException(status_code=502, detail="Failed to fetch runtime artifact") from exc

        media_type = artifact_response.headers.get("content-type") or fallback_media_type
        response_headers = dict(headers)
        content_length = artifact_response.headers.get("content-length")
        if content_length:
            response_headers["Content-Length"] = content_length
        return StreamingResponse(
            artifact_response.aiter_bytes(),
            media_type=media_type,
            headers=response_headers,
            background=BackgroundTask(_close_httpx_stream, artifact_response, client),
        )

    _get_file_url, object_exists = _get_minio_helpers()
    exists = await object_exists(normalized_object_path)
    if not exists:
        raise HTTPException(status_code=404, detail="Artifact not found")

    from utils.minio_client import stream_file

    try:
        file_stream = stream_file(normalized_object_path)
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Failed to download artifact") from exc

    return StreamingResponse(
        file_stream,
        media_type=fallback_media_type,
        headers=headers,
    )


@router.delete("/sessions/{session_id}/messages/last-assistant")
async def delete_last_assistant_message(
    session_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """删除会话中最后一条 AI 回复"""
    chat_service = _create_chat_service(db)
    
    deleted_message_id = await chat_service.delete_last_assistant_message(
        session_id,
        current_user.id
    )
    
    # 如果返回 None，可能是会话不存在或没有 assistant 消息
    # 这里我们区分两种情况：会话不存在返回404，没有消息返回成功但 deleted_message_id 为 None
    if deleted_message_id is None:
        # 检查会话是否存在
        session = await chat_service.get_session(session_id, current_user.id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
    
    return {
        "success": True,
        "deleted_message_id": deleted_message_id
    }
