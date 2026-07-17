"""通道服务（ChannelService）：管理所有 IM 通道的生命周期。"""

from __future__ import annotations

import logging
from typing import Any

from src.channels.manager import ChannelManager
from src.channels.message_bus import MessageBus
from src.channels.store import ChannelStore

logger = logging.getLogger(__name__)

# 通道名 → 延迟加载导入路径
_CHANNEL_REGISTRY: dict[str, str] = {
    "feishu": "src.channels.feishu:FeishuChannel",
    "slack": "src.channels.slack:SlackChannel",
    "telegram": "src.channels.telegram:TelegramChannel",
}


class ChannelService:
    """
    从 ``config.yaml`` 的 ``channels`` 配置读取通道设置，
    实例化已启用通道，并启动 ChannelManager 分发器。

    """

    def __init__(
        self,
        channels_config: dict[str, Any] | None = None,
        *,
        gateway_internal_api_token: str | None = None,
    ) -> None:
        self.bus = MessageBus()
        self.store = ChannelStore()
        config = dict(channels_config or {})
        langgraph_url = config.pop("langgraph_url", None) or "http://localhost:2024"
        gateway_url = config.pop("gateway_url", None) or "http://localhost:8001"
        default_session = config.pop("session", None)
        channel_sessions = {
            name: channel_config.get("session")
            for name, channel_config in config.items()
            if isinstance(channel_config, dict)
        }
        self.manager = ChannelManager(
            bus=self.bus,
            store=self.store,
            langgraph_url=langgraph_url,
            gateway_url=gateway_url,
            gateway_internal_api_token=gateway_internal_api_token,
            default_session=default_session if isinstance(default_session, dict) else None,
            channel_sessions=channel_sessions,
        )
        self._channels: dict[str, Any] = {}  # 通道名 -> Channel 实例
        self._config = config
        self._running = False

    @classmethod
    def from_app_config(cls) -> ChannelService:
        """根据应用配置创建 ChannelService。"""
        from src.config.app_config import get_app_config
        from src.gateway.config import get_gateway_config

        config = get_app_config()
        channels_config = {}
        # 应用配置（AppConfig）允许额外字段（extra="allow"）
        extra = config.model_extra or {}
        if "channels" in extra:
            channels_config = extra["channels"]
        gateway_token = (
            get_gateway_config().internal_api_token.get_secret_value()
        )
        return cls(
            channels_config=channels_config,
            gateway_internal_api_token=gateway_token,
        )

    async def start(self) -> None:
        """启动管理器和所有已启用通道。"""
        if self._running:
            return

        await self.manager.start()

        for name, channel_config in self._config.items():
            if not isinstance(channel_config, dict):
                continue
            if not channel_config.get("enabled", False):
                logger.info("Channel %s is disabled, skipping", name)
                continue

            await self._start_channel(name, channel_config)

        self._running = True
        logger.info("ChannelService started with channels: %s", list(self._channels.keys()))

    async def stop(self) -> None:
        """停止所有通道和管理器。"""
        # Stop new work and cancel in-flight handlers before tearing down the
        # outbound channel callbacks they may still use.
        await self.manager.stop()

        for name, channel in list(self._channels.items()):
            try:
                await channel.stop()
                logger.info("Channel %s stopped", name)
            except Exception as exc:
                logger.error(
                    "Error stopping channel %s (%s)",
                    name,
                    type(exc).__name__,
                )
        self._channels.clear()
        self._running = False
        logger.info("ChannelService stopped")

    async def restart_channel(self, name: str) -> bool:
        """重启指定通道。成功时返回 `True`。"""
        if name in self._channels:
            try:
                await self._channels[name].stop()
            except Exception as exc:
                logger.error(
                    "Error stopping channel %s for restart (%s)",
                    name,
                    type(exc).__name__,
                )
            del self._channels[name]

        config = self._config.get(name)
        if not config or not isinstance(config, dict):
            logger.warning("No config for channel %s", name)
            return False

        return await self._start_channel(name, config)

    async def _start_channel(self, name: str, config: dict[str, Any]) -> bool:
        """实例化并启动单个通道。"""
        import_path = _CHANNEL_REGISTRY.get(name)
        if not import_path:
            logger.warning("Unknown channel type: %s", name)
            return False

        try:
            from src.reflection import resolve_class

            channel_cls = resolve_class(import_path, base_class=None)
        except Exception as exc:
            logger.error(
                "Failed to import channel class for %s (%s)",
                name,
                type(exc).__name__,
            )
            return False

        try:
            channel = channel_cls(bus=self.bus, config=config)
            await channel.start()
            self._channels[name] = channel
            logger.info("Channel %s started", name)
            return True
        except Exception as exc:
            logger.error(
                "Failed to start channel %s (%s)",
                name,
                type(exc).__name__,
            )
            return False

    def get_status(self) -> dict[str, Any]:
        """返回所有通道的状态信息。"""
        channels_status = {}
        for name in _CHANNEL_REGISTRY:
            config = self._config.get(name, {})
            enabled = isinstance(config, dict) and config.get("enabled", False)
            running = name in self._channels and self._channels[name].is_running
            channels_status[name] = {
                "enabled": enabled,
                "running": running,
            }
        return {
            "service_running": self._running,
            "channels": channels_status,
        }


# -- 单例访问 ---------------------------------------------------------------

_channel_service: ChannelService | None = None


def get_channel_service() -> ChannelService | None:
    """获取通道服务单例（若已启动）。"""
    return _channel_service


async def start_channel_service() -> ChannelService:
    """根据应用配置创建并启动全局 ChannelService。"""
    global _channel_service
    if _channel_service is not None:
        return _channel_service
    _channel_service = ChannelService.from_app_config()
    await _channel_service.start()
    return _channel_service


async def stop_channel_service() -> None:
    """停止全局通道服务。"""
    global _channel_service
    if _channel_service is not None:
        await _channel_service.stop()
        _channel_service = None
