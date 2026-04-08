import os

from pydantic import BaseModel, Field


class GatewayConfig(BaseModel):
    """网关（API 网关）配置。"""

    host: str = Field(default="0.0.0.0", description="网关服务绑定主机")
    port: int = Field(default=8001, description="网关服务绑定端口")
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"], description="允许的 CORS 来源列表")


_gateway_config: GatewayConfig | None = None


def get_gateway_config() -> GatewayConfig:
    """获取 Gateway 配置，必要时从环境变量加载。"""
    global _gateway_config
    if _gateway_config is None:
        cors_origins_str = os.getenv("CORS_ORIGINS", "http://localhost:3000")
        _gateway_config = GatewayConfig(
            host=os.getenv("GATEWAY_HOST", "0.0.0.0"),
            port=int(os.getenv("GATEWAY_PORT", "8001")),
            cors_origins=cors_origins_str.split(","),
        )
    return _gateway_config
