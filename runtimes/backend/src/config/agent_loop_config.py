from pydantic import BaseModel, Field


class AgentLoopConfig(BaseModel):
    """Agent 主循环相关配置。"""

    max_identical_tool_calls: int = Field(
        default=3,
        ge=1,
        description="同一轮中允许的相同工具+相同参数的最大重复调用次数。超过后会触发 loop guard。",
    )
