"""lumen runtime client primitives."""

from __future__ import annotations

import re
from typing import Any, BinaryIO, Optional
from urllib.parse import quote

import httpx

from config.settings import settings


_SAFE_THREAD_ID_RE = re.compile(r"^[A-Za-z0-9_\-]+$")
_UUID_FILENAME_COMPONENT = (
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_MANAGED_KB_FILENAME_RE = re.compile(
    rf"^kb__{_UUID_FILENAME_COMPONENT}__{_UUID_FILENAME_COMPONENT}__"
    r"(?:[0-9a-f]{16}__)?[A-Za-z0-9._-]+\.md$"
)
_GATEWAY_INTERNAL_TOKEN_HEADER = "X-Gateway-Internal-Token"


class InsightRuntimeService:
    """Minimal client wrapper around lumen Gateway and LangGraph APIs."""

    def __init__(self) -> None:
        self.gateway_url = settings.INSIGHT_GATEWAY_URL.rstrip("/")
        self.langgraph_url = settings.INSIGHT_LANGGRAPH_URL.rstrip("/")
        self._gateway_internal_api_token = (
            settings.GATEWAY_INTERNAL_API_TOKEN.get_secret_value().strip()
        )
        self.assistant_id = settings.INSIGHT_ASSISTANT_ID
        self.on_disconnect = settings.INSIGHT_ON_DISCONNECT
        self.request_timeout_seconds = settings.INSIGHT_REQUEST_TIMEOUT_SECONDS
        self.recursion_limit = settings.INSIGHT_RECURSION_LIMIT

    def gateway_request_headers(self) -> dict[str, str]:
        """Return headers for Gateway-only calls, failing before network I/O."""
        token = self._gateway_internal_api_token
        if (
            len(token) < 32
            or not token.isascii()
            or token.lower().startswith(("change-me", "replace-with-"))
        ):
            raise RuntimeError("GATEWAY_INTERNAL_API_TOKEN is not configured correctly")
        return {_GATEWAY_INTERNAL_TOKEN_HEADER: token}

    def _create_gateway_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=self.request_timeout_seconds,
            headers=self.gateway_request_headers(),
            follow_redirects=False,
            trust_env=False,
        )

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

    def build_thread_upload_metadata_path(self, thread_id: str) -> str:
        normalized = self.build_thread_id(thread_id)
        return f"/api/threads/{normalized}/uploads/metadata"

    def build_thread_artifacts_base_path(self, thread_id: str) -> str:
        normalized = self.build_thread_id(thread_id)
        return f"/api/threads/{normalized}/artifacts"

    def build_run_request_template(
        self,
        *,
        thread_id: str,
        assistant_id: Optional[str] = None,
        model_name: str,
        thinking_enabled: bool,
        is_plan_mode: bool,
        subagent_enabled: bool = False,
        disable_model_streaming: bool = False,
        recursion_limit: Optional[int] = None,
    ) -> dict[str, Any]:
        normalized_thread_id = self.build_thread_id(thread_id)
        resolved_recursion_limit = int(
            self.recursion_limit if recursion_limit is None else recursion_limit
        )
        if resolved_recursion_limit < 1:
            raise ValueError("recursion_limit 必须大于 0")
        return {
            "assistant_id": str(assistant_id or self.assistant_id).strip(),
            "on_disconnect": self.on_disconnect,
            # Different sessions should run concurrently, but the same thread
            # should fail fast instead of silently queueing behind an active run.
            "multitask_strategy": "reject",
            "stream_mode": ["messages-tuple", "values", "custom"],
            "context": {
                "thread_id": normalized_thread_id,
                "model_name": model_name,
                "thinking_enabled": thinking_enabled,
                "is_plan_mode": is_plan_mode,
                "subagent_enabled": subagent_enabled,
                "disable_model_streaming": bool(disable_model_streaming),
            },
            "config": {
                "recursion_limit": resolved_recursion_limit,
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
        async with httpx.AsyncClient(
            timeout=self.request_timeout_seconds,
            follow_redirects=False,
            trust_env=False,
        ) as client:
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
        async with httpx.AsyncClient(
            timeout=self.request_timeout_seconds,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            response = await client.post(search_url, json=search_payload)
            response.raise_for_status()
            data = response.json()
            assistants = data if isinstance(data, list) else []

            for item in assistants:
                if not isinstance(item, dict):
                    continue
                assistant_id = str(item.get("assistant_id") or "").strip()
                if not assistant_id:
                    continue

                name = str(item.get("name") or "").strip()
                metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
                created_by = str(metadata.get("created_by") or "").strip().lower()
                if name == configured_assistant or created_by == "system":
                    return assistant_id

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
        async with self._create_gateway_client() as client:
            response = await client.get(url)
            response.raise_for_status()
            payload = response.json()

        models_payload = payload.get("models") if isinstance(payload, dict) else []
        if not isinstance(models_payload, list):
            return []
        return [item for item in models_payload if isinstance(item, dict)]

    async def list_thread_uploads(self, thread_id: str) -> list[dict[str, Any]]:
        normalized_thread_id = self.build_thread_id(thread_id)
        url = f"{self.gateway_url}{self.build_thread_uploads_list_path(normalized_thread_id)}"
        async with self._create_gateway_client() as client:
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

    async def has_active_thread_run(self, thread_id: str) -> bool:
        """Return whether LangGraph has a running or pending run for the thread."""
        normalized_thread_id = self.build_thread_id(thread_id)
        url = f"{self.langgraph_url}/threads/{normalized_thread_id}/runs"
        async with httpx.AsyncClient(
            timeout=self.request_timeout_seconds,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            for run_status in ("running", "pending"):
                response = await client.get(
                    url,
                    params={"limit": 1, "status": run_status},
                )
                response.raise_for_status()
                payload = response.json()
                if isinstance(payload, list) and payload:
                    return True
                if (
                    isinstance(payload, dict)
                    and isinstance(payload.get("runs"), list)
                    and payload["runs"]
                ):
                    return True
        return False

    async def get_thread_upload_integrity(
        self,
        thread_id: str,
        filename: str,
    ) -> dict[str, Any]:
        """Fetch a managed Runtime upload's streamed content metadata."""
        normalized_thread_id = self.build_thread_id(thread_id)
        normalized_filename = str(filename or "").strip()
        if (
            normalized_filename != filename
            or _MANAGED_KB_FILENAME_RE.fullmatch(normalized_filename) is None
        ):
            raise ValueError("filename 不是合法的受管知识文件名")

        url = (
            f"{self.gateway_url}"
            f"{self.build_thread_upload_metadata_path(normalized_thread_id)}"
        )
        async with self._create_gateway_client() as client:
            response = await client.get(url, params={"filename": normalized_filename})
            response.raise_for_status()
            payload = response.json()

        if not isinstance(payload, dict):
            raise ValueError("Insight upload metadata API 返回格式非法")
        returned_filename = payload.get("filename")
        size = payload.get("size")
        sha256 = payload.get("sha256")
        if returned_filename != normalized_filename:
            raise ValueError("Insight upload metadata API 返回了错误的文件名")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise ValueError("Insight upload metadata API 返回了非法的文件大小")
        if not isinstance(sha256, str) or re.fullmatch(r"[0-9a-f]{64}", sha256) is None:
            raise ValueError("Insight upload metadata API 返回了非法的 SHA-256")
        return {
            "filename": returned_filename,
            "size": size,
            "sha256": sha256,
        }

    async def download_thread_artifact_bytes(self, thread_id: str, virtual_path: str) -> bytes:
        normalized_thread_id = self.build_thread_id(thread_id)
        normalized_path = str(virtual_path or "").strip().lstrip("/")
        if not normalized_path:
            raise ValueError("artifact path 不能为空")
        if "\\" in normalized_path or ".." in normalized_path.split("/"):
            raise ValueError("artifact path 非法")

        encoded_path = quote(normalized_path, safe="/")
        url = f"{self.gateway_url}{self.build_thread_artifacts_base_path(normalized_thread_id)}/{encoded_path}?download=true"
        async with self._create_gateway_client() as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.content

    async def download_thread_artifact_text(self, thread_id: str, virtual_path: str) -> str:
        content = await self.download_thread_artifact_bytes(thread_id, virtual_path)
        return content.decode("utf-8")

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
        async with self._create_gateway_client() as client:
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
        async with self._create_gateway_client() as client:
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

    async def delete_thread_upload(
        self,
        thread_id: str,
        filename: str,
        companion_filename: str | None = None,
    ) -> dict[str, Any]:
        normalized_thread_id = self.build_thread_id(thread_id)
        normalized_filename = str(filename or "").strip()
        if not normalized_filename:
            raise ValueError("filename 不能为空")

        encoded_filename = quote(normalized_filename, safe="")
        url = f"{self.gateway_url}{self.build_thread_uploads_path(normalized_thread_id)}/{encoded_filename}"
        params = None
        if companion_filename:
            normalized_companion = str(companion_filename).strip()
            if not normalized_companion:
                raise ValueError("companion_filename 不能为空")
            params = {"companion_filename": normalized_companion}
        async with self._create_gateway_client() as client:
            response = await client.delete(url, params=params)
            response.raise_for_status()
            payload = response.json()
            return payload if isinstance(payload, dict) else {}


insight_runtime_service = InsightRuntimeService()
