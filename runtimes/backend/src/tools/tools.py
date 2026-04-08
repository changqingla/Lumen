import logging

from langchain.tools import BaseTool

from src.config import get_app_config
from src.models.resolver import resolve_chat_model_spec
from src.reflection import resolve_variable
from src.tools.builtins import ask_clarification_tool, present_file_tool, task_tool, view_image_tool

logger = logging.getLogger(__name__)

BUILTIN_TOOLS = [
    present_file_tool,
    ask_clarification_tool,
]

SUBAGENT_TOOLS = [
    task_tool,
    # `task_status_tool` 不再暴露给 LLM（轮询逻辑由后端内部处理）
]


def get_available_tools(
    groups: list[str] | None = None,
    include_mcp: bool = True,
    model_name: str | None = None,
    dynamic_model_token: str | None = None,
    thread_id: str | None = None,
    supports_vision_override: bool | None = None,
    subagent_enabled: bool = False,
) -> list[BaseTool]:
    """
    注意：MCP 工具应在应用启动时通过 `src.mcp` 模块中的
    `initialize_mcp_tools()` 进行初始化。

    参数：
        groups: 可选工具组过滤列表。
        include_mcp: 是否包含 MCP 服务器工具（默认：True）。
        model_name: 可选模型名，用于判断是否加入视觉工具。
        dynamic_model_token: 动态模型绑定令牌；提供时可解析动态模型能力。
        supports_vision_override: 显式视觉能力覆盖值；优先级高于模型解析。
        subagent_enabled: 是否包含子代理工具（task、task_status）。

    返回：
        可用工具列表。
    """
    config = get_app_config()
    loaded_tools = [resolve_variable(tool.use, BaseTool) for tool in config.tools if groups is None or tool.group in groups]

    # 若启用，则获取缓存的 MCP 工具
    # 注意：此处使用 ExtensionsConfig.from_file() 而非 config.extensions，
    # 以确保始终从磁盘读取最新配置。这样 Gateway API（独立进程）写入的配置变更
    # 能在加载 MCP 工具时立即生效。
    mcp_tools = []
    if include_mcp:
        try:
            from src.config.extensions_config import ExtensionsConfig
            from src.mcp.cache import get_cached_mcp_tools

            extensions_config = ExtensionsConfig.from_file()
            if extensions_config.get_enabled_mcp_servers():
                mcp_tools = get_cached_mcp_tools()
                if mcp_tools:
                    logger.info(f"Using {len(mcp_tools)} cached MCP tool(s)")
        except ImportError:
            logger.warning("MCP module not available. Install 'langchain-mcp-adapters' package to enable MCP tools.")
        except Exception as e:
            logger.error(f"Failed to get cached MCP tools: {e}")

    # 按配置条件添加工具
    builtin_tools = BUILTIN_TOOLS.copy()

    # 仅在运行时参数启用时添加子代理工具
    if subagent_enabled:
        builtin_tools.extend(SUBAGENT_TOOLS)
        logger.info("Including subagent tools (task)")

    resolved_supports_vision = supports_vision_override
    if resolved_supports_vision is None:
        try:
            resolved_supports_vision = resolve_chat_model_spec(
                model_name,
                dynamic_model_token=dynamic_model_token,
                thread_id=thread_id,
            ).supports_vision
        except Exception:
            resolved_supports_vision = False

    # 仅当模型支持视觉时添加 view_image_tool
    if resolved_supports_vision:
        builtin_tools.append(view_image_tool)
        logger.info(f"Including view_image_tool for model '{model_name}' (supports_vision=True)")

    return loaded_tools + builtin_tools + mcp_tools
