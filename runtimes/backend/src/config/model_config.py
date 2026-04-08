from pydantic import BaseModel, ConfigDict, Field


class ModelConfig(BaseModel):
    """模型配置项。"""

    name: str = Field(..., description="模型的唯一名称")
    display_name: str | None = Field(..., default_factory=lambda: None, description="模型展示名称")
    description: str | None = Field(..., default_factory=lambda: None, description="模型说明")
    use: str = Field(
        ...,
        description="模型提供方的类路径（例如 langchain_openai.ChatOpenAI）",
    )
    model: str = Field(..., description="模型名")
    model_config = ConfigDict(extra="allow")
    supports_thinking: bool = Field(default_factory=lambda: False, description="模型是否支持 thinking")
    supports_reasoning_effort: bool = Field(default_factory=lambda: False, description="模型是否支持 reasoning effort")
    when_thinking_enabled: dict | None = Field(
        default_factory=lambda: None,
        description="启用 thinking 时传递给模型的额外设置",
    )
    supports_vision: bool = Field(default_factory=lambda: False, description="模型是否支持视觉/图像输入")
    thinking: dict | None = Field(
        default_factory=lambda: None,
        description=(
            "模型的 thinking 设置。若提供，则在启用 thinking 时传给模型。"
            "它是 `when_thinking_enabled` 的简写；若两者同时存在，会进行合并。"
        ),
    )
