"""通道管理器（ChannelManager）：消费入站消息并通过 LangGraph Server 分发给 lumen。"""

from __future__ import annotations

import asyncio
import logging
import mimetypes
from collections.abc import Mapping
from typing import Any

from src.channels.message_bus import InboundMessage, InboundMessageType, MessageBus, OutboundMessage, ResolvedAttachment
from src.channels.store import ChannelStore

logger = logging.getLogger(__name__)

DEFAULT_LANGGRAPH_URL = "http://localhost:2024"
DEFAULT_GATEWAY_URL = "http://localhost:8001"
DEFAULT_ASSISTANT_ID = "lead_agent"

DEFAULT_RUN_CONFIG: dict[str, Any] = {"recursion_limit": 100}
DEFAULT_RUN_CONTEXT: dict[str, Any] = {
    "thinking_enabled": True,
    "is_plan_mode": False,
    "subagent_enabled": False,
}


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _merge_dicts(*layers: Any) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for layer in layers:
        if isinstance(layer, Mapping):
            merged.update(layer)
    return merged


def _extract_response_text(result: dict | list) -> str:
    """
    ``runs.wait`` 会返回最终状态字典，其中包含 ``messages`` 列表。
    每条消息至少包含 ``type`` 与 ``content`` 字段。

    处理的特殊情况：
    - 常规 AI 文本回复
    - 澄清中断（``ask_clarification`` 工具消息）
    - 带 tool_calls 但无文本内容的 AI 消息
    """
    if isinstance(result, list):
        messages = result
    elif isinstance(result, dict):
        messages = result.get("messages", [])
    else:
        return ""

    # 倒序查找可用回复文本，但在最后一条 human 消息处停止，
    # 以免返回上一轮的文本内容。
    for msg in reversed(messages):
        if not isinstance(msg, dict):
            continue

        msg_type = msg.get("type")

        # 在最后一条 human 消息处停止，之前内容属于上一轮
        if msg_type == "human":
            break

        # 检查 ask_clarification 的工具消息（中断场景）
        if msg_type == "tool" and msg.get("name") == "ask_clarification":
            content = msg.get("content", "")
            if isinstance(content, str) and content:
                return content

        # 常规 AI 文本消息
        if msg_type == "ai":
            content = msg.get("content", "")
            if isinstance(content, str) and content:
                return content
            # 消息内容（content）也可能是内容块列表
            if isinstance(content, list):
                parts = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        parts.append(block.get("text", ""))
                    elif isinstance(block, str):
                        parts.append(block)
                text = "".join(parts)
                if text:
                    return text
    return ""


def _extract_artifacts(result: dict | list) -> list[str]:
    """
    不直接读取全量 ``artifacts`` 状态（该状态包含线程历史所有产物），
    而是检查最后一条 human 消息之后的消息，收集 ``present_files`` 工具调用中的文件路径。
    这样可确保只返回本轮新生成的产物。

    """
    if isinstance(result, list):
        messages = result
    elif isinstance(result, dict):
        messages = result.get("messages", [])
    else:
        return []

    artifacts: list[str] = []
    for msg in reversed(messages):
        if not isinstance(msg, dict):
            continue
        # 在最后一条 human 消息处停止，之前内容属于上一轮
        if msg.get("type") == "human":
            break
        # 查找包含 present_files 工具调用的 AI 消息
        if msg.get("type") == "ai":
            for tc in msg.get("tool_calls", []):
                if isinstance(tc, dict) and tc.get("name") == "present_files":
                    args = tc.get("args", {})
                    paths = args.get("filepaths", [])
                    if isinstance(paths, list):
                        artifacts.extend(p for p in paths if isinstance(p, str))
    return artifacts


def _format_artifact_text(artifacts: list[str]) -> str:
    """将产物路径格式化为可读文本，仅展示文件名列表。"""
    import posixpath

    filenames = [posixpath.basename(p) for p in artifacts]
    if len(filenames) == 1:
        return f"Created File: 📎 {filenames[0]}"
    return "Created Files: 📎 " + "、".join(filenames)


_OUTPUTS_VIRTUAL_PREFIX = "/mnt/user-data/outputs/"


def _resolve_attachments(thread_id: str, artifacts: list[str]) -> list[ResolvedAttachment]:
    """
    仅接受 ``/mnt/user-data/outputs/`` 下的路径；其他虚拟路径会被告警并拒绝，
    以防通过 IM 通道外泄 uploads 或 workspace 文件。

    对无法解析的产物（文件缺失、路径非法）会跳过并记录告警。
    """
    from src.config.paths import get_paths

    attachments: list[ResolvedAttachment] = []
    paths = get_paths()
    outputs_dir = paths.sandbox_outputs_dir(thread_id).resolve()
    for virtual_path in artifacts:
        # 安全限制：只允许 Agent outputs 目录下的文件
        if not virtual_path.startswith(_OUTPUTS_VIRTUAL_PREFIX):
            logger.warning("[Manager] rejected non-outputs artifact path: %s", virtual_path)
            continue
        try:
            actual = paths.resolve_virtual_path(thread_id, virtual_path)
            # 校验解析后的路径确实在 outputs 目录内
            # （防止即使通过前缀检查仍发生路径穿越）
            try:
                actual.resolve().relative_to(outputs_dir)
            except ValueError:
                logger.warning("[Manager] artifact path escapes outputs dir: %s -> %s", virtual_path, actual)
                continue
            if not actual.is_file():
                logger.warning("[Manager] artifact not found on disk: %s -> %s", virtual_path, actual)
                continue
            mime, _ = mimetypes.guess_type(str(actual))
            mime = mime or "application/octet-stream"
            attachments.append(ResolvedAttachment(
                virtual_path=virtual_path,
                actual_path=actual,
                filename=actual.name,
                mime_type=mime,
                size=actual.stat().st_size,
                is_image=mime.startswith("image/"),
            ))
        except (ValueError, OSError) as exc:
            logger.warning("[Manager] failed to resolve artifact %s: %s", virtual_path, exc)
    return attachments


class ChannelManager:
    """
    从 MessageBus 入站队列读取消息，在 LangGraph Server 创建/复用线程，
    通过 ``runs.wait`` 发送消息，并将出站响应重新发布到总线。

    """

    def __init__(
        self,
        bus: MessageBus,
        store: ChannelStore,
        *,
        max_concurrency: int = 5,
        langgraph_url: str = DEFAULT_LANGGRAPH_URL,
        gateway_url: str = DEFAULT_GATEWAY_URL,
        assistant_id: str = DEFAULT_ASSISTANT_ID,
        default_session: dict[str, Any] | None = None,
        channel_sessions: dict[str, Any] | None = None,
    ) -> None:
        self.bus = bus
        self.store = store
        self._max_concurrency = max_concurrency
        self._langgraph_url = langgraph_url
        self._gateway_url = gateway_url
        self._assistant_id = assistant_id
        self._default_session = _as_dict(default_session)
        self._channel_sessions = dict(channel_sessions or {})
        self._client = None  # 延迟初始化：langgraph_sdk 异步客户端
        self._semaphore: asyncio.Semaphore | None = None
        self._running = False
        self._task: asyncio.Task | None = None

    def _resolve_session_layer(self, msg: InboundMessage) -> tuple[dict[str, Any], dict[str, Any]]:
        channel_layer = _as_dict(self._channel_sessions.get(msg.channel_name))
        users_layer = _as_dict(channel_layer.get("users"))
        user_layer = _as_dict(users_layer.get(msg.user_id))
        return channel_layer, user_layer

    def _resolve_run_params(self, msg: InboundMessage, thread_id: str) -> tuple[str, dict[str, Any], dict[str, Any]]:
        channel_layer, user_layer = self._resolve_session_layer(msg)

        assistant_id = (
            user_layer.get("assistant_id")
            or channel_layer.get("assistant_id")
            or self._default_session.get("assistant_id")
            or self._assistant_id
        )
        if not isinstance(assistant_id, str) or not assistant_id.strip():
            assistant_id = self._assistant_id

        run_config = _merge_dicts(
            DEFAULT_RUN_CONFIG,
            self._default_session.get("config"),
            channel_layer.get("config"),
            user_layer.get("config"),
        )

        run_context = _merge_dicts(
            DEFAULT_RUN_CONTEXT,
            self._default_session.get("context"),
            channel_layer.get("context"),
            user_layer.get("context"),
            {"thread_id": thread_id},
        )

        return assistant_id, run_config, run_context

    # -- LangGraph SDK 客户端（延迟初始化） ----------------------------------

    def _get_client(self):
        """返回 ``langgraph_sdk`` 异步客户端，首次调用时创建。"""
        if self._client is None:
            from langgraph_sdk import get_client

            self._client = get_client(url=self._langgraph_url)
        return self._client

    # -- 生命周期 ------------------------------------------------------------

    async def start(self) -> None:
        """启动分发循环。"""
        if self._running:
            return
        self._running = True
        self._semaphore = asyncio.Semaphore(self._max_concurrency)
        self._task = asyncio.create_task(self._dispatch_loop())
        logger.info("ChannelManager started (max_concurrency=%d)", self._max_concurrency)

    async def stop(self) -> None:
        """停止分发循环。"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("ChannelManager stopped")

    # -- 分发循环 ------------------------------------------------------------

    async def _dispatch_loop(self) -> None:
        logger.info("[Manager] dispatch loop started, waiting for inbound messages")
        while self._running:
            try:
                msg = await asyncio.wait_for(self.bus.get_inbound(), timeout=1.0)
            except TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            logger.info(
                "[Manager] received inbound: channel=%s, chat_id=%s, type=%s, text=%r",
                msg.channel_name,
                msg.chat_id,
                msg.msg_type.value,
                msg.text[:100] if msg.text else "",
            )
            task = asyncio.create_task(self._handle_message(msg))
            task.add_done_callback(self._log_task_error)

    @staticmethod
    def _log_task_error(task: asyncio.Task) -> None:
        """暴露后台任务中未处理的异常。"""
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            logger.error("[Manager] unhandled error in message task: %s", exc, exc_info=exc)

    async def _handle_message(self, msg: InboundMessage) -> None:
        async with self._semaphore:
            try:
                if msg.msg_type == InboundMessageType.COMMAND:
                    await self._handle_command(msg)
                else:
                    await self._handle_chat(msg)
            except Exception:
                logger.exception(
                    "Error handling message from %s (chat=%s)",
                    msg.channel_name,
                    msg.chat_id,
                )
                await self._send_error(msg, "An internal error occurred. Please try again.")

    # -- 聊天处理 ------------------------------------------------------------

    async def _create_thread(self, client, msg: InboundMessage) -> str:
        """在 LangGraph Server 创建新线程并保存映射。"""
        thread = await client.threads.create()
        thread_id = thread["thread_id"]
        self.store.set_thread_id(
            msg.channel_name,
            msg.chat_id,
            thread_id,
            topic_id=msg.topic_id,
            user_id=msg.user_id,
        )
        logger.info("[Manager] new thread created on LangGraph Server: thread_id=%s for chat_id=%s topic_id=%s", thread_id, msg.chat_id, msg.topic_id)
        return thread_id

    async def _handle_chat(self, msg: InboundMessage) -> None:
        client = self._get_client()

        # 若存在 topic_id，则先查找已有 lumen 线程
        thread_id = None
        if msg.topic_id:
            thread_id = self.store.get_thread_id(msg.channel_name, msg.chat_id, topic_id=msg.topic_id)
            if thread_id:
                logger.info("[Manager] reusing thread: thread_id=%s for topic_id=%s", thread_id, msg.topic_id)

        # 未找到已有线程时创建新线程
        if thread_id is None:
            thread_id = await self._create_thread(client, msg)

        assistant_id, run_config, run_context = self._resolve_run_params(msg, thread_id)
        logger.info("[Manager] invoking runs.wait(thread_id=%s, text=%r)", thread_id, msg.text[:100])
        result = await client.runs.wait(
            thread_id,
            assistant_id,
            input={"messages": [{"role": "human", "content": msg.text}]},
            config=run_config,
            context=run_context,
        )

        response_text = _extract_response_text(result)
        artifacts = _extract_artifacts(result)

        logger.info(
            "[Manager] agent response received: thread_id=%s, response_len=%d, artifacts=%d",
            thread_id,
            len(response_text) if response_text else 0,
            len(artifacts),
        )

        # 将产物虚拟路径解析为实际文件，供通道上传
        attachments: list[ResolvedAttachment] = []
        if artifacts:
            attachments = _resolve_attachments(thread_id, artifacts)
            resolved_virtuals = {a.virtual_path for a in attachments}
            unresolved = [p for p in artifacts if p not in resolved_virtuals]
            if unresolved:
                artifact_text = _format_artifact_text(unresolved)
                response_text = (response_text + "\n\n" + artifact_text) if response_text else artifact_text
            # 始终把已解析附件文件名附加到文本作为兜底，
            # 即使上传跳过或失败，用户仍可感知文件产物。
            if attachments:
                resolved_text = _format_artifact_text([a.virtual_path for a in attachments])
                response_text = (response_text + "\n\n" + resolved_text) if response_text else resolved_text

        if not response_text:
            if attachments:
                response_text = _format_artifact_text([a.virtual_path for a in attachments])
            else:
                response_text = "(No response from agent)"

        outbound = OutboundMessage(
            channel_name=msg.channel_name,
            chat_id=msg.chat_id,
            thread_id=thread_id,
            text=response_text,
            artifacts=artifacts,
            attachments=attachments,
            thread_ts=msg.thread_ts,
        )
        logger.info("[Manager] publishing outbound message to bus: channel=%s, chat_id=%s", msg.channel_name, msg.chat_id)
        await self.bus.publish_outbound(outbound)

    # -- 命令处理 ------------------------------------------------------------

    async def _handle_command(self, msg: InboundMessage) -> None:
        text = msg.text.strip()
        parts = text.split(maxsplit=1)
        command = parts[0].lower().lstrip("/")

        if command == "new":
            # 在 LangGraph Server 创建新线程
            client = self._get_client()
            thread = await client.threads.create()
            new_thread_id = thread["thread_id"]
            self.store.set_thread_id(
                msg.channel_name,
                msg.chat_id,
                new_thread_id,
                topic_id=msg.topic_id,
                user_id=msg.user_id,
            )
            reply = "New conversation started."
        elif command == "status":
            thread_id = self.store.get_thread_id(msg.channel_name, msg.chat_id, topic_id=msg.topic_id)
            reply = f"Active thread: {thread_id}" if thread_id else "No active conversation."
        elif command == "models":
            reply = await self._fetch_gateway("/api/models", "models")
        elif command == "memory":
            reply = await self._fetch_gateway("/api/memory", "memory")
        elif command == "help":
            reply = "Available commands:\n/new — Start a new conversation\n/status — Show current thread info\n/models — List available models\n/memory — Show memory status\n/help — Show this help"
        else:
            reply = f"Unknown command: /{command}. Type /help for available commands."

        outbound = OutboundMessage(
            channel_name=msg.channel_name,
            chat_id=msg.chat_id,
            thread_id=self.store.get_thread_id(msg.channel_name, msg.chat_id) or "",
            text=reply,
            thread_ts=msg.thread_ts,
        )
        await self.bus.publish_outbound(outbound)

    async def _fetch_gateway(self, path: str, kind: str) -> str:
        """从 Gateway API 拉取命令回复所需数据。"""
        import httpx

        try:
            async with httpx.AsyncClient() as http:
                resp = await http.get(f"{self._gateway_url}{path}", timeout=10)
                resp.raise_for_status()
                data = resp.json()
        except Exception:
            logger.exception("Failed to fetch %s from gateway", kind)
            return f"Failed to fetch {kind} information."

        if kind == "models":
            names = [m["name"] for m in data.get("models", [])]
            return ("Available models:\n" + "\n".join(f"• {n}" for n in names)) if names else "No models configured."
        elif kind == "memory":
            facts = data.get("facts", [])
            return f"Memory contains {len(facts)} fact(s)."
        return str(data)

    # -- 错误辅助 ------------------------------------------------------------

    async def _send_error(self, msg: InboundMessage, error_text: str) -> None:
        outbound = OutboundMessage(
            channel_name=msg.channel_name,
            chat_id=msg.chat_id,
            thread_id=self.store.get_thread_id(msg.channel_name, msg.chat_id) or "",
            text=error_text,
            thread_ts=msg.thread_ts,
        )
        await self.bus.publish_outbound(outbound)
