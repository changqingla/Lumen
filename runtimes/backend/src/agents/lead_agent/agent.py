import logging

from langchain.agents import create_agent
from langchain.agents.middleware import SummarizationMiddleware
from langchain_core.runnables import RunnableConfig

from src.agents.lead_agent.prompt import apply_prompt_template
from src.agents.middlewares.clarification_middleware import ClarificationMiddleware
from src.agents.middlewares.dangling_tool_call_middleware import DanglingToolCallMiddleware
from src.agents.middlewares.memory_middleware import MemoryMiddleware
from src.agents.middlewares.subagent_limit_middleware import SubagentLimitMiddleware
from src.agents.middlewares.thread_data_middleware import ThreadDataMiddleware
from src.agents.middlewares.title_middleware import TitleMiddleware
from src.agents.middlewares.todo_middleware import TodoMiddleware
from src.agents.middlewares.tool_call_loop_guard_middleware import ToolCallLoopGuardMiddleware
from src.agents.middlewares.uploads_middleware import UploadsMiddleware
from src.agents.middlewares.view_image_middleware import ViewImageMiddleware
from src.agents.thread_state import ThreadState
from src.config.agents_config import load_agent_config
from src.config.app_config import get_app_config
from src.config.summarization_config import get_summarization_config
from src.models import create_chat_model
from src.models.factory import create_chat_model_from_spec
from src.models.resolver import dump_resolved_chat_model_spec, resolve_chat_model_spec
from src.sandbox.middleware import SandboxMiddleware

logger = logging.getLogger(__name__)


def _resolve_model_name(requested_model_name: str | None = None) -> str:
    """解析运行时模型名；若无效则直接报错。"""
    app_config = get_app_config()
    default_model_name = app_config.models[0].name if app_config.models else None
    if default_model_name is None:
        raise ValueError("No chat models are configured. Please configure at least one model in config.yaml.")

    if requested_model_name and app_config.get_model_config(requested_model_name):
        return requested_model_name

    if requested_model_name:
        raise ValueError(
            f"Model '{requested_model_name}' not found in config.yaml. "
            f"Available models: {', '.join(model.name for model in app_config.models)}."
        )
    return default_model_name


def _create_summarization_middleware() -> SummarizationMiddleware | None:
    """根据配置创建并初始化摘要中间件。"""
    config = get_summarization_config()

    if not config.enabled:
        return None

    # 准备 trigger 参数
    trigger = None
    if config.trigger is not None:
        if isinstance(config.trigger, list):
            trigger = [t.to_tuple() for t in config.trigger]
        else:
            trigger = config.trigger.to_tuple()

    # 准备 keep 参数
    keep = config.keep.to_tuple()

    # 准备 model 参数
    if config.model_name:
        model = config.model_name
    else:
        # 使用轻量模型执行摘要以节省成本
        # 若未显式指定则回退到默认模型
        model = create_chat_model(thinking_enabled=False)

    # 组装 kwargs
    kwargs = {
        "model": model,
        "trigger": trigger,
        "keep": keep,
    }

    if config.trim_tokens_to_summarize is not None:
        kwargs["trim_tokens_to_summarize"] = config.trim_tokens_to_summarize

    if config.summary_prompt is not None:
        kwargs["summary_prompt"] = config.summary_prompt

    return SummarizationMiddleware(**kwargs)


def _create_todo_list_middleware(is_plan_mode: bool) -> TodoMiddleware | None:
    """创建 TodoList 中间件（仅在计划模式启用时生效）。

    参数：
        is_plan_mode: 是否启用计划模式。

    返回：
        若启用计划模式则返回 TodoMiddleware 实例，否则返回 None。
    """
    if not is_plan_mode:
        return None

    system_prompt = """
<todo_list_system>
You have access to the `write_todos` tool to help you manage and track complex multi-step objectives.

**CRITICAL RULES:**
- Mark todos as completed IMMEDIATELY after finishing each step - do NOT batch completions
- Keep EXACTLY ONE task as `in_progress` at any time (unless tasks can run in parallel)
- Update the todo list in REAL-TIME as you work - this gives users visibility into your progress
- DO NOT use this tool for simple tasks (< 3 steps) - just complete them directly

**When to Use:**
This tool is designed for complex objectives that require systematic tracking:
- Complex multi-step tasks requiring 3+ distinct steps
- Non-trivial tasks needing careful planning and execution
- User explicitly requests a todo list
- User provides multiple tasks (numbered or comma-separated list)
- The plan may need revisions based on intermediate results

**When NOT to Use:**
- Single, straightforward tasks
- Trivial tasks (< 3 steps)
- Purely conversational or informational requests
- Simple tool calls where the approach is obvious

**Best Practices:**
- Break down complex tasks into smaller, actionable steps
- Use clear, descriptive task names
- Remove tasks that become irrelevant
- Add new tasks discovered during implementation
- Don't be afraid to revise the todo list as you learn more

**Task Management:**
Writing todos takes time and tokens - use it when helpful for managing complex problems, not for simple requests.
</todo_list_system>
"""

    tool_description = """

**IMPORTANT: Only use this tool for complex tasks (3+ steps). For simple requests, just do the work directly.**

## When to Use

Use this tool in these scenarios:
1. **Complex multi-step tasks**: When a task requires 3 or more distinct steps or actions
2. **Non-trivial tasks**: Tasks requiring careful planning or multiple operations
3. **User explicitly requests todo list**: When the user directly asks you to track tasks
4. **Multiple tasks**: When users provide a list of things to be done
5. **Dynamic planning**: When the plan may need updates based on intermediate results

## When NOT to Use

Skip this tool when:
1. The task is straightforward and takes less than 3 steps
2. The task is trivial and tracking provides no benefit
3. The task is purely conversational or informational
4. It's clear what needs to be done and you can just do it

## How to Use

1. **Starting a task**: Mark it as `in_progress` BEFORE beginning work
2. **Completing a task**: Mark it as `completed` IMMEDIATELY after finishing
3. **Updating the list**: Add new tasks, remove irrelevant ones, or update descriptions as needed
4. **Multiple updates**: You can make several updates at once (e.g., complete one task and start the next)

## Task States

- `pending`: Task not yet started
- `in_progress`: Currently working on (can have multiple if tasks run in parallel)
- `completed`: Task finished successfully

## Task Completion Requirements

**CRITICAL: Only mark a task as completed when you have FULLY accomplished it.**

Never mark a task as completed if:
- There are unresolved issues or errors
- Work is partial or incomplete
- You encountered blockers preventing completion
- You couldn't find necessary resources or dependencies
- Quality standards haven't been met

If blocked, keep the task as `in_progress` and create a new task describing what needs to be resolved.

## Best Practices

- Create specific, actionable items
- Break complex tasks into smaller, manageable steps
- Use clear, descriptive task names
- Update task status in real-time as you work
- Mark tasks complete IMMEDIATELY after finishing (don't batch completions)
- Remove tasks that are no longer relevant
- **IMPORTANT**: When you write the todo list, mark your first task(s) as `in_progress` immediately
- **IMPORTANT**: Unless all tasks are completed, always have at least one task `in_progress` to show progress

Being proactive with task management demonstrates thoroughness and ensures all requirements are completed successfully.

**Remember**: If you only need a few tool calls to complete a task and it's clear what to do, it's better to just do the task directly and NOT use this tool at all.
"""

    return TodoMiddleware(system_prompt=system_prompt, tool_description=tool_description)


# 线程数据中间件（ThreadDataMiddleware）必须位于 SandboxMiddleware 之前，确保 thread_id 可用
# 上传中间件（UploadsMiddleware）应放在 ThreadDataMiddleware 之后，便于访问 thread_id
# 悬空工具调用中间件（DanglingToolCallMiddleware）会在模型读取历史前补齐缺失的 ToolMessage
# 摘要中间件（SummarizationMiddleware）应尽量靠前，以先行压缩上下文
# 待办清单中间件（TodoListMiddleware）应在 ClarificationMiddleware 之前，便于任务清单管理
# 标题中间件（TitleMiddleware）在首轮交互后生成标题
# 记忆中间件（MemoryMiddleware）在 TitleMiddleware 之后排队触发记忆更新
# 图像查看中间件（ViewImageMiddleware）应在 ClarificationMiddleware 之前注入图像细节
# 澄清中间件（ClarificationMiddleware）应始终置于末尾以拦截澄清请求
def _build_middlewares(
    config: RunnableConfig,
    model_name: str | None,
    *,
    supports_vision: bool = False,
    agent_name: str | None = None,
):
    """构建 Lead Agent 的中间件链。

    参数：
        config: 运行时配置，包含如 `is_plan_mode` 等可配置项。
        agent_name: 若提供，MemoryMiddleware 将使用按 agent 隔离的记忆存储。

    返回：
        中间件实例列表。
    """
    app_config = get_app_config()
    middlewares = [
        ThreadDataMiddleware(),
        UploadsMiddleware(),
        SandboxMiddleware(),
        ToolCallLoopGuardMiddleware(max_identical_calls=app_config.agent_loop.max_identical_tool_calls),
        DanglingToolCallMiddleware(),
    ]

    # 若启用摘要，则加入摘要中间件
    summarization_middleware = _create_summarization_middleware()
    if summarization_middleware is not None:
        middlewares.append(summarization_middleware)

    # 若启用计划模式，则加入 TodoList 中间件
    is_plan_mode = config.get("configurable", {}).get("is_plan_mode", False)
    todo_list_middleware = _create_todo_list_middleware(is_plan_mode)
    if todo_list_middleware is not None:
        middlewares.append(todo_list_middleware)

    # 加入 TitleMiddleware
    middlewares.append(TitleMiddleware())

    # 加入 MemoryMiddleware（位于 TitleMiddleware 之后）
    middlewares.append(MemoryMiddleware(agent_name=agent_name))

    # 仅当当前模型支持视觉能力时才加入 ViewImageMiddleware。
    if supports_vision:
        middlewares.append(ViewImageMiddleware())

    # 加入 SubagentLimitMiddleware，截断超出并发上限的子代理调用
    subagent_enabled = config.get("configurable", {}).get("subagent_enabled", False)
    if subagent_enabled:
        max_concurrent_subagents = config.get("configurable", {}).get("max_concurrent_subagents", 3)
        middlewares.append(SubagentLimitMiddleware(max_concurrent=max_concurrent_subagents))

    # 澄清中间件（ClarificationMiddleware）始终放在最后
    middlewares.append(ClarificationMiddleware())
    return middlewares


def make_lead_agent(config: RunnableConfig):
    # 延迟导入，避免循环依赖
    from src.tools import get_available_tools
    from src.tools.builtins import setup_agent

    cfg = config.get("configurable", {})

    thinking_enabled = cfg.get("thinking_enabled", True)
    reasoning_effort = cfg.get("reasoning_effort", None)
    requested_model_name: str | None = cfg.get("model_name") or cfg.get("model")
    dynamic_model_token: str | None = cfg.get("dynamic_model_token")
    thread_id: str | None = cfg.get("thread_id")
    is_plan_mode = cfg.get("is_plan_mode", False)
    subagent_enabled = cfg.get("subagent_enabled", False)
    max_concurrent_subagents = cfg.get("max_concurrent_subagents", 3)
    is_bootstrap = cfg.get("is_bootstrap", False)
    agent_name = cfg.get("agent_name")

    agent_config = load_agent_config(agent_name) if not is_bootstrap else None
    # 使用自定义 agent 模型；若未配置则回退到全局/默认模型解析结果
    agent_model_name = agent_config.model if agent_config and agent_config.model else _resolve_model_name()

    # 最终模型名优先级：请求覆盖 > agent 配置 > 全局默认
    model_name = requested_model_name or agent_model_name

    try:
        model_spec = resolve_chat_model_spec(
            model_name,
            dynamic_model_token=dynamic_model_token,
            thread_id=thread_id,
        )
    except Exception as exc:
        logger.exception("Failed to resolve chat model spec for model '%s'", model_name)
        raise ValueError(
            "No chat model could be resolved. Please configure at least one model in config.yaml "
            "or provide a valid model binding."
        ) from exc

    if model_spec is None:
        raise ValueError("No chat model could be resolved. Please configure at least one model in config.yaml or provide a valid 'model_name'/'model' in the request.")
    if thinking_enabled and not model_spec.supports_thinking:
        logger.warning(f"Thinking mode is enabled but model '{model_spec.name}' does not support it; fallback to non-thinking mode.")
        thinking_enabled = False

    effective_reasoning_effort = reasoning_effort
    if effective_reasoning_effort is None and model_spec.supports_reasoning_effort:
        config_reasoning_effort = model_spec.config.get("reasoning_effort")
        if config_reasoning_effort is not None:
            effective_reasoning_effort = config_reasoning_effort

    logger.info(
        "Create Agent(%s) -> thinking_enabled: %s, reasoning_effort: %s, model_name: %s, is_plan_mode: %s, subagent_enabled: %s, max_concurrent_subagents: %s",
        agent_name or "default",
        thinking_enabled,
        effective_reasoning_effort,
        model_spec.name,
        is_plan_mode,
        subagent_enabled,
        max_concurrent_subagents,
    )

    # 注入运行元数据，用于 LangSmith 链路追踪标记
    if "metadata" not in config:
        config["metadata"] = {}

    config["metadata"].update(
        {
            "agent_name": agent_name or "default",
            "model_name": model_spec.name or "default",
            "thinking_enabled": thinking_enabled,
            "reasoning_effort": effective_reasoning_effort,
            "is_plan_mode": is_plan_mode,
            "subagent_enabled": subagent_enabled,
            "dynamic_model_token": dynamic_model_token,
            "resolved_model_spec": dump_resolved_chat_model_spec(model_spec),
        }
    )

    if is_bootstrap:
        # 引导阶段专用 agent：使用最小提示词完成初始自定义 agent 创建流程
        system_prompt = apply_prompt_template(subagent_enabled=subagent_enabled, max_concurrent_subagents=max_concurrent_subagents, available_skills=set(["bootstrap"]))

        return create_agent(
            model=create_chat_model_from_spec(model_spec, thinking_enabled=thinking_enabled),
            tools=get_available_tools(
                model_name=model_spec.name,
                dynamic_model_token=dynamic_model_token,
                thread_id=thread_id,
                supports_vision_override=model_spec.supports_vision,
                subagent_enabled=subagent_enabled,
            ) + [setup_agent],
            middleware=_build_middlewares(
                config,
                model_name=model_spec.name,
                supports_vision=model_spec.supports_vision,
            ),
            system_prompt=system_prompt,
            state_schema=ThreadState,
        )

    # 默认 lead agent（保持原有行为）
    model_kwargs = {}
    if reasoning_effort is not None:
        model_kwargs["reasoning_effort"] = reasoning_effort

    return create_agent(
        model=create_chat_model_from_spec(
            model_spec,
            thinking_enabled=thinking_enabled,
            **model_kwargs,
        ),
        tools=get_available_tools(
            model_name=model_spec.name,
            dynamic_model_token=dynamic_model_token,
            thread_id=thread_id,
            supports_vision_override=model_spec.supports_vision,
            groups=agent_config.tool_groups if agent_config else None,
            subagent_enabled=subagent_enabled,
        ),
        middleware=_build_middlewares(
            config,
            model_name=model_spec.name,
            supports_vision=model_spec.supports_vision,
            agent_name=agent_name,
        ),
        system_prompt=apply_prompt_template(subagent_enabled=subagent_enabled, max_concurrent_subagents=max_concurrent_subagents, agent_name=agent_name),
        state_schema=ThreadState,
    )
