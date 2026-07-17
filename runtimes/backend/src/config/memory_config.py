"""记忆机制配置。"""

from pydantic import BaseModel, Field


class MemoryConfig(BaseModel):
    """Tenant-scoped long-term memory configuration."""

    enabled: bool = Field(
        default=True,
        description="是否启用记忆机制",
    )
    storage_path: str = Field(
        default="",
        description=(
            "Scoped memory storage root. If omitted, profiles are stored under "
            "`{base_dir}/memories/{memory_scope}/`. A historical JSON filename "
            "is treated only as a location hint and scoped data is written to a "
            "distinct `<filename>.scoped/` directory. Legacy global files are "
            "never loaded or automatically migrated. Relative paths are resolved "
            "under `Paths.base_dir` and may not escape it."
        ),
    )
    debounce_seconds: int = Field(
        default=30,
        ge=1,
        le=300,
        description="处理排队更新前的等待秒数（防抖）",
    )
    model_name: str | None = Field(
        default=None,
        description="用于记忆更新的模型名称（None = 使用默认模型）",
    )
    max_facts: int = Field(
        default=100,
        ge=10,
        le=500,
        description="最多存储的事实数量",
    )
    fact_confidence_threshold: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description="存储事实所需的最低置信度阈值",
    )
    injection_enabled: bool = Field(
        default=True,
        description="是否将记忆注入系统提示词",
    )
    max_injection_tokens: int = Field(
        default=2000,
        ge=100,
        le=8000,
        description="记忆注入可使用的最大 token 数",
    )


# 全局配置实例
_memory_config: MemoryConfig = MemoryConfig()


def get_memory_config() -> MemoryConfig:
    """获取当前记忆配置。"""
    return _memory_config


def load_memory_config_from_dict(config_dict: dict) -> None:
    """从字典加载记忆配置。"""
    global _memory_config
    _memory_config = MemoryConfig(**config_dict)
