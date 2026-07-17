"""电报（Telegram）通道：通过长轮询连接（无需公网 IP）。"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

from src.channels.base import Channel
from src.channels.message_bus import InboundMessageType, MessageBus, OutboundMessage, ResolvedAttachment

logger = logging.getLogger(__name__)


class TelegramChannel(Channel):
    """
    配置项（``config.yaml`` 中 ``channels.telegram``）：
        - ``bot_token``: Telegram Bot API token（来自 @BotFather）。
        - ``allowed_users``: （可选）允许的 Telegram 用户 ID 列表。空列表表示允许所有人。

    """

    def __init__(self, bus: MessageBus, config: dict[str, Any]) -> None:
        super().__init__(name="telegram", bus=bus, config=config)
        self._application = None
        self._thread: threading.Thread | None = None
        self._tg_loop: asyncio.AbstractEventLoop | None = None
        self._main_loop: asyncio.AbstractEventLoop | None = None
        self._allowed_users: set[int] = set()
        for uid in config.get("allowed_users", []):
            try:
                self._allowed_users.add(int(uid))
            except (ValueError, TypeError):
                pass
        # 会话 ID（chat_id）-> 最近一次机器人消息 message_id（用于线程回复）
        self._last_bot_message: dict[str, int] = {}

    async def start(self) -> None:
        if self._running:
            return

        try:
            from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters
        except ImportError:
            logger.error("未安装 python-telegram-bot。安装命令：uv add python-telegram-bot")
            return

        bot_token = self.config.get("bot_token", "")
        if not bot_token:
            logger.error("Telegram 通道必须配置 bot_token")
            return

        self._main_loop = asyncio.get_event_loop()
        self._running = True
        self.bus.subscribe_outbound(self._on_outbound)

        # 构建应用实例
        app = ApplicationBuilder().token(bot_token).build()

        # 命令处理器
        app.add_handler(CommandHandler("start", self._cmd_start))
        app.add_handler(CommandHandler("new", self._cmd_generic))
        app.add_handler(CommandHandler("status", self._cmd_generic))
        app.add_handler(CommandHandler("models", self._cmd_generic))
        app.add_handler(CommandHandler("memory", self._cmd_generic))
        app.add_handler(CommandHandler("help", self._cmd_generic))

        # 普通消息处理器
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_text))

        self._application = app

        # 在独立线程中运行轮询，并使用该线程自己的事件循环
        self._thread = threading.Thread(target=self._run_polling, daemon=True)
        self._thread.start()
        logger.info("Telegram 通道已启动")

    async def stop(self) -> None:
        self._running = False
        self.bus.unsubscribe_outbound(self._on_outbound)
        if self._tg_loop and self._tg_loop.is_running():
            self._tg_loop.call_soon_threadsafe(self._tg_loop.stop)
        if self._thread:
            self._thread.join(timeout=10)
            self._thread = None
        self._application = None
        logger.info("Telegram 通道已停止")

    async def send(self, msg: OutboundMessage, *, _max_retries: int = 3) -> None:
        if not self._application:
            return

        try:
            chat_id = int(msg.chat_id)
        except (ValueError, TypeError):
            logger.error("无效的 Telegram chat_id")
            return

        kwargs: dict[str, Any] = {"chat_id": chat_id, "text": msg.text}

        # 回复本会话中最后一条机器人消息，以形成线程效果
        reply_to = self._last_bot_message.get(msg.chat_id)
        if reply_to:
            kwargs["reply_to_message_id"] = reply_to

        bot = self._application.bot
        last_exc: Exception | None = None
        for attempt in range(_max_retries):
            try:
                sent = await bot.send_message(**kwargs)
                self._last_bot_message[msg.chat_id] = sent.message_id
                return
            except Exception as exc:
                last_exc = exc
                if attempt < _max_retries - 1:
                    delay = 2**attempt  # 1 秒、2 秒
                    logger.warning(
                        "[Telegram] send failed (attempt %d/%d), retrying in %ds (%s)",
                        attempt + 1,
                        _max_retries,
                        delay,
                        type(exc).__name__,
                    )
                    await asyncio.sleep(delay)

        logger.error(
            "[Telegram] send failed after %d attempts (%s)",
            _max_retries,
            type(last_exc).__name__,
        )
        raise last_exc  # type: ignore[misc]

    async def send_file(self, msg: OutboundMessage, attachment: ResolvedAttachment) -> bool:
        if not self._application:
            return False

        try:
            chat_id = int(msg.chat_id)
        except (ValueError, TypeError):
            logger.error("[Telegram] 无效的 chat_id")
            return False

        # 电报平台（Telegram）限制：图片 10MB，文档 50MB
        if attachment.size > 50 * 1024 * 1024:
            logger.warning("[Telegram] file too large (%d bytes), skipping", attachment.size)
            return False

        bot = self._application.bot
        reply_to = self._last_bot_message.get(msg.chat_id)

        try:
            if attachment.is_image and attachment.size <= 10 * 1024 * 1024:
                with open(attachment.actual_path, "rb") as f:
                    kwargs: dict[str, Any] = {"chat_id": chat_id, "photo": f}
                    if reply_to:
                        kwargs["reply_to_message_id"] = reply_to
                    sent = await bot.send_photo(**kwargs)
            else:
                from telegram import InputFile

                with open(attachment.actual_path, "rb") as f:
                    input_file = InputFile(f, filename=attachment.filename)
                    kwargs = {"chat_id": chat_id, "document": input_file}
                    if reply_to:
                        kwargs["reply_to_message_id"] = reply_to
                    sent = await bot.send_document(**kwargs)

            self._last_bot_message[msg.chat_id] = sent.message_id
            logger.info("[Telegram] file sent")
            return True
        except Exception as exc:
            logger.error(
                "[Telegram] failed to send file (%s)",
                type(exc).__name__,
            )
            return False

    # -- 辅助方法 -----------------------------------------------------------

    async def _send_running_reply(self, chat_id: str, reply_to_message_id: int) -> None:
        """向用户消息回复“处理中...”。"""
        if not self._application:
            return
        try:
            bot = self._application.bot
            await bot.send_message(
                chat_id=int(chat_id),
                text="正在处理中...",
                reply_to_message_id=reply_to_message_id,
            )
            logger.info("[Telegram] 已发送处理中回复")
        except Exception as exc:
            logger.error(
                "[Telegram] 发送处理中回复失败（%s）",
                type(exc).__name__,
            )

    # -- 内部 ---------------------------------------------------------------

    def _run_polling(self) -> None:
        """在独立线程中运行 Telegram 轮询。"""
        self._tg_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._tg_loop)
        try:
            # 不能使用 run_polling()，因为它会调用 add_signal_handler()，
            # 而该调用只允许在主线程执行。这里改为手动初始化应用并启动 updater。
            self._tg_loop.run_until_complete(self._application.initialize())
            self._tg_loop.run_until_complete(self._application.start())
            self._tg_loop.run_until_complete(self._application.updater.start_polling())
            self._tg_loop.run_forever()
        except Exception as exc:
            if self._running:
                logger.error("Telegram 轮询出错（%s）", type(exc).__name__)
        finally:
            # 优雅关闭
            try:
                if self._application.updater.running:
                    self._tg_loop.run_until_complete(self._application.updater.stop())
                self._tg_loop.run_until_complete(self._application.stop())
                self._tg_loop.run_until_complete(self._application.shutdown())
            except Exception as exc:
                logger.error(
                    "关闭 Telegram 通道时出错（%s）",
                    type(exc).__name__,
                )

    def _check_user(self, user_id: int) -> bool:
        if not self._allowed_users:
            return True
        return user_id in self._allowed_users

    async def _cmd_start(self, update, context) -> None:
        """处理 /start 命令。"""
        if not self._check_user(update.effective_user.id):
            return
        await update.message.reply_text("欢迎使用 lumen！直接发送消息即可开始对话。\n输入 /help 可查看可用命令。")

    async def _cmd_generic(self, update, context) -> None:
        """将斜杠命令转发给通道管理器。"""
        if not self._check_user(update.effective_user.id):
            return

        text = update.message.text
        chat_id = str(update.effective_chat.id)
        user_id = str(update.effective_user.id)
        msg_id = str(update.message.message_id)

        inbound = self._make_inbound(
            chat_id=chat_id,
            user_id=user_id,
            text=text,
            msg_type=InboundMessageType.COMMAND,
            thread_ts=msg_id,
        )

        if self._main_loop and self._main_loop.is_running():
            asyncio.run_coroutine_threadsafe(self._send_running_reply(chat_id, update.message.message_id), self._main_loop)
            asyncio.run_coroutine_threadsafe(self.bus.publish_inbound(inbound), self._main_loop)

    async def _on_text(self, update, context) -> None:
        """处理普通文本消息。"""
        if not self._check_user(update.effective_user.id):
            return

        text = update.message.text.strip()
        if not text:
            return

        chat_id = str(update.effective_chat.id)
        user_id = str(update.effective_user.id)
        msg_id = str(update.message.message_id)

        # 主题 ID（topic_id）：若用户回复了机器人消息，则复用该回复链对应的原始 topic_id；
        # 否则当前消息开启新主题。
        reply_to = update.message.reply_to_message
        if reply_to:
            topic_id = str(reply_to.message_id)
        else:
            topic_id = msg_id

        inbound = self._make_inbound(
            chat_id=chat_id,
            user_id=user_id,
            text=text,
            msg_type=InboundMessageType.CHAT,
            thread_ts=msg_id,
        )
        inbound.topic_id = topic_id

        if self._main_loop and self._main_loop.is_running():
            asyncio.run_coroutine_threadsafe(self._send_running_reply(chat_id, update.message.message_id), self._main_loop)
            asyncio.run_coroutine_threadsafe(self.bus.publish_inbound(inbound), self._main_loop)
