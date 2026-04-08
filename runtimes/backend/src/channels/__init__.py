"""
提供可插拔的通道系统，通过 ChannelManager 将外部消息平台
（Feishu/Lark、Slack、Telegram）连接到 lumen。
ChannelManager 使用 ``langgraph-sdk`` 与底层 LangGraph Server 通信。
"""

from src.channels.base import Channel
from src.channels.message_bus import InboundMessage, MessageBus, OutboundMessage

__all__ = [
    "Channel",
    "InboundMessage",
    "MessageBus",
    "OutboundMessage",
]
