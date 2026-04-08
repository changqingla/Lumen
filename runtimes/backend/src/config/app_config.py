import os
from pathlib import Path
from typing import Any, Self

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field

from src.config.agent_loop_config import AgentLoopConfig
from src.config.checkpointer_config import CheckpointerConfig, load_checkpointer_config_from_dict
from src.config.extensions_config import ExtensionsConfig
from src.config.memory_config import load_memory_config_from_dict
from src.config.model_config import ModelConfig
from src.config.sandbox_config import SandboxConfig
from src.config.skills_config import SkillsConfig
from src.config.subagents_config import load_subagents_config_from_dict
from src.config.summarization_config import load_summarization_config_from_dict
from src.config.title_config import load_title_config_from_dict
from src.config.tool_config import ToolConfig, ToolGroupConfig
from src.config.uploads_config import UploadsConfig

_RUNTIMES_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_CONFIG_DIR = _RUNTIMES_ROOT / "config"
_DEFAULT_CONFIG_PATH = _DEFAULT_CONFIG_DIR / "config.yaml"

load_dotenv(_DEFAULT_CONFIG_DIR / ".env")


class AppConfig(BaseModel):
    """用于 lumen 项目的应用级配置。"""

    models: list[ModelConfig] = Field(default_factory=list, description="可用模型列表")
    sandbox: SandboxConfig = Field(description="沙箱配置")
    tools: list[ToolConfig] = Field(default_factory=list, description="可用工具列表")
    tool_groups: list[ToolGroupConfig] = Field(default_factory=list, description="可用工具组列表")
    uploads: UploadsConfig = Field(default_factory=UploadsConfig, description="文件上传配置")
    agent_loop: AgentLoopConfig = Field(default_factory=AgentLoopConfig, description="Agent 主循环配置")
    skills: SkillsConfig = Field(default_factory=SkillsConfig, description="技能配置")
    extensions: ExtensionsConfig = Field(default_factory=ExtensionsConfig, description="扩展配置（MCP 服务器与技能状态）")
    model_config = ConfigDict(extra="allow", frozen=False)
    checkpointer: CheckpointerConfig | None = Field(default=None, description="Checkpointer 配置")

    @classmethod
    def resolve_config_path(cls, config_path: str | None = None) -> Path:
        """解析配置文件路径。

        优先级：
        1. 若传入 `config_path` 参数，则使用该路径。
        2. 若设置 `LUMEN_CONFIG_PATH` 环境变量，则使用该路径。
        3. 否则先检查当前目录下的 `config.yaml`，若不存在再回退到父目录下的 `config.yaml`。
        """
        if config_path:
            path = Path(config_path)
            if not Path.exists(path):
                raise FileNotFoundError(f"Config file specified by param `config_path` not found at {path}")
            return path
        elif os.getenv("LUMEN_CONFIG_PATH"):
            path = Path(os.getenv("LUMEN_CONFIG_PATH"))
            if not Path.exists(path):
                raise FileNotFoundError(f"环境变量 `LUMEN_CONFIG_PATH` 指定的配置文件不存在：{path}")
            return path
        else:
            candidates = [
                Path(os.getcwd()) / "config.yaml",
                Path(os.getcwd()).parent / "config.yaml",
                _DEFAULT_CONFIG_PATH,
            ]
            for path in candidates:
                if path.exists():
                    return path
            raise FileNotFoundError(
                "`config.yaml` file not found at the current directory, its parent directory, or the default runtimes/config directory"
            )

    @classmethod
    def from_file(cls, config_path: str | None = None) -> Self:
        """从 YAML 文件加载配置。

        详细路径解析规则见 `resolve_config_path`。

        参数：
            config_path: 配置文件路径。

        返回：
            AppConfig: 解析后的配置对象。
        """
        resolved_path = cls.resolve_config_path(config_path)
        with open(resolved_path, encoding="utf-8") as f:
            config_data = yaml.safe_load(f)
        config_data = cls.resolve_env_variables(config_data)

        # 若存在 title 配置，则加载标题配置
        if "title" in config_data:
            load_title_config_from_dict(config_data["title"])

        # 若存在 summarization 配置，则加载摘要配置
        if "summarization" in config_data:
            load_summarization_config_from_dict(config_data["summarization"])

        # 若存在 memory 配置，则加载记忆配置
        if "memory" in config_data:
            load_memory_config_from_dict(config_data["memory"])

        # 若存在 subagents 配置，则加载子代理配置
        if "subagents" in config_data:
            load_subagents_config_from_dict(config_data["subagents"])

        # 若存在 checkpointer 配置，则加载检查点配置
        if "checkpointer" in config_data:
            load_checkpointer_config_from_dict(config_data["checkpointer"])

        # 单独加载 extensions 配置（位于不同文件）
        extensions_config = ExtensionsConfig.from_file()
        config_data["extensions"] = extensions_config.model_dump()

        result = cls.model_validate(config_data)
        return result

    @classmethod
    def resolve_env_variables(cls, config: Any) -> Any:
        """递归解析配置中的环境变量。

        环境变量通过 `os.getenv` 解析，例如：`$OPENAI_API_KEY`。

        参数：
            config: 待解析环境变量的配置对象。

        返回：
            已解析环境变量后的配置对象。
        """
        if isinstance(config, str):
            if config.startswith("$"):
                env_value = os.getenv(config[1:])
                if env_value is None:
                    raise ValueError(f"Environment variable {config[1:]} not found for config value {config}")
                return env_value
            return config
        elif isinstance(config, dict):
            return {k: cls.resolve_env_variables(v) for k, v in config.items()}
        elif isinstance(config, list):
            return [cls.resolve_env_variables(item) for item in config]
        return config

    def get_model_config(self, name: str) -> ModelConfig | None:
        """按名称获取模型配置。

        参数：
            name: 要查找的模型名称。

        返回：
            找到则返回模型配置，否则返回 None。
        """
        return next((model for model in self.models if model.name == name), None)

    def get_tool_config(self, name: str) -> ToolConfig | None:
        """按名称获取工具配置。

        参数：
            name: 要查找的工具名称。

        返回：
            找到则返回工具配置，否则返回 None。
        """
        return next((tool for tool in self.tools if tool.name == name), None)

    def get_tool_group_config(self, name: str) -> ToolGroupConfig | None:
        """按名称获取工具组配置。

        参数：
            name: 要查找的工具组名称。

        返回：
            找到则返回工具组配置，否则返回 None。
        """
        return next((group for group in self.tool_groups if group.name == name), None)


_app_config: AppConfig | None = None


def get_app_config() -> AppConfig:
    """获取 lumen 配置实例。

    返回缓存的单例实例。可通过 `reload_app_config()` 从文件重载，
    或通过 `reset_app_config()` 清空缓存。
    """
    global _app_config
    if _app_config is None:
        _app_config = AppConfig.from_file()
    return _app_config


def reload_app_config(config_path: str | None = None) -> AppConfig:
    """从文件重新加载配置并更新缓存实例。

    当配置文件已修改且希望在不重启应用的情况下生效时可使用该方法。

    参数：
        config_path: 可选的配置文件路径。未提供时使用默认解析策略。

    返回：
        新加载的 AppConfig 实例。
    """
    global _app_config
    _app_config = AppConfig.from_file(config_path)
    return _app_config


def reset_app_config() -> None:
    """重置缓存中的配置实例。

    该操作会清空单例缓存，使下一次调用 `get_app_config()` 时重新从文件加载。
    适用于测试场景或在不同配置之间切换时使用。
    """
    global _app_config
    _app_config = None


def set_app_config(config: AppConfig) -> None:
    """设置自定义配置实例。

    可用于在测试场景中注入自定义或 mock 配置。

    参数：
        config: 要使用的 AppConfig 实例。
    """
    global _app_config
    _app_config = config
