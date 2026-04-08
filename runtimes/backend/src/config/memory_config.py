"""记忆机制配置。"""

from pydantic import BaseModel, Field


class MemoryConfig(BaseModel):
    """全局记忆机制配置。"""

    enabled: bool = Field(
        default=True,
        description="是否启用记忆机制",
    )
    storage_path: str = Field(
        default="",
        description=(
            "用于存储记忆数据的路径。"
            "若为空，默认使用 `{base_dir}/memory.json`（见 Paths.memory_file）。"
            "绝对路径会原样使用。"
            "相对路径将基于 `Paths.base_dir` 解析"
            "（而非 backend 的工作目录）。"
            "注意：若你此前设置为 `.lumen/memory.json`，"
            "现在会解析为 `{base_dir}/.lumen/memory.json`；"
            "请迁移已有数据，或使用绝对路径以保持旧位置。"
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


def set_memory_config(config: MemoryConfig) -> None:
    """设置记忆配置。"""
    global _memory_config
    _memory_config = config


def load_memory_config_from_dict(config_dict: dict) -> None:
    """从字典加载记忆配置。"""
    global _memory_config
    _memory_config = MemoryConfig(**config_dict)
