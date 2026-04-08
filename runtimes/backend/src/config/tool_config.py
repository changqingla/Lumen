from pydantic import BaseModel, ConfigDict, Field


class ToolGroupConfig(BaseModel):
    """工具组配置项。"""

    name: str = Field(..., description="Unique name for the tool group")
    model_config = ConfigDict(extra="allow")


class ToolConfig(BaseModel):
    """工具配置项。"""

    name: str = Field(..., description="Unique name for the tool")
    group: str = Field(..., description="Group name for the tool")
    use: str = Field(
        ...,
        description="Variable name of the tool provider(e.g. src.sandbox.tools:bash_tool)",
    )
    model_config = ConfigDict(extra="allow")
