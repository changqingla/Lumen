"""子代理配置定义。"""

from dataclasses import dataclass, field


@dataclass
class SubagentConfig:
    """子代理配置项。

    属性：
        name: 子代理唯一标识。
        description: 何时应将任务委派给该子代理。
        system_prompt: 指导子代理行为的系统提示词。
        tools: 允许工具名白名单；为 None 时继承全部工具。
        disallowed_tools: 拒绝工具名列表。
        model: 使用的模型；`"inherit"` 表示继承父代理模型。
        max_turns: 最大代理轮数。
        timeout_seconds: 最大执行时长（秒，默认 900 即 15 分钟）。
    """

    name: str
    description: str
    system_prompt: str
    tools: list[str] | None = None
    disallowed_tools: list[str] | None = field(default_factory=lambda: ["task"])
    model: str = "inherit"
    max_turns: int = 50
    timeout_seconds: int = 900
