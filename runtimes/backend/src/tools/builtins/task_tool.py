"""用于将任务委派给子代理的工具。"""

import logging
import time
import uuid
from dataclasses import replace
from typing import Annotated, Literal

from langchain.tools import InjectedToolCallId, ToolRuntime, tool
from langgraph.config import get_stream_writer
from langgraph.typing import ContextT

from src.agents.lead_agent.prompt import get_skills_prompt_section
from src.agents.thread_state import ThreadState
from src.subagents import SubagentExecutor, get_subagent_config
from src.subagents.executor import SubagentStatus, cleanup_background_task, get_background_task_result
from src.usage import retain_run_reservation

logger = logging.getLogger(__name__)


@tool("task", parse_docstring=False)
def task_tool(
    runtime: ToolRuntime[ContextT, ThreadState],
    description: str,
    prompt: str,
    subagent_type: Literal["general-purpose", "bash"],
    tool_call_id: Annotated[str, InjectedToolCallId],
    max_turns: int | None = None,
) -> str:
    """将任务委派给在独立上下文中运行的专用子代理。

    子代理可帮助你：
    - 将探索与实现拆分，降低主上下文污染
    - 自主处理复杂多步骤任务
    - 在隔离上下文中执行命令或操作

    可用子代理类型：
    - **general-purpose**：适合需要探索与执行并重的复杂多步骤任务。
      当任务需要复杂推理、多个依赖步骤，或适合隔离上下文时使用。
    - **bash**：命令执行型子代理，适合运行 bash 命令。
      可用于 git 操作、构建流程，或命令输出较冗长的场景。

    适用场景：
    - 需要多步骤或多工具协作的复杂任务
    - 可能产生大量输出的任务
    - 希望与主对话隔离上下文的任务
    - 并行研究或探索任务

    不适用场景：
    - 简单单步操作（直接调用工具即可）
    - 需要用户实时交互或澄清的任务

    参数：
        description: 任务简述（3-5 个词）用于日志/展示。必须第一个提供。
        prompt: 给子代理的任务说明。请明确、具体。必须第二个提供。
        subagent_type: 子代理类型。必须第三个提供。
        max_turns: 可选最大轮数。默认使用子代理配置值。
    """
    # 获取子代理配置
    config = get_subagent_config(subagent_type)
    if config is None:
        return f"Error: Unknown subagent type '{subagent_type}'. Available: general-purpose, bash"

    # 构造配置覆盖项
    overrides: dict = {}

    skills_section = get_skills_prompt_section()
    if skills_section:
        overrides["system_prompt"] = config.system_prompt + "\n\n" + skills_section

    if max_turns is not None:
        overrides["max_turns"] = max_turns

    if overrides:
        config = replace(config, **overrides)

    # 从 runtime 提取父级上下文
    sandbox_state = None
    thread_data = None
    thread_id = None
    parent_model = None
    parent_dynamic_model_token = None
    usage_context = None
    trace_id = None

    if runtime is not None:
        sandbox_state = runtime.state.get("sandbox")
        thread_data = runtime.state.get("thread_data")
        thread_id = runtime.context.get("thread_id")
        candidate_usage_context = runtime.context.get("usage_context")
        if isinstance(candidate_usage_context, str) and candidate_usage_context.strip():
            usage_context = candidate_usage_context.strip()

        # metadata 只携带无密的追踪字段。动态绑定令牌只从本次运行的
        # configurable 读取，解析后的含密模型规格不再跨代理传递。
        metadata = runtime.config.get("metadata", {})
        configurable = runtime.config.get("configurable", {})
        parent_model = metadata.get("model_name") or configurable.get("model_name") or configurable.get("model")
        parent_dynamic_model_token = configurable.get("dynamic_model_token")

        # 获取或生成用于分布式追踪的 trace_id
        trace_id = metadata.get("trace_id") or str(uuid.uuid4())[:8]

    # 获取可用工具（排除 task 自身，避免嵌套调用）
    # 延迟导入以避免循环依赖
    from src.tools import get_available_tools

    # 子代理不应再启用子代理工具（防止递归嵌套）
    tools = get_available_tools(
        model_name=parent_model,
        dynamic_model_token=parent_dynamic_model_token,
        thread_id=thread_id,
        supports_vision_override=None,
        subagent_enabled=False,
    )

    # 创建执行器
    executor = SubagentExecutor(
        config=config,
        tools=tools,
        parent_model=parent_model,
        parent_dynamic_model_token=parent_dynamic_model_token,
        sandbox_state=sandbox_state,
        thread_data=thread_data,
        thread_id=thread_id,
        trace_id=trace_id,
        usage_context=usage_context,
    )

    # 启动后台执行（始终异步，避免阻塞）
    # 使用 tool_call_id 作为 task_id，提升可追踪性
    task_id = executor.execute_async(prompt, task_id=tool_call_id)

    # 由后端轮询任务完成状态（无需 LLM 自行轮询）
    poll_count = 0
    last_status = None
    last_message_count = 0  # 记录已发送的 AI 消息数量
    # 轮询超时：执行超时 + 60 秒缓冲；每 5 秒检查一次
    max_poll_count = (config.timeout_seconds + 60) // 5

    logger.info(f"[trace={trace_id}] Started background task {task_id} (subagent={subagent_type}, timeout={config.timeout_seconds}s, polling_limit={max_poll_count} polls)")

    writer = get_stream_writer()
    # 发送任务开始消息
    writer({"type": "task_started", "task_id": task_id, "description": description})

    while True:
        result = get_background_task_result(task_id)

        if result is None:
            logger.error(f"[trace={trace_id}] Task {task_id} not found in background tasks")
            writer({"type": "task_failed", "task_id": task_id, "error": "Task disappeared from background tasks"})
            cleanup_background_task(task_id)
            return f"Error: Task {task_id} disappeared from background tasks"

        # 记录状态变化，便于调试
        if result.status != last_status:
            logger.info(f"[trace={trace_id}] Task {task_id} status: {result.status.value}")
            last_status = result.status

        # 检查是否有新 AI 消息，并发送 task_running 事件
        current_message_count = len(result.ai_messages)
        if current_message_count > last_message_count:
            # 为每条新增消息发送 task_running 事件
            for i in range(last_message_count, current_message_count):
                message = result.ai_messages[i]
                writer(
                    {
                        "type": "task_running",
                        "task_id": task_id,
                        "message": message,
                        "message_index": i + 1,  # 面向展示的 1-based 索引
                        "total_messages": current_message_count,
                    }
                )
                logger.info(f"[trace={trace_id}] Task {task_id} sent message #{i + 1}/{current_message_count}")
            last_message_count = current_message_count

        # 检查任务是否完成、失败或超时
        if result.status == SubagentStatus.COMPLETED:
            writer({"type": "task_completed", "task_id": task_id, "result": result.result})
            logger.info(f"[trace={trace_id}] Task {task_id} completed after {poll_count} polls")
            cleanup_background_task(task_id)
            return f"Task Succeeded. Result: {result.result}"
        elif result.status == SubagentStatus.FAILED:
            writer({"type": "task_failed", "task_id": task_id, "error": result.error})
            logger.error(f"[trace={trace_id}] Task {task_id} failed: {result.error}")
            cleanup_background_task(task_id)
            return f"Task failed. Error: {result.error}"
        elif result.status == SubagentStatus.TIMED_OUT:
            writer({"type": "task_timed_out", "task_id": task_id, "error": result.error})
            logger.warning(f"[trace={trace_id}] Task {task_id} timed out: {result.error}")
            cleanup_background_task(task_id)
            return f"Task timed out. Error: {result.error}"

        # 仍在运行，等待下一轮轮询
        time.sleep(5)  # 每 5 秒轮询一次
        poll_count += 1

        # 轮询超时作为兜底保护（防止线程池超时机制失效）
        # 阈值为执行超时 + 60 秒缓冲，以 5 秒为轮询间隔
        # 用于兜住后台任务卡死等边缘场景
        # 注意：这里不调用 cleanup_background_task，因为任务可能仍在后台运行。
        # 真正清理会在执行器结束并写入终态后完成。
        if poll_count > max_poll_count:
            timeout_minutes = config.timeout_seconds // 60
            retain_run_reservation(getattr(runtime, "context", None))
            logger.error(f"[trace={trace_id}] Task {task_id} polling timed out after {poll_count} polls (should have been caught by thread pool timeout)")
            writer({"type": "task_timed_out", "task_id": task_id})
            return f"Task polling timed out after {timeout_minutes} minutes. This may indicate the background task is stuck. Status: {result.status.value}"
