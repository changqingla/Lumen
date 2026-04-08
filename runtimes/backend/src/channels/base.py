"""即时通讯（IM）通道抽象基类。"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

from src.channels.message_bus import InboundMessage, InboundMessageType, MessageBus, OutboundMessage, ResolvedAttachment

logger = logging.getLogger(__name__)


class Channel(ABC):
    """
    每个通道连接一个外部消息平台，并负责：

    1. 接收消息，封装为 InboundMessage 后发布到总线。
    2. 订阅出站消息，并将回复发送回平台。

    子类必须实现 ``start``、``stop`` 与 ``send``。
    """

    def __init__(self, name: str, bus: MessageBus, config: dict[str, Any]) -> None:
        self.name = name
        self.bus = bus
        self.config = config
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    # -- 生命周期 ------------------------------------------------------------

    @abstractmethod
    async def start(self) -> None:
        """开始监听外部平台消息。"""

    @abstractmethod
    async def stop(self) -> None:
        """优雅停止通道。"""

    # -- 出站 ---------------------------------------------------------------

    @abstractmethod
    async def send(self, msg: OutboundMessage) -> None:
        """
        实现应使用 ``msg.chat_id`` 与 ``msg.thread_ts``，
        将回复路由到正确的会话/线程。

        """

    async def send_file(self, msg: OutboundMessage, attachment: ResolvedAttachment) -> bool:
        """
        若上传成功返回 True，否则返回 False。
        默认实现返回 False（表示不支持文件上传）。

        """
        return False

    # -- 辅助方法 -----------------------------------------------------------

    def _make_inbound(
        self,
        chat_id: str,
        user_id: str,
        text: str,
        *,
        msg_type: InboundMessageType = InboundMessageType.CHAT,
        thread_ts: str | None = None,
        files: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> InboundMessage:
        """用于创建 InboundMessage 的便捷工厂方法。"""
        return InboundMessage(
            channel_name=self.name,
            chat_id=chat_id,
            user_id=user_id,
            text=text,
            msg_type=msg_type,
            thread_ts=thread_ts,
            files=files or [],
            metadata=metadata or {},
        )

    async def _on_outbound(self, msg: OutboundMessage) -> None:
        """
        仅转发目标为当前通道的消息。
        先发送文本，再上传文件附件。
        若文本发送失败，则完全跳过文件上传，避免出现
        “只有文件没有配套文本”的部分投递。

        """
        if msg.channel_name == self.name:
            try:
                await self.send(msg)
            except Exception:
                logger.exception("Failed to send outbound message on channel %s", self.name)
                return  # 文本发送失败时不再尝试上传文件

            for attachment in msg.attachments:
                try:
                    success = await self.send_file(msg, attachment)
                    if not success:
                        logger.warning("[%s] file upload skipped for %s", self.name, attachment.filename)
                except Exception:
                    logger.exception("[%s] failed to upload file %s", self.name, attachment.filename)
