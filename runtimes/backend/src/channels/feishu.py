"""飞书（Feishu/Lark）通道：通过 WebSocket 连接飞书（无需公网 IP）。"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Any

from src.channels.base import Channel
from src.channels.message_bus import InboundMessageType, MessageBus, OutboundMessage, ResolvedAttachment

logger = logging.getLogger(__name__)


class FeishuChannel(Channel):
    """
    配置项（``config.yaml`` 中 ``channels.feishu``）：
        - ``app_id``: 飞书应用 ID。
        - ``app_secret``: 飞书应用密钥。
        - ``verification_token``: （可选）事件校验 token。

    通道采用 WebSocket 长连接模式，因此不需要公网 IP。

    消息流程：
        1. 用户发送消息 → 机器人添加 "OK" emoji 反应
        2. 机器人在线程中回复："Working on it......"
        3. Agent 处理消息并返回结果
        4. 机器人在线程中回复结果
        5. 机器人在原消息上添加 "DONE" emoji 反应
    """

    def __init__(self, bus: MessageBus, config: dict[str, Any]) -> None:
        super().__init__(name="feishu", bus=bus, config=config)
        self._thread: threading.Thread | None = None
        self._main_loop: asyncio.AbstractEventLoop | None = None
        self._api_client = None
        self._CreateMessageReactionRequest = None
        self._CreateMessageReactionRequestBody = None
        self._Emoji = None
        self._CreateFileRequest = None
        self._CreateFileRequestBody = None
        self._CreateImageRequest = None
        self._CreateImageRequestBody = None

    async def start(self) -> None:
        if self._running:
            return

        try:
            import lark_oapi as lark
            from lark_oapi.api.im.v1 import (
                CreateFileRequest,
                CreateFileRequestBody,
                CreateImageRequest,
                CreateImageRequestBody,
                CreateMessageReactionRequest,
                CreateMessageReactionRequestBody,
                CreateMessageRequest,
                CreateMessageRequestBody,
                Emoji,
                ReplyMessageRequest,
                ReplyMessageRequestBody,
            )
        except ImportError:
            logger.error("lark-oapi is not installed. Install it with: uv add lark-oapi")
            return

        self._lark = lark
        self._CreateMessageRequest = CreateMessageRequest
        self._CreateMessageRequestBody = CreateMessageRequestBody
        self._ReplyMessageRequest = ReplyMessageRequest
        self._ReplyMessageRequestBody = ReplyMessageRequestBody
        self._CreateMessageReactionRequest = CreateMessageReactionRequest
        self._CreateMessageReactionRequestBody = CreateMessageReactionRequestBody
        self._Emoji = Emoji
        self._CreateFileRequest = CreateFileRequest
        self._CreateFileRequestBody = CreateFileRequestBody
        self._CreateImageRequest = CreateImageRequest
        self._CreateImageRequestBody = CreateImageRequestBody

        app_id = self.config.get("app_id", "")
        app_secret = self.config.get("app_secret", "")

        if not app_id or not app_secret:
            logger.error("Feishu channel requires app_id and app_secret")
            return

        self._api_client = lark.Client.builder().app_id(app_id).app_secret(app_secret).build()
        self._main_loop = asyncio.get_event_loop()

        self._running = True
        self.bus.subscribe_outbound(self._on_outbound)

        # 飞书 WebSocket 客户端（ws.Client）的构造与 start() 都必须在独立线程执行，并使用该线程自己的事件循环。
        # 飞书 SDK（lark-oapi）会在构造时缓存当前事件循环，随后调用 loop.run_until_complete()；
        # 若复用已运行中的 uvloop 会产生冲突。
        self._thread = threading.Thread(
            target=self._run_ws,
            args=(app_id, app_secret),
            daemon=True,
        )
        self._thread.start()
        logger.info("Feishu channel started")

    def _run_ws(self, app_id: str, app_secret: str) -> None:
        """
        lark-oapi SDK 在导入时会捕获一个模块级事件循环
        （``lark_oapi.ws.client.loop``）。当 uvicorn 使用 uvloop 时，
        该循环通常是主线程 uvloop，且已在运行，因此 ``Client.start()``
        内部调用 ``loop.run_until_complete()`` 会抛出 ``RuntimeError``。

        这里的绕过方案是：为当前线程创建一个普通 asyncio 事件循环，
        并在调用 ``start()`` 前替换 SDK 模块级循环引用。
        """
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            import lark_oapi as lark
            import lark_oapi.ws.client as _ws_client_mod

            # 替换 SDK 的模块级 loop，使 Client.start() 使用当前线程
            # 的（未运行）事件循环，而不是主线程 uvloop。
            _ws_client_mod.loop = loop

            event_handler = lark.EventDispatcherHandler.builder("", "").register_p2_im_message_receive_v1(self._on_message).build()
            ws_client = lark.ws.Client(
                app_id=app_id,
                app_secret=app_secret,
                event_handler=event_handler,
                log_level=lark.LogLevel.INFO,
            )
            ws_client.start()
        except Exception:
            if self._running:
                logger.exception("Feishu WebSocket error")

    async def stop(self) -> None:
        self._running = False
        self.bus.unsubscribe_outbound(self._on_outbound)
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        logger.info("Feishu channel stopped")

    async def send(self, msg: OutboundMessage, *, _max_retries: int = 3) -> None:
        if not self._api_client:
            logger.warning("[Feishu] send called but no api_client available")
            return

        logger.info(
            "[Feishu] sending reply: chat_id=%s, thread_ts=%s, text_len=%d",
            msg.chat_id,
            msg.thread_ts,
            len(msg.text),
        )
        content = self._build_card_content(msg.text)

        last_exc: Exception | None = None
        for attempt in range(_max_retries):
            try:
                if msg.thread_ts:
                    # 在线程中回复（话题）
                    request = self._ReplyMessageRequest.builder().message_id(msg.thread_ts).request_body(self._ReplyMessageRequestBody.builder().msg_type("interactive").content(content).reply_in_thread(True).build()).build()
                    await asyncio.to_thread(self._api_client.im.v1.message.reply, request)
                else:
                    # 发送新消息
                    request = self._CreateMessageRequest.builder().receive_id_type("chat_id").request_body(self._CreateMessageRequestBody.builder().receive_id(msg.chat_id).msg_type("interactive").content(content).build()).build()
                    await asyncio.to_thread(self._api_client.im.v1.message.create, request)

                # 最终回复时，在原消息添加 "DONE" 反应
                if msg.is_final and msg.thread_ts:
                    await self._add_reaction(msg.thread_ts, "DONE")

                return  # success
            except Exception as exc:
                last_exc = exc
                if attempt < _max_retries - 1:
                    delay = 2**attempt  # 1 秒、2 秒
                    logger.warning(
                        "[Feishu] send failed (attempt %d/%d), retrying in %ds: %s",
                        attempt + 1,
                        _max_retries,
                        delay,
                        exc,
                    )
                    await asyncio.sleep(delay)

        logger.error("[Feishu] send failed after %d attempts: %s", _max_retries, last_exc)
        raise last_exc  # type: ignore[misc]

    async def send_file(self, msg: OutboundMessage, attachment: ResolvedAttachment) -> bool:
        if not self._api_client:
            return False

        # 检查大小限制（图片 10MB，文件 30MB）
        if attachment.is_image and attachment.size > 10 * 1024 * 1024:
            logger.warning("[Feishu] image too large (%d bytes), skipping: %s", attachment.size, attachment.filename)
            return False
        if not attachment.is_image and attachment.size > 30 * 1024 * 1024:
            logger.warning("[Feishu] file too large (%d bytes), skipping: %s", attachment.size, attachment.filename)
            return False

        try:
            if attachment.is_image:
                file_key = await self._upload_image(attachment.actual_path)
                msg_type = "image"
                content = json.dumps({"image_key": file_key})
            else:
                file_key = await self._upload_file(attachment.actual_path, attachment.filename)
                msg_type = "file"
                content = json.dumps({"file_key": file_key})

            if msg.thread_ts:
                request = self._ReplyMessageRequest.builder().message_id(msg.thread_ts).request_body(self._ReplyMessageRequestBody.builder().msg_type(msg_type).content(content).reply_in_thread(True).build()).build()
                await asyncio.to_thread(self._api_client.im.v1.message.reply, request)
            else:
                request = self._CreateMessageRequest.builder().receive_id_type("chat_id").request_body(self._CreateMessageRequestBody.builder().receive_id(msg.chat_id).msg_type(msg_type).content(content).build()).build()
                await asyncio.to_thread(self._api_client.im.v1.message.create, request)

            logger.info("[Feishu] file sent: %s (type=%s)", attachment.filename, msg_type)
            return True
        except Exception:
            logger.exception("[Feishu] failed to upload/send file: %s", attachment.filename)
            return False

    async def _upload_image(self, path) -> str:
        """上传图片到飞书并返回 image_key。"""
        with open(str(path), "rb") as f:
            request = self._CreateImageRequest.builder().request_body(self._CreateImageRequestBody.builder().image_type("message").image(f).build()).build()
            response = await asyncio.to_thread(self._api_client.im.v1.image.create, request)
        if not response.success():
            raise RuntimeError(f"Feishu image upload failed: code={response.code}, msg={response.msg}")
        return response.data.image_key

    async def _upload_file(self, path, filename: str) -> str:
        """上传文件到飞书并返回 file_key。"""
        suffix = path.suffix.lower() if hasattr(path, "suffix") else ""
        if suffix in (".xls", ".xlsx", ".csv"):
            file_type = "xls"
        elif suffix in (".ppt", ".pptx"):
            file_type = "ppt"
        elif suffix == ".pdf":
            file_type = "pdf"
        elif suffix in (".doc", ".docx"):
            file_type = "doc"
        else:
            file_type = "stream"

        with open(str(path), "rb") as f:
            request = self._CreateFileRequest.builder().request_body(self._CreateFileRequestBody.builder().file_type(file_type).file_name(filename).file(f).build()).build()
            response = await asyncio.to_thread(self._api_client.im.v1.file.create, request)
        if not response.success():
            raise RuntimeError(f"Feishu file upload failed: code={response.code}, msg={response.msg}")
        return response.data.file_key

    # -- 消息格式化 ----------------------------------------------------------

    @staticmethod
    def _build_card_content(text: str) -> str:
        """
        飞书 interactive card 原生支持 markdown 渲染，
        包括标题、粗体/斜体、代码块、列表和链接。

        """
        card = {
            "config": {"wide_screen_mode": True},
            "elements": [{"tag": "markdown", "content": text}],
        }
        return json.dumps(card)

    # -- 反应辅助 ------------------------------------------------------------

    async def _add_reaction(self, message_id: str, emoji_type: str = "THUMBSUP") -> None:
        """给消息添加 emoji 反应。"""
        if not self._api_client or not self._CreateMessageReactionRequest:
            return
        try:
            request = self._CreateMessageReactionRequest.builder().message_id(message_id).request_body(self._CreateMessageReactionRequestBody.builder().reaction_type(self._Emoji.builder().emoji_type(emoji_type).build()).build()).build()
            await asyncio.to_thread(self._api_client.im.v1.message_reaction.create, request)
            logger.info("[Feishu] reaction '%s' added to message %s", emoji_type, message_id)
        except Exception:
            logger.exception("[Feishu] failed to add reaction '%s' to message %s", emoji_type, message_id)

    async def _send_running_reply(self, message_id: str) -> None:
        """在线程中回复一条“处理中...”提示。"""
        if not self._api_client:
            return
        try:
            content = self._build_card_content("Working on it...")
            request = self._ReplyMessageRequest.builder().message_id(message_id).request_body(self._ReplyMessageRequestBody.builder().msg_type("interactive").content(content).reply_in_thread(True).build()).build()
            await asyncio.to_thread(self._api_client.im.v1.message.reply, request)
            logger.info("[Feishu] 'Working on it......' reply sent for message %s", message_id)
        except Exception:
            logger.exception("[Feishu] failed to send running reply for message %s", message_id)

    # -- 内部 ---------------------------------------------------------------

    @staticmethod
    def _log_future_error(fut, name: str, msg_id: str) -> None:
        """`run_coroutine_threadsafe` 返回 Future 的错误回调。"""
        try:
            exc = fut.exception()
            if exc:
                logger.error("[Feishu] %s failed for msg_id=%s: %s", name, msg_id, exc)
        except Exception:
            pass

    def _on_message(self, event) -> None:
        """`lark-oapi` 收到消息时调用（运行于 lark 线程）。"""
        try:
            logger.info("[Feishu] raw event received: type=%s", type(event).__name__)
            message = event.event.message
            chat_id = message.chat_id
            msg_id = message.message_id
            sender_id = event.event.sender.sender_id.open_id

            # 当消息是飞书线程内回复时会携带 root_id。
            # 将其作为 topic_id，确保同一回复链复用同一 lumen 线程。
            root_id = getattr(message, "root_id", None) or None

            # 解析消息内容
            content = json.loads(message.content)
            text = content.get("text", "").strip()
            logger.info(
                "[Feishu] parsed message: chat_id=%s, msg_id=%s, root_id=%s, sender=%s, text=%r",
                chat_id,
                msg_id,
                root_id,
                sender_id,
                text[:100] if text else "",
            )

            if not text:
                logger.info("[Feishu] empty text, ignoring message")
                return

            # 判断是否为命令
            if text.startswith("/"):
                msg_type = InboundMessageType.COMMAND
            else:
                msg_type = InboundMessageType.CHAT

            # 主题 ID（topic_id）：回复消息使用 root_id（同主题），新消息使用 msg_id（新主题）
            topic_id = root_id or msg_id

            inbound = self._make_inbound(
                chat_id=chat_id,
                user_id=sender_id,
                text=text,
                msg_type=msg_type,
                thread_ts=msg_id,
                metadata={"message_id": msg_id, "root_id": root_id},
            )
            inbound.topic_id = topic_id

            # 在异步事件循环上调度
            if self._main_loop and self._main_loop.is_running():
                logger.info("[Feishu] publishing inbound message to bus (type=%s, msg_id=%s)", msg_type.value, msg_id)
                # 调度所有协程，并给 Future 绑定错误日志回调
                for name, coro in [
                    ("add_reaction", self._add_reaction(msg_id, "OK")),
                    ("send_running_reply", self._send_running_reply(msg_id)),
                    ("publish_inbound", self.bus.publish_inbound(inbound)),
                ]:
                    fut = asyncio.run_coroutine_threadsafe(coro, self._main_loop)
                    fut.add_done_callback(lambda f, n=name, mid=msg_id: self._log_future_error(f, n, mid))
            else:
                logger.warning("[Feishu] main loop not running, cannot publish inbound message")
        except Exception:
            logger.exception("[Feishu] error processing message")
