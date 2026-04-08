import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.config.app_config import get_app_config
from src.gateway.config import get_gateway_config
from src.gateway.routers import (
    agents,
    artifacts,
    channels,
    mcp,
    memory,
    models,
    skills,
    suggestions,
    uploads,
)

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """应用生命周期处理器。"""

    # 启动时加载配置并校验必要环境变量
    try:
        get_app_config()
        logger.info("配置加载成功")
    except Exception as e:
        error_msg = f"Gateway 启动时加载配置失败：{e}"
        logger.exception(error_msg)
        raise RuntimeError(error_msg) from e
    config = get_gateway_config()
    logger.info(f"正在启动 API Gateway：{config.host}:{config.port}")

    # 注意：这里不初始化 MCP 工具，原因如下：
    # 1. Gateway 本身不直接使用 MCP 工具，工具由 LangGraph Server 中的 Agent 调用
    # 2. Gateway 与 LangGraph Server 是独立进程，缓存也相互独立
    # 模型上下文协议（MCP）工具会在 LangGraph Server 首次需要时延迟初始化

    # 若配置了 IM 渠道，则启动渠道服务
    try:
        from src.channels.service import start_channel_service

        channel_service = await start_channel_service()
        logger.info("渠道服务已启动：%s", channel_service.get_status())
    except Exception:
        logger.exception("未配置 IM 通道，或渠道服务启动失败")

    yield

    # 关闭时停止渠道服务
    try:
        from src.channels.service import stop_channel_service

        await stop_channel_service()
    except Exception:
        logger.exception("停止渠道服务失败")
    logger.info("正在关闭 API Gateway")


def create_app() -> FastAPI:
    """创建并返回配置完成的 FastAPI 应用实例。"""

    app = FastAPI(
        title="lumen API Gateway",
        description="""
## lumen API Gateway

lumen 的 API Gateway，是一个基于 LangGraph、支持沙箱执行的 AI Agent 后端接口层。

### 功能概览

- **模型管理**：查询可用 AI 模型及其配置
- **MCP 配置**：管理 Model Context Protocol（MCP）服务配置
- **记忆管理**：读取和管理全局记忆数据，用于个性化对话
- **技能管理**：查询技能并控制启用状态
- **产物访问**：读取线程产物与生成文件
- **健康检查**：查看服务状态

### 架构说明

LangGraph 与 Gateway 为独立服务（默认端口分别为 2024/8001）。
Gateway 主要提供模型、MCP、技能、记忆、产物等自定义接口。
        """,
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        openapi_tags=[
            {
                "name": "models",
                "description": "查询可用 AI 模型及其配置",
            },
            {
                "name": "mcp",
                "description": "管理 Model Context Protocol（MCP）服务配置",
            },
            {
                "name": "memory",
                "description": "访问和管理全局记忆数据，用于个性化对话",
            },
            {
                "name": "skills",
                "description": "管理技能及其配置",
            },
            {
                "name": "artifacts",
                "description": "访问和下载线程产物与生成文件",
            },
            {
                "name": "uploads",
                "description": "上传并管理线程相关用户文件",
            },
            {
                "name": "agents",
                "description": "创建并管理带独立配置和提示词的自定义 Agent",
            },
            {
                "name": "suggestions",
                "description": "为对话生成后续追问建议",
            },
            {
                "name": "channels",
                "description": "管理 IM 通道集成（Feishu、Slack、Telegram）",
            },
            {
                "name": "health",
                "description": "健康检查与系统状态接口",
            },
        ],
    )

    # 当前不在应用层启用 CORS；如需跨域，请在部署层统一配置。

    # 注册路由
    # 模型接口挂载于 /api/models
    app.include_router(models.router)

    # 模型上下文协议（MCP）接口挂载于 /api/mcp
    app.include_router(mcp.router)

    # 记忆接口挂载于 /api/memory
    app.include_router(memory.router)

    # 技能接口挂载于 /api/skills
    app.include_router(skills.router)

    # 产物接口挂载于 /api/threads/{thread_id}/artifacts
    app.include_router(artifacts.router)

    # 上传接口挂载于 /api/threads/{thread_id}/uploads
    app.include_router(uploads.router)

    # 代理接口挂载于 /api/agents
    app.include_router(agents.router)

    # 建议问题接口挂载于 /api/threads/{thread_id}/suggestions
    app.include_router(suggestions.router)

    # 渠道接口挂载于 /api/channels
    app.include_router(channels.router)

    @app.get("/health", tags=["health"])
    async def health_check() -> dict:
        """返回服务健康状态信息。"""
        return {"status": "healthy", "service": "lumen-gateway"}

    return app


# 为 uvicorn 创建应用实例
app = create_app()
