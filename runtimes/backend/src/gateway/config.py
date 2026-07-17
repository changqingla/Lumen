import os

from pydantic import BaseModel, Field, SecretStr

_INTERNAL_TOKEN_ENV = "GATEWAY_INTERNAL_API_TOKEN"
_MIN_INTERNAL_TOKEN_LENGTH = 32


def validate_gateway_internal_api_token(value: str | None) -> str:
    """Normalize a service token and reject missing or template credentials."""
    token = str(value or "").strip()
    if not token:
        raise RuntimeError(f"{_INTERNAL_TOKEN_ENV} is required")
    if not token.isascii():
        raise RuntimeError(f"{_INTERNAL_TOKEN_ENV} must contain only ASCII characters")
    if len(token) < _MIN_INTERNAL_TOKEN_LENGTH or token.lower().startswith(
        ("change-me", "replace-with-")
    ):
        raise RuntimeError(
            f"{_INTERNAL_TOKEN_ENV} must be a random token of at least "
            f"{_MIN_INTERNAL_TOKEN_LENGTH} characters"
        )
    return token


class GatewayConfig(BaseModel):
    """网关（API 网关）配置。"""

    host: str = Field(default="0.0.0.0", description="网关服务绑定主机")
    port: int = Field(default=8001, description="网关服务绑定端口")
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"], description="允许的 CORS 来源列表")
    internal_api_token: SecretStr = Field(description="Gateway 内部 API 服务认证 token")


_gateway_config: GatewayConfig | None = None


def get_gateway_config() -> GatewayConfig:
    """获取 Gateway 配置，必要时从环境变量加载。"""
    global _gateway_config
    if _gateway_config is None:
        cors_origins_str = os.getenv("CORS_ORIGINS", "http://localhost:3000")
        internal_api_token = validate_gateway_internal_api_token(
            os.getenv(_INTERNAL_TOKEN_ENV)
        )
        _gateway_config = GatewayConfig(
            host=os.getenv("GATEWAY_HOST", "0.0.0.0"),
            port=int(os.getenv("GATEWAY_PORT", "8001")),
            cors_origins=cors_origins_str.split(","),
            internal_api_token=SecretStr(internal_api_token),
        )
    return _gateway_config
