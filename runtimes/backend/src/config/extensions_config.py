"""模型上下文协议（MCP）服务器与技能的统一扩展配置。"""

import json
import os
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

_RUNTIMES_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_CONFIG_DIR = _RUNTIMES_ROOT / "config"
_DEFAULT_EXTENSIONS_CONFIG_PATH = _DEFAULT_CONFIG_DIR / "extensions_config.json"
_DEFAULT_LEGACY_MCP_CONFIG_PATH = _DEFAULT_CONFIG_DIR / "mcp_config.json"


class McpOAuthConfig(BaseModel):
    """模型上下文协议服务器（MCP）的 OAuth 配置（HTTP/SSE 传输方式）。"""

    enabled: bool = Field(default=True, description="是否启用 OAuth token 注入")
    token_url: str = Field(description="OAuth token 端点 URL")
    grant_type: Literal["client_credentials", "refresh_token"] = Field(
        default="client_credentials",
        description="OAuth 授权类型",
    )
    client_id: str | None = Field(default=None, description="OAuth 客户端 ID")
    client_secret: str | None = Field(default=None, description="OAuth 客户端密钥")
    refresh_token: str | None = Field(default=None, description="OAuth 刷新令牌（用于 refresh_token 授权）")
    scope: str | None = Field(default=None, description="OAuth scope")
    audience: str | None = Field(default=None, description="OAuth audience（与提供方相关）")
    token_field: str = Field(default="access_token", description="token 响应中 access token 所在字段名")
    token_type_field: str = Field(default="token_type", description="token 响应中 token 类型所在字段名")
    expires_in_field: str = Field(default="expires_in", description="token 响应中有效期（秒）字段名")
    default_token_type: str = Field(default="Bearer", description="当响应中缺失 token 类型时使用的默认值")
    refresh_skew_seconds: int = Field(default=60, description="在过期前提前多少秒刷新 token")
    extra_token_params: dict[str, str] = Field(default_factory=dict, description="发送到 token 端点的额外表单参数")
    model_config = ConfigDict(extra="allow")


class McpServerConfig(BaseModel):
    """单个 MCP 服务器配置。"""

    enabled: bool = Field(default=True, description="是否启用该 MCP 服务器")
    type: str = Field(default="stdio", description="传输类型：'stdio'、'sse' 或 'http'")
    command: str | None = Field(default=None, description="用于启动 MCP 服务器的命令（stdio 类型）")
    args: list[str] = Field(default_factory=list, description="传给命令的参数（stdio 类型）")
    env: dict[str, str] = Field(default_factory=dict, description="MCP 服务器环境变量")
    url: str | None = Field(default=None, description="MCP 服务器 URL（sse 或 http 类型）")
    headers: dict[str, str] = Field(default_factory=dict, description="发送的 HTTP 头（sse 或 http 类型）")
    oauth: McpOAuthConfig | None = Field(default=None, description="OAuth 配置（sse 或 http 类型）")
    description: str = Field(default="", description="该 MCP 服务器能力的人类可读说明")
    model_config = ConfigDict(extra="allow")


class SkillStateConfig(BaseModel):
    """单个技能状态配置。"""

    enabled: bool = Field(default=True, description="该技能是否启用")


class ExtensionsConfig(BaseModel):
    """模型上下文协议服务器（MCP）与技能的统一配置模型。"""

    mcp_servers: dict[str, McpServerConfig] = Field(
        default_factory=dict,
        description="MCP 服务器名称到配置的映射",
        alias="mcpServers",
    )
    skills: dict[str, SkillStateConfig] = Field(
        default_factory=dict,
        description="技能名称到状态配置的映射",
    )
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    @classmethod
    def resolve_config_path(cls, config_path: str | None = None) -> Path | None:
        """解析扩展配置文件路径。

        优先级：
        1. 若传入 `config_path` 参数，则使用该路径。
        2. 若设置 `LUMEN_EXTENSIONS_CONFIG_PATH` 环境变量，则使用该路径。
        3. 否则先在当前目录查找 `extensions_config.json`，再查找父目录。
        4. 为兼容旧版本，若未找到 `extensions_config.json`，还会检查 `mcp_config.json`。
        5. 若都未找到，返回 None（扩展配置是可选的）。

        参数：
            config_path: 扩展配置文件可选路径。

        返回：
            找到则返回扩展配置文件路径，否则返回 None。
        """
        if config_path:
            path = Path(config_path)
            if not path.exists():
                raise FileNotFoundError(f"Extensions config file specified by param `config_path` not found at {path}")
            return path
        elif os.getenv("LUMEN_EXTENSIONS_CONFIG_PATH"):
            path = Path(os.getenv("LUMEN_EXTENSIONS_CONFIG_PATH"))
            if not path.exists():
                raise FileNotFoundError(f"环境变量 `LUMEN_EXTENSIONS_CONFIG_PATH` 指定的扩展配置文件不存在：{path}")
            return path
        else:
            candidates = [
                Path(os.getcwd()) / "extensions_config.json",
                Path(os.getcwd()).parent / "extensions_config.json",
                _DEFAULT_EXTENSIONS_CONFIG_PATH,
                Path(os.getcwd()) / "mcp_config.json",
                Path(os.getcwd()).parent / "mcp_config.json",
                _DEFAULT_LEGACY_MCP_CONFIG_PATH,
            ]
            for path in candidates:
                if path.exists():
                    return path

            # 扩展配置是可选项，未找到时返回 None
            return None

    @classmethod
    def from_file(cls, config_path: str | None = None) -> "ExtensionsConfig":
        """从 JSON 文件加载扩展配置。

        详细路径解析规则见 `resolve_config_path`。

        参数：
            config_path: 扩展配置文件路径。

        返回：
            ExtensionsConfig: 读取到的配置；若文件不存在则返回空配置。
        """
        resolved_path = cls.resolve_config_path(config_path)
        if resolved_path is None:
            # 未找到扩展配置文件时返回空配置
            return cls(mcp_servers={}, skills={})

        try:
            with open(resolved_path, encoding="utf-8") as f:
                config_data = json.load(f)
            cls.resolve_env_variables(config_data)
            return cls.model_validate(config_data)
        except json.JSONDecodeError as e:
            raise ValueError(f"Extensions config file at {resolved_path} is not valid JSON: {e}") from e
        except Exception as e:
            raise RuntimeError(f"Failed to load extensions config from {resolved_path}: {e}") from e

    @classmethod
    def resolve_env_variables(cls, config: dict[str, Any]) -> dict[str, Any]:
        """递归解析配置中的环境变量。

        环境变量通过 `os.getenv` 解析，例如：`$OPENAI_API_KEY`。

        参数：
            config: 待解析环境变量的配置对象。

        返回：
            解析后的配置对象。
        """
        for key, value in config.items():
            if isinstance(value, str):
                if value.startswith("$"):
                    env_value = os.getenv(value[1:])
                    if env_value is None:
                        # 占位符无法解析时写入空字符串，避免下游消费者
                        # （例如 MCP 服务器）把字面量 "$VAR" 当作真实值。
                        config[key] = ""
                    else:
                        config[key] = env_value
                else:
                    config[key] = value
            elif isinstance(value, dict):
                config[key] = cls.resolve_env_variables(value)
            elif isinstance(value, list):
                config[key] = [cls.resolve_env_variables(item) if isinstance(item, dict) else item for item in value]
        return config

    def get_enabled_mcp_servers(self) -> dict[str, McpServerConfig]:
        """获取仅启用的 MCP 服务器。

        返回：
            启用 MCP 服务器的字典。
        """
        return {name: config for name, config in self.mcp_servers.items() if config.enabled}

    def is_skill_enabled(self, skill_name: str, skill_category: str) -> bool:
        """检查技能是否启用。

        参数：
            skill_name: 技能名称
            skill_category: 技能类别

        返回：
            启用返回 True，否则返回 False
        """
        skill_config = self.skills.get(skill_name)
        if skill_config is None:
            # `public` 与 `custom` 类别默认启用
            return skill_category in ("public", "custom")
        return skill_config.enabled


_extensions_config: ExtensionsConfig | None = None


def get_extensions_config() -> ExtensionsConfig:
    """获取扩展配置实例。

    返回缓存的单例实例。可通过 `reload_extensions_config()` 从文件重载，
    或通过 `reset_extensions_config()` 清空缓存。

    返回：
        缓存的 ExtensionsConfig 实例。
    """
    global _extensions_config
    if _extensions_config is None:
        _extensions_config = ExtensionsConfig.from_file()
    return _extensions_config


def reload_extensions_config(config_path: str | None = None) -> ExtensionsConfig:
    """从文件重载扩展配置并更新缓存实例。

    当扩展配置文件已修改且希望在不重启应用的情况下生效时可使用该方法。

    参数：
        config_path: 可选的扩展配置文件路径。未提供时使用默认解析策略。

    返回：
        新加载的 ExtensionsConfig 实例。
    """
    global _extensions_config
    _extensions_config = ExtensionsConfig.from_file(config_path)
    return _extensions_config


def reset_extensions_config() -> None:
    """重置缓存中的扩展配置实例。

    该操作会清空单例缓存，使下一次调用 `get_extensions_config()` 时重新从文件加载。
    适用于测试场景或在不同配置之间切换时使用。
    """
    global _extensions_config
    _extensions_config = None


def set_extensions_config(config: ExtensionsConfig) -> None:
    """设置自定义扩展配置实例。

    可用于在测试场景中注入自定义或 mock 配置。

    参数：
        config: 要使用的 ExtensionsConfig 实例。
    """
    global _extensions_config
    _extensions_config = config
