"""lumen runtime client primitives."""

from __future__ import annotations

import re
from typing import Any, BinaryIO, Optional
from urllib.parse import quote

import httpx

from config.settings import settings


_SAFE_THREAD_ID_RE = re.compile(r"^[A-Za-z0-9_\-]+$")


def _normalize_public_base_url(value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized or normalized == "/":
        return ""
    return normalized.rstrip("/")


class InsightRuntimeService:
    """Minimal client wrapper around lumen Gateway and LangGraph APIs."""

    def __init__(self) -> None:
        self.gateway_url = settings.INSIGHT_GATEWAY_URL.rstrip("/")
        self.langgraph_url = settings.INSIGHT_LANGGRAPH_URL.rstrip("/")
        self.gateway_public_base_url = _normalize_public_base_url(
            settings.INSIGHT_GATEWAY_PUBLIC_BASE_URL
        )
        self.langgraph_public_base_url = _normalize_public_base_url(
            settings.INSIGHT_LANGGRAPH_PUBLIC_BASE_URL
        )
        self.assistant_id = settings.INSIGHT_ASSISTANT_ID
        self.on_disconnect = settings.INSIGHT_ON_DISCONNECT
        self.request_timeout_seconds = settings.INSIGHT_REQUEST_TIMEOUT_SECONDS
        self.recursion_limit = settings.INSIGHT_RECURSION_LIMIT

    @staticmethod
    def build_thread_id(session_id: str) -> str:
        thread_id = str(session_id or "").strip()
        if not thread_id:
            raise ValueError("thread_id 不能为空")
        if not _SAFE_THREAD_ID_RE.match(thread_id):
            raise ValueError(
                "thread_id 非法：仅允许字母、数字、下划线和短横线"
            )
        return thread_id

    def build_run_stream_path(self, thread_id: str) -> str:
        normalized = self.build_thread_id(thread_id)
        return f"/threads/{normalized}/runs/stream"

    def build_thread_uploads_path(self, thread_id: str) -> str:
        normalized = self.build_thread_id(thread_id)
        return f"/api/threads/{normalized}/uploads"

    def build_thread_uploads_list_path(self, thread_id: str) -> str:
        normalized = self.build_thread_id(thread_id)
        return f"/api/threads/{normalized}/uploads/list"

    def build_thread_artifacts_base_path(self, thread_id: str) -> str:
        normalized = self.build_thread_id(thread_id)
        return f"/api/threads/{normalized}/artifacts"

    def build_thread_suggestions_path(self, thread_id: str) -> str:
        normalized = self.build_thread_id(thread_id)
        return f"/api/threads/{normalized}/suggestions"

    def build_cancel_run_path(self, thread_id: str, run_id: str) -> str:
        normalized_thread_id = self.build_thread_id(thread_id)
        normalized_run_id = str(run_id or "").strip()
        if not normalized_run_id:
            raise ValueError("run_id 不能为空")
        return (
            f"/threads/{normalized_thread_id}/runs/"
            f"{normalized_run_id}/cancel?action=interrupt&wait=0"
        )

    def build_run_request_template(
        self,
        *,
        thread_id: str,
        assistant_id: Optional[str] = None,
        model_name: str,
        thinking_enabled: bool,
        is_plan_mode: bool,
        subagent_enabled: bool = False,
    ) -> dict[str, Any]:
        normalized_thread_id = self.build_thread_id(thread_id)
        return {
            "assistant_id": str(assistant_id or self.assistant_id).strip(),
            "on_disconnect": self.on_disconnect,
            # Different sessions should run concurrently, but the same thread
            # should fail fast instead of silently queueing behind an active run.
            "multitask_strategy": "reject",
            "stream_mode": ["messages", "values", "custom"],
            "context": {
                "thread_id": normalized_thread_id,
                "model_name": model_name,
                "thinking_enabled": thinking_enabled,
                "is_plan_mode": is_plan_mode,
                "subagent_enabled": subagent_enabled,
            },
            "config": {
                "recursion_limit": self.recursion_limit,
            },
            "input": {
                "messages": [],
            },
        }

    async def ensure_thread_exists(self, thread_id: str) -> dict[str, Any]:
        normalized_thread_id = self.build_thread_id(thread_id)
        url = f"{self.langgraph_url}/threads"
        payload = {
            "thread_id": normalized_thread_id,
            "if_exists": "do_nothing",
        }
        async with httpx.AsyncClient(timeout=self.request_timeout_seconds) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
            return data if isinstance(data, dict) else {}

    async def resolve_assistant_id(self) -> str:
        configured_assistant = str(self.assistant_id or "").strip()
        if not configured_assistant:
            raise ValueError("Insight assistant_id 未配置")

        search_url = f"{self.langgraph_url}/assistants/search"
        search_payload = {
            "graph_id": configured_assistant,
            "limit": 10,
        }
        async with httpx.AsyncClient(timeout=self.request_timeout_seconds) as client:
            response = await client.post(search_url, json=search_payload)
            response.raise_for_status()
            data = response.json()
            assistants = data if isinstance(data, list) else []

            preferred_assistant_id: Optional[str] = None
            fallback_assistant_id: Optional[str] = None
            for item in assistants:
                if not isinstance(item, dict):
                    continue
                assistant_id = str(item.get("assistant_id") or "").strip()
                if not assistant_id:
                    continue
                if fallback_assistant_id is None:
                    fallback_assistant_id = assistant_id

                name = str(item.get("name") or "").strip()
                metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
                created_by = str(metadata.get("created_by") or "").strip().lower()
                if name == configured_assistant or created_by == "system":
                    preferred_assistant_id = assistant_id
                    break

            if preferred_assistant_id:
                return preferred_assistant_id
            if fallback_assistant_id:
                return fallback_assistant_id

            create_response = await client.post(
                f"{self.langgraph_url}/assistants",
                json={
                    "graph_id": configured_assistant,
                    "config": {},
                    "metadata": {"source": "lumen"},
                },
            )
            create_response.raise_for_status()
            created = create_response.json()
            created_assistant_id = (
                str(created.get("assistant_id") or "").strip()
                if isinstance(created, dict)
                else ""
            )
            if not created_assistant_id:
                raise ValueError("LangGraph assistant 创建成功但未返回 assistant_id")
            return created_assistant_id

    async def list_runtime_models(self) -> list[dict[str, Any]]:
        url = f"{self.gateway_url}/api/models"
        async with httpx.AsyncClient(timeout=self.request_timeout_seconds) as client:
            response = await client.get(url)
            response.raise_for_status()
            payload = response.json()

        models_payload = payload.get("models") if isinstance(payload, dict) else []
        if not isinstance(models_payload, list):
            return []
        return [item for item in models_payload if isinstance(item, dict)]

    async def resolve_runtime_model_name(self, requested_model_name: Optional[str]) -> str:
        normalized_requested = str(requested_model_name or "").strip()
        models_payload = await self.list_runtime_models()
        available_names = [
            str(item.get("name") or "").strip()
            for item in models_payload
            if isinstance(item, dict) and str(item.get("name") or "").strip()
        ]
        if not available_names:
            if normalized_requested:
                return normalized_requested
            raise ValueError("Insight runtime 未配置任何可用模型")

        if normalized_requested and normalized_requested in available_names:
            return normalized_requested
        if normalized_requested:
            raise ValueError(
                f"Insight runtime 未配置模型: {normalized_requested}。"
                f" 当前仅支持: {', '.join(available_names)}"
            )
        return available_names[0]

    async def list_thread_uploads(self, thread_id: str) -> list[dict[str, Any]]:
        normalized_thread_id = self.build_thread_id(thread_id)
        url = f"{self.gateway_url}{self.build_thread_uploads_list_path(normalized_thread_id)}"
        async with httpx.AsyncClient(timeout=self.request_timeout_seconds) as client:
            response = await client.get(url)
            if response.status_code == 404:
                return []
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                return []
            files = payload.get("files")
            if not isinstance(files, list):
                return []
            return [item for item in files if isinstance(item, dict)]

    async def upload_bytes(
        self,
        *,
        thread_id: str,
        filename: str,
        data: bytes,
        content_type: Optional[str] = None,
    ) -> dict[str, Any]:
        normalized_thread_id = self.build_thread_id(thread_id)
        safe_filename = str(filename or "").strip()
        if not safe_filename:
            raise ValueError("filename 不能为空")

        url = f"{self.gateway_url}{self.build_thread_uploads_path(normalized_thread_id)}"
        files = {
            "files": (safe_filename, data, content_type or "application/octet-stream"),
        }
        async with httpx.AsyncClient(timeout=self.request_timeout_seconds) as client:
            response = await client.post(url, files=files)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("Insight uploads API 返回格式非法")
            uploaded_files = payload.get("files")
            if not isinstance(uploaded_files, list) or not uploaded_files:
                raise ValueError("Insight uploads API 未返回文件信息")
            first_file = uploaded_files[0]
            if not isinstance(first_file, dict):
                raise ValueError("Insight uploads API 文件信息格式非法")
            return first_file

    async def upload_file_object(
        self,
        *,
        thread_id: str,
        filename: str,
        file_object: BinaryIO,
        content_type: Optional[str] = None,
    ) -> dict[str, Any]:
        normalized_thread_id = self.build_thread_id(thread_id)
        safe_filename = str(filename or "").strip()
        if not safe_filename:
            raise ValueError("filename 不能为空")

        url = f"{self.gateway_url}{self.build_thread_uploads_path(normalized_thread_id)}"
        files = {
            "files": (safe_filename, file_object, content_type or "application/octet-stream"),
        }
        async with httpx.AsyncClient(timeout=self.request_timeout_seconds) as client:
            response = await client.post(url, files=files)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("Insight uploads API 返回格式非法")
            uploaded_files = payload.get("files")
            if not isinstance(uploaded_files, list) or not uploaded_files:
                raise ValueError("Insight uploads API 未返回文件信息")
            first_file = uploaded_files[0]
            if not isinstance(first_file, dict):
                raise ValueError("Insight uploads API 文件信息格式非法")
            return first_file

    async def delete_thread_upload(self, thread_id: str, filename: str) -> dict[str, Any]:
        normalized_thread_id = self.build_thread_id(thread_id)
        normalized_filename = str(filename or "").strip()
        if not normalized_filename:
            raise ValueError("filename 不能为空")

        encoded_filename = quote(normalized_filename, safe="")
        url = f"{self.gateway_url}{self.build_thread_uploads_path(normalized_thread_id)}/{encoded_filename}"
        async with httpx.AsyncClient(timeout=self.request_timeout_seconds) as client:
            response = await client.delete(url)
            response.raise_for_status()
            payload = response.json()
            return payload if isinstance(payload, dict) else {}


insight_runtime_service = InsightRuntimeService()
