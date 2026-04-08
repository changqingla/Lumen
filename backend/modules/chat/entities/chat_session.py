"""
聊天会话模型
"""
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import Column, String, DateTime, ForeignKey, Text, Integer, ARRAY
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
import uuid

from config.database import Base

logger = logging.getLogger(__name__)


class ChatSession(Base):
    """聊天会话"""
    __tablename__ = "chat_sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    title = Column(String(200), nullable=False)  # 会话标题（通常是第一条消息的摘要）
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # 会话配置元数据
    config = Column(JSONB, nullable=True)  # 存储会话配置的 JSON 对象
    # config 结构示例:
    # {
    #   "uiMode": "normal",              # 交互模式: "normal" | "plan"
    #   "kbIds": ["uuid1", "uuid2"],      # 知识库ID列表
    #   "docIds": ["uuid3", "uuid4"],     # 文档ID列表
    #   "sourceType": "home",             # 来源: "home" | "knowledge" | "favorites"
    #   "isKBLocked": true,               # 知识库是否已锁定
    #   "modelName": "qwen3.5-flash",    # 当前会话选择的模型目录名
    #   "deepThinking": true              # 是否启用深度思考
    # }
    
    # 关系（使用 lazy loading）
    messages = relationship("ChatMessage", back_populates="session", cascade="all, delete-orphan", lazy="select")
    user = relationship("User", back_populates="chat_sessions", lazy="select")

    def to_dict(self, include_messages=False):
        """转换为字典"""
        result = {
            "id": str(self.id),
            "title": self.title,
            "lastMessage": "",
            "timestamp": self._format_timestamp(self.updated_at),
            "createdAt": self.created_at.isoformat(),
            "updatedAt": self.updated_at.isoformat(),
            "messageCount": 0,
            "config": self.config or {}  # 添加配置信息
        }
        
        # 只在明确需要时才访问关系属性
        if include_messages:
            try:
                messages_list = list(self.messages)
                if messages_list:
                    last_message = messages_list[-1]
                    result["lastMessage"] = last_message.content[:50]
                    result["messageCount"] = len(messages_list)
            except Exception as exc:
                logger.warning(
                    "Failed to load messages while serializing session %s: %s",
                    self.id,
                    exc,
                )
        
        return result
    
    @staticmethod
    def _format_timestamp(dt: datetime) -> str:
        """格式化时间戳为相对时间"""
        now = datetime.utcnow()
        diff = now - dt
        
        if diff.days > 7:
            return f"{diff.days} 天前"
        elif diff.days > 0:
            return f"{diff.days} 天前"
        elif diff.seconds >= 3600:
            hours = diff.seconds // 3600
            return f"{hours} 小时前"
        elif diff.seconds >= 60:
            minutes = diff.seconds // 60
            return f"{minutes} 分钟前"
        else:
            return "刚刚"


class ChatMessage(Base):
    """聊天消息"""
    __tablename__ = "chat_messages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(UUID(as_uuid=True), ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    role = Column(String(20), nullable=False)  # 'user' | 'assistant'
    content = Column(Text, nullable=False)
    thinking = Column(Text, nullable=True)  # AI 的思考过程
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    # 消息扩展信息（兼容历史结构）：
    # - 历史: list[document_summary]
    # - 新版: {"document_summaries": [...], "image_data_urls": [...], "artifacts": [...], "attachments": [...], "truncation": {...}}
    document_summaries = Column(JSONB, nullable=True)
    
    # 关系
    session = relationship("ChatSession", back_populates="messages")

    @staticmethod
    def _parse_message_metadata(
        raw: Any,
    ) -> Tuple[
        Optional[List[Dict[str, Any]]],
        List[str],
        Optional[Dict[str, Any]],
        List[Dict[str, Any]],
        List[Dict[str, Any]],
        List[Dict[str, Any]],
        List[Dict[str, Any]],
        Optional[Dict[str, Any]],
    ]:
        """兼容解析消息扩展信息。"""
        if raw is None:
            return None, [], None, [], [], [], [], None

        # 兼容历史结构：直接是文档总结列表
        if isinstance(raw, list):
            return raw, [], None, [], [], [], [], None

        if isinstance(raw, dict):
            doc_summaries = raw.get("document_summaries")
            image_data_urls = raw.get("image_data_urls")
            truncation = raw.get("truncation")
            artifacts = raw.get("artifacts")
            attachments = raw.get("attachments")
            tool_traces = raw.get("tool_traces")
            assistant_tuple_messages = raw.get("assistant_tuple_messages")
            interruption = raw.get("interruption")
            parsed_doc_summaries = doc_summaries if isinstance(doc_summaries, list) else None
            parsed_image_urls = (
                [str(url) for url in image_data_urls if isinstance(url, str)]
                if isinstance(image_data_urls, list)
                else []
            )
            parsed_truncation = None
            if isinstance(truncation, dict) and truncation.get("was_truncated"):
                parsed_truncation = {"was_truncated": True}
                truncated_at = truncation.get("truncated_at")
                if isinstance(truncated_at, str) and truncated_at.strip():
                    parsed_truncation["truncated_at"] = truncated_at.strip()
            parsed_artifacts: List[Dict[str, Any]] = []
            if isinstance(artifacts, list):
                for item in artifacts:
                    if not isinstance(item, dict):
                        continue
                    object_path = str(item.get("object_path", "")).strip()
                    if not object_path:
                        continue
                    artifact: Dict[str, Any] = {"object_path": object_path}
                    name = item.get("name")
                    if isinstance(name, str) and name.strip():
                        artifact["name"] = name.strip()
                    path = item.get("path")
                    if isinstance(path, str) and path.strip():
                        artifact["path"] = path.strip()
                    mime_type = item.get("mime_type")
                    if isinstance(mime_type, str) and mime_type.strip():
                        artifact["mime_type"] = mime_type.strip()
                    session_id = item.get("session_id")
                    if isinstance(session_id, str) and session_id.strip():
                        artifact["session_id"] = session_id.strip()
                    size_bytes = item.get("size_bytes")
                    try:
                        if size_bytes is not None:
                            resolved_size = int(size_bytes)
                            if resolved_size >= 0:
                                artifact["size_bytes"] = resolved_size
                    except (TypeError, ValueError):
                        pass
                    parsed_artifacts.append(artifact)
            parsed_attachments: List[Dict[str, Any]] = []
            if isinstance(attachments, list):
                for item in attachments:
                    if not isinstance(item, dict):
                        continue
                    attachment_id = str(item.get("attachment_id", "")).strip()
                    name = str(item.get("name", "")).strip()
                    object_path = str(item.get("object_path", "")).strip()
                    workspace_path = str(item.get("workspace_path", "")).strip()
                    if not attachment_id or not name or not object_path or not workspace_path:
                        continue
                    attachment: Dict[str, Any] = {
                        "attachment_id": attachment_id,
                        "name": name,
                        "object_path": object_path,
                        "workspace_path": workspace_path,
                    }
                    for key in (
                        "mime_type",
                        "source_kind",
                        "role",
                        "input_mode",
                        "sha256",
                        "created_at",
                        "parent_attachment_id",
                        "view_type",
                        "parse_status",
                    ):
                        value = item.get(key)
                        if isinstance(value, str) and value.strip():
                            attachment[key] = value.strip()
                    size_bytes = item.get("size_bytes")
                    try:
                        if size_bytes is not None:
                            resolved_size = int(size_bytes)
                            if resolved_size >= 0:
                                attachment["size_bytes"] = resolved_size
                    except (TypeError, ValueError):
                        pass
                    available_views = item.get("available_views")
                    if isinstance(available_views, list):
                        attachment["available_views"] = [
                            str(value).strip()
                            for value in available_views
                            if isinstance(value, str) and value.strip()
                        ]
                    capabilities = item.get("capabilities")
                    if isinstance(capabilities, list):
                        attachment["capabilities"] = [
                            str(value).strip()
                            for value in capabilities
                            if isinstance(value, str) and value.strip()
                        ]
                    metadata = item.get("metadata")
                    if isinstance(metadata, dict):
                        attachment["metadata"] = metadata
                    parsed_attachments.append(attachment)
            parsed_tool_traces: List[Dict[str, Any]] = []
            if isinstance(tool_traces, list):
                for item in tool_traces:
                    if not isinstance(item, dict):
                        continue
                    name = str(item.get("name", "")).strip()
                    if not name:
                        continue
                    trace: Dict[str, Any] = {"name": name}
                    call_id = item.get("call_id")
                    if isinstance(call_id, str) and call_id.strip():
                        trace["call_id"] = call_id.strip()
                    iteration = item.get("iteration")
                    try:
                        if iteration is not None:
                            resolved_iteration = int(iteration)
                            if resolved_iteration > 0:
                                trace["iteration"] = resolved_iteration
                    except (TypeError, ValueError):
                        pass
                    if "args" in item:
                        trace["args"] = item.get("args")
                    if "result" in item:
                        trace["result"] = item.get("result")
                    success = item.get("success")
                    if isinstance(success, bool):
                        trace["success"] = success
                    error = item.get("error")
                    if isinstance(error, str) and error.strip():
                        trace["error"] = error.strip()
                    status = item.get("status")
                    if isinstance(status, str) and status.strip():
                        trace["status"] = status.strip()
                    duration_ms = item.get("duration_ms")
                    try:
                        if duration_ms is not None:
                            resolved_duration = int(duration_ms)
                            if resolved_duration >= 0:
                                trace["duration_ms"] = resolved_duration
                    except (TypeError, ValueError):
                        pass
                    parsed_tool_traces.append(trace)
            parsed_assistant_tuple_messages: List[Dict[str, Any]] = []
            if isinstance(assistant_tuple_messages, list):
                for item in assistant_tuple_messages:
                    if not isinstance(item, dict):
                        continue
                    tuple_type = str(item.get("type", "")).strip()
                    if tuple_type not in {"ai", "tool"}:
                        continue
                    tuple_id = str(item.get("id", "")).strip()
                    if not tuple_id:
                        continue
                    tuple_message: Dict[str, Any] = {
                        "type": tuple_type,
                        "id": tuple_id,
                    }

                    if "content" in item and isinstance(item.get("content"), str):
                        tuple_message["content"] = item.get("content")

                    raw_tool_calls = item.get("tool_calls")
                    normalized_tool_calls: List[Dict[str, Any]] = []
                    if isinstance(raw_tool_calls, list):
                        for raw_call in raw_tool_calls:
                            if not isinstance(raw_call, dict):
                                continue
                            tool_name = str(raw_call.get("name", "")).strip()
                            if not tool_name:
                                continue
                            tool_call: Dict[str, Any] = {"name": tool_name}
                            call_id = raw_call.get("id")
                            if isinstance(call_id, str) and call_id.strip():
                                tool_call["id"] = call_id.strip()
                            if "args" in raw_call:
                                tool_call["args"] = raw_call.get("args")
                            normalized_tool_calls.append(tool_call)
                    if normalized_tool_calls:
                        tuple_message["tool_calls"] = normalized_tool_calls

                    tool_call_id = item.get("tool_call_id")
                    if isinstance(tool_call_id, str) and tool_call_id.strip():
                        tuple_message["tool_call_id"] = tool_call_id.strip()
                    tool_name = item.get("name")
                    if isinstance(tool_name, str) and tool_name.strip():
                        tuple_message["name"] = tool_name.strip()

                    if tuple_type == "ai":
                        has_content = bool(str(tuple_message.get("content", "")).strip())
                        has_tool_calls = bool(tuple_message.get("tool_calls"))
                        if not has_content and not has_tool_calls:
                            continue
                    if tuple_type == "tool":
                        if not tuple_message.get("tool_call_id") or not tuple_message.get("name"):
                            continue

                    parsed_assistant_tuple_messages.append(tuple_message)

            parsed_interruption = None
            if isinstance(interruption, dict):
                reason = str(interruption.get("reason", "")).strip()
                if reason:
                    parsed_interruption = {
                        "reason": reason,
                        "retryable": bool(interruption.get("retryable", True)),
                    }
                    interrupted_at = interruption.get("interrupted_at")
                    if isinstance(interrupted_at, str) and interrupted_at.strip():
                        parsed_interruption["interrupted_at"] = interrupted_at.strip()

            return (
                parsed_doc_summaries,
                parsed_image_urls,
                parsed_truncation,
                parsed_artifacts,
                parsed_attachments,
                parsed_tool_traces,
                parsed_assistant_tuple_messages,
                parsed_interruption,
            )

        return None, [], None, [], [], [], [], None

    def to_dict(self):
        """转换为字典"""
        (
            document_summaries,
            image_data_urls,
            truncation,
            artifacts,
            attachments,
            tool_traces,
            assistant_tuple_messages,
            interruption,
        ) = self._parse_message_metadata(
            self.document_summaries,
        )
        return {
            "id": str(self.id),
            "role": self.role,
            "content": self.content,
            "thinking": self.thinking,
            "createdAt": self.created_at.isoformat(),
            "documentSummaries": document_summaries,
            "imageDataUrls": image_data_urls,
            "artifacts": artifacts,
            "attachments": attachments,
            "toolTraces": tool_traces,
            "assistantTupleMessages": assistant_tuple_messages,
            "wasTruncated": bool(truncation and truncation.get("was_truncated")),
            "truncatedAt": truncation.get("truncated_at") if truncation else None,
            "interruption": {
                "reason": interruption.get("reason"),
                "interruptedAt": interruption.get("interrupted_at"),
                "retryable": interruption.get("retryable", True),
            } if interruption else None,
        }
