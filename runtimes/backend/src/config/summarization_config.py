"""会话摘要配置。"""

from typing import Literal

from pydantic import BaseModel, Field

ContextSizeType = Literal["fraction", "tokens", "messages"]


class ContextSize(BaseModel):
    """用于触发条件或保留策略的上下文大小规格。"""

    type: ContextSizeType = Field(description="上下文大小规格类型")
    value: int | float = Field(description="上下文大小规格值")

    def to_tuple(self) -> tuple[ContextSizeType, int | float]:
        """转换为 SummarizationMiddleware 所需的元组格式。"""
        return (self.type, self.value)


class SummarizationConfig(BaseModel):
    """自动会话摘要配置。"""

    enabled: bool = Field(
        default=False,
        description="是否启用自动会话摘要",
    )
    model_name: str | None = Field(
        default=None,
        description="用于摘要的模型名称（None = 使用轻量默认模型）",
    )
    trigger: ContextSize | list[ContextSize] | None = Field(
        default=None,
        description="触发摘要的一个或多个阈值。满足任一阈值即执行摘要。"
        "示例：{'type': 'messages', 'value': 50} 表示消息数达到 50 时触发，"
        "{'type': 'tokens', 'value': 4000} 表示 token 达到 4000 时触发，"
        "{'type': 'fraction', 'value': 0.8} 表示达到模型最大输入 token 的 80% 时触发",
    )
    keep: ContextSize = Field(
        default_factory=lambda: ContextSize(type="messages", value=20),
        description="摘要后的上下文保留策略，用于指定保留多少历史。"
        "示例：{'type': 'messages', 'value': 20} 表示保留 20 条消息，"
        "{'type': 'tokens', 'value': 3000} 表示保留 3000 个 token，"
        "{'type': 'fraction', 'value': 0.3} 表示保留模型最大输入 token 的 30%",
    )
    trim_tokens_to_summarize: int | None = Field(
        default=4000,
        description="准备摘要消息时允许保留的最大 token 数。传入 null 可跳过裁剪。",
    )
    summary_prompt: str | None = Field(
        default=None,
        description="用于生成摘要的自定义提示词模板。未提供时使用默认 LangChain 提示词。",
    )


# 全局配置实例
_summarization_config: SummarizationConfig = SummarizationConfig()


def get_summarization_config() -> SummarizationConfig:
    """获取当前摘要配置。"""
    return _summarization_config


def set_summarization_config(config: SummarizationConfig) -> None:
    """设置摘要配置。"""
    global _summarization_config
    _summarization_config = config


def load_summarization_config_from_dict(config_dict: dict) -> None:
    """从字典加载摘要配置。"""
    global _summarization_config
    _summarization_config = SummarizationConfig(**config_dict)
