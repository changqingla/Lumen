"""模型上下文协议（MCP）工具缓存，避免重复加载。"""

import asyncio
import logging
import os

from langchain_core.tools import BaseTool

logger = logging.getLogger(__name__)

_mcp_tools_cache: list[BaseTool] | None = None
_cache_initialized = False
_initialization_lock = asyncio.Lock()
_config_mtime: float | None = None  # 记录配置文件修改时间


def _get_config_mtime() -> float | None:
    """获取配置文件修改时间。

    返回：
        文件存在时返回浮点时间戳，不存在则返回 None。
    """
    from src.config.extensions_config import ExtensionsConfig

    config_path = ExtensionsConfig.resolve_config_path()
    if config_path and config_path.exists():
        return os.path.getmtime(config_path)
    return None


def _is_cache_stale() -> bool:
    """判断缓存是否过期。

    返回：
        需要失效重建时返回 True，否则返回 False。
    """
    global _config_mtime

    if not _cache_initialized:
        return False  # 尚未初始化，不视为过期

    current_mtime = _get_config_mtime()

    # 若当前或历史 mtime 不可用，则保守视为不过期
    if _config_mtime is None or current_mtime is None:
        return False

    # 若配置文件在缓存之后被修改，则判定为过期
    if current_mtime > _config_mtime:
        logger.info(f"MCP config file has been modified (mtime: {_config_mtime} -> {current_mtime}), cache is stale")
        return True

    return False


async def initialize_mcp_tools() -> list[BaseTool]:
    """初始化 MCP 工具缓存。

    建议在应用启动时调用一次。

    返回：
        来自所有已启用 MCP 服务的 LangChain 工具列表。
    """
    global _mcp_tools_cache, _cache_initialized, _config_mtime

    async with _initialization_lock:
        if _cache_initialized:
            logger.info("MCP tools already initialized")
            return _mcp_tools_cache or []

        from src.mcp.tools import get_mcp_tools

        logger.info("Initializing MCP tools...")
        _mcp_tools_cache = await get_mcp_tools()
        _cache_initialized = True
        _config_mtime = _get_config_mtime()  # 记录配置文件 mtime
        logger.info(f"MCP tools initialized: {len(_mcp_tools_cache)} tool(s) loaded (config mtime: {_config_mtime})")

        return _mcp_tools_cache


def get_cached_mcp_tools() -> list[BaseTool]:
    """获取 MCP 工具缓存（必要时自动初始化）。

    若尚未初始化，会自动初始化，确保 FastAPI 与 LangGraph Studio
    等上下文都可正常使用 MCP 工具。

    同时会检查配置文件是否自上次初始化后发生变化，若变化则自动重建缓存，
    以便 Gateway API（独立进程）写入的配置变更能反映到 LangGraph Server。

    返回：
        缓存中的 MCP 工具列表。
    """
    global _cache_initialized

    # 根据配置文件变化检查缓存是否过期
    if _is_cache_stale():
        logger.info("MCP cache is stale, resetting for re-initialization...")
        reset_mcp_tools_cache()

    if not _cache_initialized:
        logger.info("MCP tools not initialized, performing lazy initialization...")
        try:
            # 尝试在当前事件循环中初始化
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # 若当前循环已在运行（如 LangGraph Studio），
                # 则在线程中创建新循环执行初始化
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(asyncio.run, initialize_mcp_tools())
                    future.result()
            else:
                # 当前循环未运行时，可直接在该循环中执行
                loop.run_until_complete(initialize_mcp_tools())
        except RuntimeError:
            # 当前无事件循环，创建一个新循环运行
            asyncio.run(initialize_mcp_tools())
        except Exception as e:
            logger.error(f"Failed to lazy-initialize MCP tools: {e}")
            return []

    return _mcp_tools_cache or []


def reset_mcp_tools_cache() -> None:
    """重置 MCP 工具缓存。

    常用于测试场景或需要强制重新加载 MCP 工具时。
    """
    global _mcp_tools_cache, _cache_initialized, _config_mtime
    _mcp_tools_cache = None
    _cache_initialized = False
    _config_mtime = None
    logger.info("MCP tools cache reset")
