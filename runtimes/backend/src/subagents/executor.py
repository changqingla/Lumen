"""子代理执行引擎。"""

import asyncio
import logging
import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from langchain.agents import create_agent
from langchain.tools import BaseTool
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig

from src.agents.thread_state import SandboxState, ThreadDataState, ThreadState
from src.models import create_chat_model
from src.subagents.config import SubagentConfig

logger = logging.getLogger(__name__)


class SubagentStatus(Enum):
    """子代理执行状态。"""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


@dataclass
class SubagentResult:
    """子代理执行结果。

    字段：
        task_id: 本次执行唯一标识。
        trace_id: 分布式追踪 ID（关联父代理与子代理日志）。
        status: 当前执行状态。
        result: 最终结果消息（完成时）。
        error: 错误消息（失败时）。
        started_at: 执行开始时间。
        completed_at: 执行完成时间。
        ai_messages: 执行过程中生成的完整 AI 消息列表（dict）。
    """

    task_id: str
    trace_id: str
    status: SubagentStatus
    result: str | None = None
    error: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    ai_messages: list[dict[str, Any]] | None = None

    def __post_init__(self):
        """初始化可变默认字段。"""
        if self.ai_messages is None:
            self.ai_messages = []


# 后台任务结果的全局存储
_background_tasks: dict[str, SubagentResult] = {}
_background_tasks_lock = threading.Lock()

# 用于后台任务调度与编排的线程池
_scheduler_pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="subagent-scheduler-")

# 用于子代理实际执行的线程池（支持超时）
# 适当增大池大小，避免调度器提交执行任务时阻塞
_execution_pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="subagent-exec-")


def _publish_background_result(
    task_id: str,
    result: SubagentResult,
) -> bool:
    """Publish a terminal snapshot unless the polling owner already removed it."""

    with _background_tasks_lock:
        current = _background_tasks.get(task_id)
        if current is None:
            return False
        current.status = result.status
        current.result = result.result
        current.error = result.error
        current.completed_at = result.completed_at or datetime.now()
        current.ai_messages = result.ai_messages
        return True


def _publish_background_failure(task_id: str, error: str) -> bool:
    """Mark a scheduler failure without resurrecting a cleaned-up task."""

    with _background_tasks_lock:
        current = _background_tasks.get(task_id)
        if current is None:
            return False
        current.status = SubagentStatus.FAILED
        current.error = error
        current.completed_at = datetime.now()
        return True


def _filter_tools(
    all_tools: list[BaseTool],
    allowed: list[str] | None,
    disallowed: list[str] | None,
) -> list[BaseTool]:
    """根据子代理配置过滤工具列表。

    参数：
        all_tools: 全部可用工具列表。
        allowed: 可选允许名单。若提供，仅保留名单内工具。
        disallowed: 可选拒绝名单。名单内工具始终被排除。

    返回：
        过滤后的工具列表。
    """
    filtered = all_tools

    # 若配置了 allowlist，则先按允许名单过滤
    if allowed is not None:
        allowed_set = set(allowed)
        filtered = [t for t in filtered if t.name in allowed_set]

    # 再应用 denylist 过滤
    if disallowed is not None:
        disallowed_set = set(disallowed)
        filtered = [t for t in filtered if t.name not in disallowed_set]

    return filtered


def _get_model_name(config: SubagentConfig, parent_model: str | None) -> str | None:
    """解析子代理使用的模型名。

    参数：
        config: 子代理配置。
        parent_model: 父代理模型名。

    返回：
        待使用模型名；返回 None 表示使用默认模型。
    """
    if config.model == "inherit":
        return parent_model
    return config.model


def _get_dynamic_model_token(config: SubagentConfig, parent_dynamic_model_token: str | None) -> str | None:
    """在子代理继承父模型时同步继承动态模型绑定令牌。"""
    if config.model == "inherit":
        return parent_dynamic_model_token
    return None


class SubagentExecutor:
    """子代理执行器。"""

    def __init__(
        self,
        config: SubagentConfig,
        tools: list[BaseTool],
        parent_model: str | None = None,
        parent_dynamic_model_token: str | None = None,
        sandbox_state: SandboxState | None = None,
        thread_data: ThreadDataState | None = None,
        thread_id: str | None = None,
        trace_id: str | None = None,
        usage_context: str | None = None,
    ):
        """初始化执行器。

        参数：
            config: 子代理配置。
            tools: 全部可用工具（内部会再过滤）。
            parent_model: 父代理模型名（用于继承）。
            parent_dynamic_model_token: 父代理动态模型绑定令牌（用于继承自定义模型）。
            sandbox_state: 父代理沙箱状态。
            thread_data: 父代理线程数据。
            thread_id: 供沙箱操作使用的线程 ID。
            trace_id: 从父级透传的分布式追踪 ID。
        """
        self.config = config
        self.parent_model = parent_model
        self.parent_dynamic_model_token = parent_dynamic_model_token
        self.sandbox_state = sandbox_state
        self.thread_data = thread_data
        self.thread_id = thread_id
        self.usage_context = usage_context
        # 若未提供 trace_id，则自动生成（用于顶层调用）
        self.trace_id = trace_id or str(uuid.uuid4())[:8]

        # 按配置过滤工具
        self.tools = _filter_tools(
            tools,
            config.tools,
            config.disallowed_tools,
        )

        logger.info(f"[trace={self.trace_id}] SubagentExecutor initialized: {config.name} with {len(self.tools)} tools")

    def _create_agent(self):
        """创建子代理实例。"""
        model_name = _get_model_name(self.config, self.parent_model)
        dynamic_model_token = _get_dynamic_model_token(self.config, self.parent_dynamic_model_token)
        model = create_chat_model(
            name=model_name,
            thinking_enabled=False,
            dynamic_model_token=dynamic_model_token,
            thread_id=self.thread_id,
        )

        # 子代理只需最小中间件集合，确保工具可访问 sandbox 与 thread_data
        # 这些中间件会复用父代理的 sandbox/thread_data
        from src.agents.middlewares.thread_data_middleware import ThreadDataMiddleware
        from src.agents.middlewares.tool_call_loop_guard_middleware import ToolCallLoopGuardMiddleware
        from src.agents.middlewares.usage_accounting_middleware import UsageAccountingMiddleware
        from src.config.app_config import get_app_config
        from src.sandbox.middleware import SandboxMiddleware

        app_config = get_app_config()
        middlewares = [
            UsageAccountingMiddleware(request_type="subagent"),
            ThreadDataMiddleware(lazy_init=True),  # 计算线程路径
            SandboxMiddleware(lazy_init=True),  # 复用父级沙箱（不重复获取）
            ToolCallLoopGuardMiddleware(max_identical_calls=app_config.agent_loop.max_identical_tool_calls),
        ]

        return create_agent(
            model=model,
            tools=self.tools,
            middleware=middlewares,
            system_prompt=self.config.system_prompt,
            state_schema=ThreadState,
        )

    def _build_initial_state(self, task: str) -> dict[str, Any]:
        """构建代理执行的初始状态。

        参数：
            task: 任务描述。

        返回：
            初始状态字典。
        """
        state: dict[str, Any] = {
            "messages": [HumanMessage(content=task)],
        }

    # 透传父代理的沙箱与线程数据
        if self.sandbox_state is not None:
            state["sandbox"] = self.sandbox_state
        if self.thread_data is not None:
            state["thread_data"] = self.thread_data

        return state

    def _retain_usage_reservation(self) -> None:
        """Prevent finalization after an uncertain or still-running model call."""

        if not self.usage_context:
            return
        from src.usage import retain_run_reservation

        retain_run_reservation({"usage_context": self.usage_context})

    async def _aexecute(self, task: str, result_holder: SubagentResult | None = None) -> SubagentResult:
        """异步执行任务。

        参数：
            task: 提供给子代理的任务描述。
            result_holder: 可选预创建结果对象，用于执行中实时更新。

        返回：
            包含执行结果的 SubagentResult。
        """
        if result_holder is not None:
            # 使用外部传入的结果容器（支持异步实时更新）
            result = result_holder
        else:
            # 同步执行场景下创建新的结果对象
            task_id = str(uuid.uuid4())[:8]
            result = SubagentResult(
                task_id=task_id,
                trace_id=self.trace_id,
                status=SubagentStatus.RUNNING,
                started_at=datetime.now(),
            )

        try:
            agent = self._create_agent()
            state = self._build_initial_state(task)

            # 构建执行配置：注入 thread_id 供沙箱访问，并设置递归上限
            run_config: RunnableConfig = {
                "recursion_limit": self.config.max_turns,
            }
            context = {}
            if self.thread_id:
                run_config["configurable"] = {"thread_id": self.thread_id}
                context["thread_id"] = self.thread_id
            if self.usage_context:
                context["usage_context"] = self.usage_context

            logger.info(f"[trace={self.trace_id}] Subagent {self.config.name} starting async execution with max_turns={self.config.max_turns}")

            # 使用 stream 而非 invoke，以便获取实时更新
            # 这样可以在生成过程中持续收集 AI 消息
            final_state = None
            async for chunk in agent.astream(state, config=run_config, context=context, stream_mode="values"):  # type: ignore[arg-type]
                final_state = chunk

                # 从当前状态提取 AI 消息
                messages = chunk.get("messages", [])
                if messages:
                    last_message = messages[-1]
                    # 判断是否为新的 AI 消息
                    if isinstance(last_message, AIMessage):
                        # 转为 dict 便于序列化
                        message_dict = last_message.model_dump()
                        # 仅在列表中不存在时追加（避免重复）
                        # 优先按 message id 判重；无 id 时再按完整 dict 判重
                        message_id = message_dict.get("id")
                        is_duplicate = False
                        if message_id:
                            is_duplicate = any(msg.get("id") == message_id for msg in result.ai_messages)
                        else:
                            is_duplicate = message_dict in result.ai_messages

                        if not is_duplicate:
                            result.ai_messages.append(message_dict)
                            logger.info(f"[trace={self.trace_id}] Subagent {self.config.name} captured AI message #{len(result.ai_messages)}")

            logger.info(f"[trace={self.trace_id}] Subagent {self.config.name} completed async execution")

            if final_state is None:
                logger.warning(f"[trace={self.trace_id}] Subagent {self.config.name} no final state")
                result.result = "No response generated"
            else:
                # 提取最终消息：寻找最后一个 AIMessage
                messages = final_state.get("messages", [])
                logger.info(f"[trace={self.trace_id}] Subagent {self.config.name} final messages count: {len(messages)}")

                # 倒序查找会话中的最后一个 AIMessage
                last_ai_message = None
                for msg in reversed(messages):
                    if isinstance(msg, AIMessage):
                        last_ai_message = msg
                        break

                if last_ai_message is not None:
                    content = last_ai_message.content
                    # 最终结果同时兼容 str 与 list 两种内容结构
                    if isinstance(content, str):
                        result.result = content
                    elif isinstance(content, list):
                        # 从内容块列表中提取文本作为最终结果
                        text_parts = []
                        for block in content:
                            if isinstance(block, str):
                                text_parts.append(block)
                            elif isinstance(block, dict) and "text" in block:
                                text_parts.append(block["text"])
                        result.result = "\n".join(text_parts) if text_parts else "No text content in response"
                    else:
                        result.result = str(content)
                elif messages:
                    # 兜底：若未找到 AIMessage，则使用最后一条消息
                    last_message = messages[-1]
                    logger.warning(f"[trace={self.trace_id}] Subagent {self.config.name} no AIMessage found, using last message: {type(last_message)}")
                    result.result = str(last_message.content) if hasattr(last_message, "content") else str(last_message)
                else:
                    logger.warning(f"[trace={self.trace_id}] Subagent {self.config.name} no messages in final state")
                    result.result = "No response generated"

            result.status = SubagentStatus.COMPLETED
            result.completed_at = datetime.now()

        except Exception as exc:
            self._retain_usage_reservation()
            logger.error(
                "[trace=%s] Subagent %s async execution failed (%s)",
                self.trace_id,
                self.config.name,
                type(exc).__name__,
            )
            result.status = SubagentStatus.FAILED
            result.error = "Subagent execution failed"
            result.completed_at = datetime.now()

        return result

    def execute(self, task: str, result_holder: SubagentResult | None = None) -> SubagentResult:
        """同步执行任务（对异步执行的封装）。

        该方法会在新事件循环中运行异步执行逻辑，从而允许在线程池环境中
        正常使用异步工具（例如 MCP 工具）。

        参数：
            task: 提供给子代理的任务描述。
            result_holder: 可选预创建结果对象，用于执行中更新。

        返回：
            包含执行结果的 SubagentResult。
        """
        # 在新事件循环中运行异步执行
        # 原因：
        # 1. 可能存在仅支持异步的工具（如 MCP 工具）
        # 2. 当前运行在线程池内，默认没有事件循环
        #
        # 注意：_aexecute() 内部已捕获执行异常，
        # 这里的 try-except 主要处理 asyncio.run() 级别失败
        # （例如在已有事件循环的异步上下文中调用）。
        # 子代理执行错误会在 _aexecute() 内部转为 FAILED 状态返回。
        try:
            return asyncio.run(self._aexecute(task, result_holder))
        except Exception as exc:
            self._retain_usage_reservation()
            logger.error(
                "[trace=%s] Subagent %s execution failed (%s)",
                self.trace_id,
                self.config.name,
                type(exc).__name__,
            )
            # 若无现成 result 对象，则创建一个错误结果
            if result_holder is not None:
                result = result_holder
            else:
                result = SubagentResult(
                    task_id=str(uuid.uuid4())[:8],
                    trace_id=self.trace_id,
                    status=SubagentStatus.FAILED,
                )
            result.status = SubagentStatus.FAILED
            result.error = "Subagent execution failed"
            result.completed_at = datetime.now()
            return result

    def execute_async(self, task: str, task_id: str | None = None) -> str:
        """在后台启动任务执行。

        参数：
            task: 子代理任务描述。
            task_id: 可选任务 ID；不提供时自动生成随机 UUID。

        返回：
            可用于后续查询状态的任务 ID。
        """
        # 使用传入 task_id，或自动生成新 ID
        if task_id is None:
            task_id = str(uuid.uuid4())[:8]

        # 创建初始 PENDING 结果
        result = SubagentResult(
            task_id=task_id,
            trace_id=self.trace_id,
            status=SubagentStatus.PENDING,
        )

        logger.info(f"[trace={self.trace_id}] Subagent {self.config.name} starting async execution, task_id={task_id}, timeout={self.config.timeout_seconds}s")

        with _background_tasks_lock:
            _background_tasks[task_id] = result

        # 提交到调度线程池
        def run_task():
            with _background_tasks_lock:
                _background_tasks[task_id].status = SubagentStatus.RUNNING
                _background_tasks[task_id].started_at = datetime.now()
                result_holder = _background_tasks[task_id]

            try:
                # 将执行任务提交到执行线程池，并设置超时
    # 传入 `result_holder` 以支持 `execute()` 实时更新
                execution_future: Future = _execution_pool.submit(self.execute, task, result_holder)
                try:
                    # 带超时等待执行结果
                    exec_result = execution_future.result(timeout=self.config.timeout_seconds)
                    _publish_background_result(task_id, exec_result)
                except FuturesTimeoutError:
                    logger.error(f"[trace={self.trace_id}] Subagent {self.config.name} execution timed out after {self.config.timeout_seconds}s")
                    self._retain_usage_reservation()
                    with _background_tasks_lock:
                        _background_tasks[task_id].status = SubagentStatus.TIMED_OUT
                        _background_tasks[task_id].error = f"Execution timed out after {self.config.timeout_seconds} seconds"
                        _background_tasks[task_id].completed_at = datetime.now()
                    # 取消 future（尽力而为，未必能中止实际执行）
                    execution_future.cancel()
            except Exception as exc:
                self._retain_usage_reservation()
                logger.error(
                    "[trace=%s] Subagent %s scheduler failed (%s)",
                    self.trace_id,
                    self.config.name,
                    type(exc).__name__,
                )
                _publish_background_failure(
                    task_id,
                    "Subagent execution failed",
                )

        _scheduler_pool.submit(run_task)
        return task_id


MAX_CONCURRENT_SUBAGENTS = 3


def get_background_task_result(task_id: str) -> SubagentResult | None:
    """获取后台任务结果。

    参数：
        task_id: `execute_async` 返回的任务 ID。

    返回：
        若找到则返回 `SubagentResult`，否则返回 `None`。
    """
    with _background_tasks_lock:
        return _background_tasks.get(task_id)


def cleanup_background_task(task_id: str) -> None:
    """从后台任务存储中移除已完成任务。

    建议由 `task_tool` 在轮询完成并返回结果后调用，
    以避免大量已结束任务堆积导致内存泄漏。

    仅会移除终态任务（COMPLETED/FAILED/TIMED_OUT），
    以避免与仍在更新该任务条目的后台执行器产生竞态。

    参数：
        task_id: 需要移除的任务 ID。
    """
    with _background_tasks_lock:
        result = _background_tasks.get(task_id)
        if result is None:
            # 无需清理，任务可能已被提前移除。
            logger.debug("Requested cleanup for unknown background task %s", task_id)
            return

        # 仅清理终态任务，避免与后台执行器更新同一任务条目发生竞态。
        is_terminal_status = result.status in {
            SubagentStatus.COMPLETED,
            SubagentStatus.FAILED,
            SubagentStatus.TIMED_OUT,
        }
        if is_terminal_status or result.completed_at is not None:
            del _background_tasks[task_id]
            logger.debug("Cleaned up background task: %s", task_id)
        else:
            logger.debug(
                "Skipping cleanup for non-terminal background task %s (status=%s)",
                task_id,
                result.status.value if hasattr(result.status, "value") else result.status,
            )
