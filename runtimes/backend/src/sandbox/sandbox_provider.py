from abc import ABC, abstractmethod

from src.config import get_app_config
from src.reflection import resolve_class
from src.sandbox.sandbox import Sandbox


class SandboxProvider(ABC):
    """沙箱提供者抽象基类。"""

    @abstractmethod
    def acquire(self, thread_id: str | None = None) -> str:
        """
        返回：
            已获取沙箱环境的 ID。

        """
        pass

    @abstractmethod
    def get(self, sandbox_id: str) -> Sandbox | None:
        """
        参数：
            sandbox_id: 要保留/查询的沙箱环境 ID。

        """
        pass

    @abstractmethod
    def release(self, sandbox_id: str) -> None:
        """
        参数：
            sandbox_id: 要销毁的沙箱环境 ID。

        """
        pass


_default_sandbox_provider: SandboxProvider | None = None


def get_sandbox_provider(**kwargs) -> SandboxProvider:
    """
    返回缓存的单例实例。可通过 `reset_sandbox_provider()` 清空缓存，
    或通过 `shutdown_sandbox_provider()` 先执行关闭再清空。

    返回：
        一个沙箱提供者实例。
    """
    global _default_sandbox_provider
    if _default_sandbox_provider is None:
        config = get_app_config()
        cls = resolve_class(config.sandbox.use, SandboxProvider)
        _default_sandbox_provider = cls(**kwargs)
    return _default_sandbox_provider


def reset_sandbox_provider() -> None:
    """
    清空缓存实例，但不调用 shutdown。
    下次调用 `get_sandbox_provider()` 时会创建新实例，
    适用于测试或切换配置。

    注意：若提供者仍有活动沙箱，这些沙箱将成为孤儿实例。
    需要完整清理时请使用 `shutdown_sandbox_provider()`。
    """
    global _default_sandbox_provider
    _default_sandbox_provider = None


def shutdown_sandbox_provider() -> None:
    """
    在清空单例前，先正确关闭提供者（释放全部沙箱）。
    适用于应用关闭阶段，或需要彻底重置沙箱系统时。

    """
    global _default_sandbox_provider
    if _default_sandbox_provider is not None:
        if hasattr(_default_sandbox_provider, "shutdown"):
            _default_sandbox_provider.shutdown()
        _default_sandbox_provider = None


def set_sandbox_provider(provider: SandboxProvider) -> None:
    """
    设置自定义提供者实例，可用于测试时注入 mock。

    参数：
        provider: 要使用的 SandboxProvider 实例。
    """
    global _default_sandbox_provider
    _default_sandbox_provider = provider
