"""用于 LangGraph 的检查点（Checkpointer）配置。"""

from typing import Literal

from pydantic import BaseModel, Field

CheckpointerType = Literal["memory", "postgres"]


class CheckpointerConfig(BaseModel):
    """用于 LangGraph 状态持久化的检查点（Checkpointer）配置。"""

    type: CheckpointerType = Field(
        description="Checkpointer 后端类型。"
        "'memory' 仅在进程内有效（重启后丢失）。"
        "'postgres' 持久化到 PostgreSQL（需要 langgraph-checkpoint-postgres）。"
    )
    connection_string: str | None = Field(
        default=None,
        description="postgres 类型使用的 DSN 连接字符串。"
        "postgres 类型必填。"
        "postgres 示例：'postgresql://user:pass@localhost:5432/db'。",
    )


# 全局配置实例。None 表示未配置 checkpointer。
_checkpointer_config: CheckpointerConfig | None = None


def get_checkpointer_config() -> CheckpointerConfig | None:
    """获取当前 checkpointer 配置；未配置时返回 None。"""
    return _checkpointer_config


def set_checkpointer_config(config: CheckpointerConfig | None) -> None:
    """设置 checkpointer 配置。"""
    global _checkpointer_config
    _checkpointer_config = config


def load_checkpointer_config_from_dict(config_dict: dict) -> None:
    """从字典加载 checkpointer 配置。"""
    global _checkpointer_config
    _checkpointer_config = CheckpointerConfig(**config_dict)
