from typing import NotRequired, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langgraph.runtime import Runtime

from src.agents.thread_state import ThreadDataState
from src.config.paths import Paths, get_paths


class ThreadDataMiddlewareState(AgentState):
    """与 `ThreadState` 模式兼容。"""

    thread_data: NotRequired[ThreadDataState | None]


class ThreadDataMiddleware(AgentMiddleware[ThreadDataMiddlewareState]):
    """为每个线程执行构建线程数据目录信息。

    目录结构如下：
    - `{base_dir}/threads/{thread_id}/user-data/workspace`
    - `{base_dir}/threads/{thread_id}/user-data/uploads`
    - `{base_dir}/threads/{thread_id}/user-data/outputs`

    生命周期策略：
    - `lazy_init=True`（默认）：仅计算路径，目录按需创建
    - `lazy_init=False`：在 `before_agent()` 中立即创建目录
    """

    state_schema = ThreadDataMiddlewareState

    def __init__(self, base_dir: str | None = None, lazy_init: bool = True):
        """初始化中间件。

        参数：
            base_dir: 线程数据根目录；默认使用 `Paths` 解析结果。
            lazy_init: 为 True 时延迟目录创建；为 False 时在 `before_agent()`
                中立即创建目录。默认 True 以获得更好性能。
        """
        super().__init__()
        self._paths = Paths(base_dir) if base_dir else get_paths()
        self._lazy_init = lazy_init

    def _get_thread_paths(self, thread_id: str) -> dict[str, str]:
        """获取线程数据目录路径。

        参数：
            thread_id: 线程 ID。

        返回：
            包含 `workspace_path`、`uploads_path`、`outputs_path` 的字典。
        """
        return {
            "workspace_path": str(self._paths.sandbox_work_dir(thread_id)),
            "uploads_path": str(self._paths.sandbox_uploads_dir(thread_id)),
            "outputs_path": str(self._paths.sandbox_outputs_dir(thread_id)),
        }

    def _create_thread_directories(self, thread_id: str) -> dict[str, str]:
        """创建线程数据目录并返回路径字典。

        参数：
            thread_id: 线程 ID。

        返回：
            已创建目录对应的路径字典。
        """
        self._paths.ensure_thread_dirs(thread_id)
        return self._get_thread_paths(thread_id)

    @override
    def before_agent(self, state: ThreadDataMiddlewareState, runtime: Runtime) -> dict | None:
        thread_id = runtime.context.get("thread_id")
        if thread_id is None:
            raise ValueError("Thread ID is required in the context")

        if self._lazy_init:
            # 延迟初始化：仅计算路径，不立即建目录
            paths = self._get_thread_paths(thread_id)
        else:
            # 立即初始化：直接创建目录
            paths = self._create_thread_directories(thread_id)
            print(f"Created thread data directories for thread {thread_id}")

        return {
            "thread_data": {
                **paths,
            }
        }
