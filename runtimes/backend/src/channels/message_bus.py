"""消息总线（MessageBus）：用于解耦通道与分发器的异步发布/订阅总线。"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 消息类型
# ---------------------------------------------------------------------------


class InboundMessageType(StrEnum):
    """来自 IM 通道的入站消息类型。"""

    CHAT = "chat"
    COMMAND = "command"


@dataclass
class InboundMessage:
    """
    字段：
        channel_name: 源通道名称（例如 "feishu"、"slack"）。
        chat_id: 平台侧会话/对话标识。
        user_id: 平台侧用户标识。
        text: 消息文本。
        msg_type: 普通聊天消息或命令消息。
        thread_ts: 可选的平台线程标识（用于线程回复）。
        topic_id: 会话主题标识，用于映射到 lumen 线程。
            在同一 ``chat_id`` 下，共享 ``topic_id`` 的消息会复用同一 lumen 线程。
            当为 ``None`` 时，每条消息都创建新线程（一次性问答）。
        files: 可选文件附件列表（平台相关字典结构）。
        metadata: 来自通道的任意额外数据。
        created_at: 消息创建时间（Unix 时间戳）。

    """

    channel_name: str
    chat_id: str
    user_id: str
    text: str
    msg_type: InboundMessageType = InboundMessageType.CHAT
    thread_ts: str | None = None
    topic_id: str | None = None
    files: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)


@dataclass
class ResolvedAttachment:
    """
    字段：
        virtual_path: 原始虚拟路径（例如 /mnt/user-data/outputs/report.pdf）。
        actual_path: 解析后的宿主机文件系统路径。
        filename: 文件名（basename）。
        mime_type: MIME 类型（例如 "application/pdf"）。
        size: 文件大小（字节）。
        is_image: 对 image/* MIME 类型为 True（某些平台会差异化处理图片）。

    """

    virtual_path: str
    actual_path: Path
    filename: str
    mime_type: str
    size: int
    is_image: bool
    temporary: bool = False

    def cleanup(self) -> None:
        """Remove a private attachment snapshot after all listeners finish."""

        if self.temporary:
            self.actual_path.unlink(missing_ok=True)


@dataclass
class OutboundMessage:
    """
    字段：
        channel_name: 目标通道名（用于路由）。
        chat_id: 目标会话标识。
        thread_id: 生成该回复的 lumen 线程 ID。
        text: 回复文本。
        artifacts: Agent 产物路径列表。
        is_final: 是否为当前回复流的最终消息。
        thread_ts: 可选的平台线程标识（用于线程回复）。
        metadata: 任意扩展数据。
        created_at: Unix 时间戳。

    """

    channel_name: str
    chat_id: str
    thread_id: str
    text: str
    artifacts: list[str] = field(default_factory=list)
    attachments: list[ResolvedAttachment] = field(default_factory=list)
    is_final: bool = True
    thread_ts: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# 消息总线
# ---------------------------------------------------------------------------

OutboundCallback = Callable[[OutboundMessage], Coroutine[Any, Any, None]]


class MessageBus:
    """
    通道发布入站消息，分发器消费入站消息；
    分发器发布出站消息，通道通过注册回调接收。

    """

    def __init__(self) -> None:
        self._inbound_queue: asyncio.Queue[InboundMessage] = asyncio.Queue()
        self._outbound_listeners: list[OutboundCallback] = []

    # -- 入站 ---------------------------------------------------------------

    async def publish_inbound(self, msg: InboundMessage) -> None:
        """将通道入站消息放入队列。"""
        await self._inbound_queue.put(msg)
        logger.info(
            "[Bus] inbound enqueued: channel=%s, type=%s, queue_size=%d",
            msg.channel_name,
            msg.msg_type.value,
            self._inbound_queue.qsize(),
        )

    async def get_inbound(self) -> InboundMessage:
        """阻塞直到拿到下一条入站消息。"""
        return await self._inbound_queue.get()

    @property
    def inbound_queue(self) -> asyncio.Queue[InboundMessage]:
        return self._inbound_queue

    # -- 出站 ---------------------------------------------------------------

    def subscribe_outbound(self, callback: OutboundCallback) -> None:
        """注册一个用于出站消息的异步回调。"""
        self._outbound_listeners.append(callback)

    def unsubscribe_outbound(self, callback: OutboundCallback) -> None:
        """移除已注册的出站回调。"""
        self._outbound_listeners = [cb for cb in self._outbound_listeners if cb is not callback]

    async def publish_outbound(self, msg: OutboundMessage) -> None:
        """将出站消息分发给所有已注册监听器。"""
        logger.info(
            "[Bus] outbound dispatching: channel=%s, listeners=%d, text_len=%d",
            msg.channel_name,
            len(self._outbound_listeners),
            len(msg.text),
        )
        for callback in self._outbound_listeners:
            try:
                await callback(msg)
            except Exception as exc:
                logger.error(
                    "Error in outbound callback for channel=%s (%s)",
                    msg.channel_name,
                    type(exc).__name__,
                )
