import logging
from typing import NotRequired, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langgraph.runtime import Runtime

from src.agents.thread_state import SandboxState, ThreadDataState
from src.sandbox import get_sandbox_provider

logger = logging.getLogger(__name__)


class SandboxMiddlewareState(AgentState):
    """与 `ThreadState` 结构兼容。"""

    sandbox: NotRequired[SandboxState | None]
    thread_data: NotRequired[ThreadDataState | None]


class SandboxMiddleware(AgentMiddleware[SandboxMiddlewareState]):
    """
    生命周期管理：
    - 当 `lazy_init=True`（默认）时：首次工具调用才获取沙箱
    - 当 `lazy_init=False` 时：首次 Agent 调用前（before_agent）即获取沙箱
    - 同一线程内可复用同一个沙箱，跨多轮对话不重复创建
    - 每次 Agent 调用后不会立即释放沙箱，避免频繁重建开销
    - 最终清理在应用关闭时通过 `SandboxProvider.shutdown()` 完成

    """

    state_schema = SandboxMiddlewareState

    def __init__(self, lazy_init: bool = True):
        """
        参数：
            lazy_init: 若为 True，则延迟到首次工具调用再获取沙箱；
                      若为 False，则在 before_agent() 中提前获取。
                      默认 True，以获得更优性能。

        """
        super().__init__()
        self._lazy_init = lazy_init

    def _acquire_sandbox(self, thread_id: str) -> str:
        provider = get_sandbox_provider()
        sandbox_id = provider.acquire(thread_id)
        logger.info(f"Acquiring sandbox {sandbox_id}")
        return sandbox_id

    @override
    def before_agent(self, state: SandboxMiddlewareState, runtime: Runtime) -> dict | None:
        # 开启 lazy_init 时跳过预先获取
        if self._lazy_init:
            return super().before_agent(state, runtime)

        # 提前初始化（原有行为）
        if "sandbox" not in state or state["sandbox"] is None:
            thread_id = runtime.context["thread_id"]
            sandbox_id = self._acquire_sandbox(thread_id)
            logger.info(f"Assigned sandbox {sandbox_id} to thread {thread_id}")
            return {"sandbox": {"sandbox_id": sandbox_id}}
        return super().before_agent(state, runtime)

    @override
    def after_agent(self, state: SandboxMiddlewareState, runtime: Runtime) -> dict | None:
        sandbox = state.get("sandbox")
        if sandbox is not None:
            sandbox_id = sandbox["sandbox_id"]
            logger.info(f"Releasing sandbox {sandbox_id}")
            get_sandbox_provider().release(sandbox_id)
            return None

        if runtime.context.get("sandbox_id") is not None:
            sandbox_id = runtime.context.get("sandbox_id")
            logger.info(f"Releasing sandbox {sandbox_id} from context")
            get_sandbox_provider().release(sandbox_id)
            return None

        # 无可释放沙箱
        return super().after_agent(state, runtime)
