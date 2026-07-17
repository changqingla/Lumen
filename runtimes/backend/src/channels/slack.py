"""用于 Slack 平台的渠道：通过 Socket Mode 连接（无需公网 IP）。"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from markdown_to_mrkdwn import SlackMarkdownConverter

from src.channels.base import Channel
from src.channels.message_bus import InboundMessageType, MessageBus, OutboundMessage, ResolvedAttachment

logger = logging.getLogger(__name__)

_slack_md_converter = SlackMarkdownConverter()


class SlackChannel(Channel):
    """
    配置项（``config.yaml`` 中 ``channels.slack``）：
        - ``bot_token``: Slack Bot User OAuth Token（xoxb-...）。
        - ``app_token``: 用于 Socket Mode 的 Slack App-Level Token（xapp-...）。
        - ``allowed_users``: （可选）允许的 Slack 用户 ID 列表。空列表表示允许所有人。

    """

    def __init__(self, bus: MessageBus, config: dict[str, Any]) -> None:
        super().__init__(name="slack", bus=bus, config=config)
        self._socket_client = None
        self._web_client = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._allowed_users: set[str] = set(config.get("allowed_users", []))

    async def start(self) -> None:
        if self._running:
            return

        try:
            from slack_sdk import WebClient
            from slack_sdk.socket_mode import SocketModeClient
            from slack_sdk.socket_mode.response import SocketModeResponse
        except ImportError:
            logger.error("未安装 slack-sdk。安装命令：uv add slack-sdk")
            return

        self._SocketModeResponse = SocketModeResponse

        bot_token = self.config.get("bot_token", "")
        app_token = self.config.get("app_token", "")

        if not bot_token or not app_token:
            logger.error("Slack 通道必须配置 bot_token 和 app_token")
            return

        self._web_client = WebClient(token=bot_token)
        self._socket_client = SocketModeClient(
            app_token=app_token,
            web_client=self._web_client,
        )
        self._loop = asyncio.get_event_loop()

        self._socket_client.socket_mode_request_listeners.append(self._on_socket_event)

        self._running = True
        self.bus.subscribe_outbound(self._on_outbound)

        # 在后台线程启动 Socket Mode
        asyncio.get_event_loop().run_in_executor(None, self._socket_client.connect)
        logger.info("Slack 通道已启动")

    async def stop(self) -> None:
        self._running = False
        self.bus.unsubscribe_outbound(self._on_outbound)
        if self._socket_client:
            self._socket_client.close()
            self._socket_client = None
        logger.info("Slack 通道已停止")

    async def send(self, msg: OutboundMessage, *, _max_retries: int = 3) -> None:
        if not self._web_client:
            return

        kwargs: dict[str, Any] = {
            "channel": msg.chat_id,
                "text": _slack_md_converter.convert(msg.text),
        }
        if msg.thread_ts:
            kwargs["thread_ts"] = msg.thread_ts

        last_exc: Exception | None = None
        for attempt in range(_max_retries):
            try:
                await asyncio.to_thread(self._web_client.chat_postMessage, **kwargs)
                # 在线程根消息上添加完成反应
                if msg.thread_ts:
                    await asyncio.to_thread(
                        self._add_reaction,
                        msg.chat_id,
                        msg.thread_ts,
                        "white_check_mark",
                    )
                return
            except Exception as exc:
                last_exc = exc
                if attempt < _max_retries - 1:
                    delay = 2**attempt  # 1 秒、2 秒
                    logger.warning(
                        "[Slack] send failed (attempt %d/%d), retrying in %ds (%s)",
                        attempt + 1,
                        _max_retries,
                        delay,
                        type(exc).__name__,
                    )
                    await asyncio.sleep(delay)

        logger.error(
            "[Slack] send failed after %d attempts (%s)",
            _max_retries,
            type(last_exc).__name__,
        )
        # 发送失败时添加失败反应
        if msg.thread_ts:
            try:
                await asyncio.to_thread(
                    self._add_reaction,
                    msg.chat_id,
                    msg.thread_ts,
                    "x",
                )
            except Exception:
                pass
        raise last_exc  # type: ignore[misc]

    async def send_file(self, msg: OutboundMessage, attachment: ResolvedAttachment) -> bool:
        if not self._web_client:
            return False

        try:
            kwargs: dict[str, Any] = {
                "channel": msg.chat_id,
                "file": str(attachment.actual_path),
                "filename": attachment.filename,
                "title": attachment.filename,
            }
            if msg.thread_ts:
                kwargs["thread_ts"] = msg.thread_ts

            await asyncio.to_thread(self._web_client.files_upload_v2, **kwargs)
            logger.info("[Slack] file uploaded")
            return True
        except Exception as exc:
            logger.error(
                "[Slack] failed to upload file (%s)",
                type(exc).__name__,
            )
            return False

    # -- 内部 ---------------------------------------------------------------

    def _add_reaction(self, channel_id: str, timestamp: str, emoji: str) -> None:
        """为消息添加 emoji 反应（尽力而为，非阻塞）。"""
        if not self._web_client:
            return
        try:
            self._web_client.reactions_add(
                channel=channel_id,
                timestamp=timestamp,
                name=emoji,
            )
        except Exception as exc:
            if "already_reacted" not in str(exc):
                logger.warning(
                    "[Slack] failed to add reaction (%s)",
                    type(exc).__name__,
                )

    def _send_running_reply(self, channel_id: str, thread_ts: str) -> None:
        """在线程中发送“处理中...”回复（从 SDK 线程调用）。"""
        if not self._web_client:
            return
        try:
            self._web_client.chat_postMessage(
                channel=channel_id,
                text=":hourglass_flowing_sand: 正在处理中...",
                thread_ts=thread_ts,
            )
            logger.info("[Slack] 已发送处理中回复")
        except Exception as exc:
            logger.error(
                "[Slack] 发送处理中回复失败（%s）",
                type(exc).__name__,
            )

    def _on_socket_event(self, client, req) -> None:
        """由 slack-sdk 在每个 Socket Mode 事件上回调。"""
        try:
            # 确认已接收事件
            response = self._SocketModeResponse(envelope_id=req.envelope_id)
            client.send_socket_mode_response(response)

            event_type = req.type
            if event_type != "events_api":
                return

            event = req.payload.get("event", {})
            etype = event.get("type", "")

            # 处理消息事件（私聊或 @ 提及）
            if etype in ("message", "app_mention"):
                self._handle_message_event(event)

        except Exception as exc:
            logger.error("处理 Slack 事件时出错（%s）", type(exc).__name__)

    def _handle_message_event(self, event: dict) -> None:
        # 忽略机器人消息
        if event.get("bot_id") or event.get("subtype"):
            return

        user_id = event.get("user", "")

        # 检查白名单用户
        if self._allowed_users and user_id not in self._allowed_users:
            logger.debug("Ignoring message from non-allowed user")
            return

        text = event.get("text", "").strip()
        if not text:
            return

        channel_id = event.get("channel", "")
        thread_ts = event.get("thread_ts") or event.get("ts", "")

        if text.startswith("/"):
            msg_type = InboundMessageType.COMMAND
        else:
            msg_type = InboundMessageType.CHAT

        # 主题 ID（topic_id）：使用 thread_ts 作为主题标识。
        # 对线程消息，thread_ts 是根消息 ts（共享同一主题）。
        # 对非线程消息，thread_ts 是该消息自身 ts（新主题）。
        inbound = self._make_inbound(
            chat_id=channel_id,
            user_id=user_id,
            text=text,
            msg_type=msg_type,
            thread_ts=thread_ts,
        )
        inbound.topic_id = thread_ts

        if self._loop and self._loop.is_running():
            # 用 eyes 反应表示已接收
            self._add_reaction(channel_id, event.get("ts", thread_ts), "eyes")
            # 先发送“处理中...”回复（从 SDK 线程 fire-and-forget）
            self._send_running_reply(channel_id, thread_ts)
            asyncio.run_coroutine_threadsafe(self.bus.publish_inbound(inbound), self._loop)
