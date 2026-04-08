"""异步版检查点提供器。

为需要正确资源清理的长生命周期异步服务提供**异步上下文管理器**。

支持后端：memory、postgres。

用法（例如 FastAPI lifespan）：:

    from src.agents.checkpointer.async_provider import make_checkpointer

    async with make_checkpointer() as checkpointer:
        app.state.checkpointer = checkpointer  # 未配置时为 InMemorySaver

同步用法请见 :mod:`src.agents.checkpointer.provider`。
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import AsyncIterator, Sequence

from langgraph.checkpoint.base import CheckpointTuple, copy_checkpoint
from langgraph.types import Checkpointer

from src.agents.checkpointer.provider import (
    POSTGRES_CONN_REQUIRED,
    POSTGRES_INSTALL,
)
from src.agents.checkpointer.utils import (
    _checkpoint_id,
    _checkpoint_namespace,
    _checkpoint_run_id,
    _group_pending_writes,
    _normalize_string_values,
    _select_checkpoint_keys_for_run_ids,
    _select_latest_checkpoints_per_namespace,
    _select_prunable_checkpoint_keys,
    _sort_checkpoints_oldest_first,
)
from src.config.app_config import get_app_config

logger = logging.getLogger(__name__)

@contextlib.asynccontextmanager
async def _async_checkpointer(config) -> AsyncIterator[Checkpointer]:
    """构建并托管检查点生命周期的异步上下文管理器。"""
    if config.type == "memory":
        from langgraph.checkpoint.memory import InMemorySaver

        yield InMemorySaver()
        return

    if config.type == "postgres":
        try:
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        except ImportError as exc:
            raise ImportError(POSTGRES_INSTALL) from exc

        if not config.connection_string:
            raise ValueError(POSTGRES_CONN_REQUIRED)

        class ManagedAsyncPostgresSaver(AsyncPostgresSaver):
            async def _list_thread_checkpoints(self, thread_id: str) -> list[CheckpointTuple]:
                return [
                    checkpoint_tuple
                    async for checkpoint_tuple in self.alist({"configurable": {"thread_id": str(thread_id)}})
                ]

            async def _restore_thread(
                self,
                target_thread_id: str,
                checkpoints: Sequence[CheckpointTuple],
            ) -> None:
                ordered_checkpoints = _sort_checkpoints_oldest_first(checkpoints)
                if not ordered_checkpoints:
                    return

                retained_keys = {
                    (_checkpoint_namespace(checkpoint_tuple), _checkpoint_id(checkpoint_tuple))
                    for checkpoint_tuple in ordered_checkpoints
                }
                last_checkpoint_ids: dict[str, str] = {}

                for checkpoint_tuple in ordered_checkpoints:
                    checkpoint_ns = _checkpoint_namespace(checkpoint_tuple)
                    parent_id: str | None = None
                    if checkpoint_tuple.parent_config and checkpoint_tuple.parent_config.get("configurable"):
                        parent_config = checkpoint_tuple.parent_config["configurable"]
                        parent_ns = str(parent_config.get("checkpoint_ns", checkpoint_ns))
                        parent_checkpoint_id = parent_config.get("checkpoint_id")
                        if (
                            parent_checkpoint_id is not None
                            and parent_ns == checkpoint_ns
                            and (parent_ns, str(parent_checkpoint_id)) in retained_keys
                        ):
                            parent_id = str(parent_checkpoint_id)
                    if parent_id is None:
                        parent_id = last_checkpoint_ids.get(checkpoint_ns)

                    config = {
                        "configurable": {
                            "thread_id": str(target_thread_id),
                            "checkpoint_ns": checkpoint_ns,
                        }
                    }
                    if parent_id is not None:
                        config["configurable"]["checkpoint_id"] = parent_id

                    metadata = dict(checkpoint_tuple.metadata or {})
                    if "thread_id" in metadata:
                        metadata["thread_id"] = str(target_thread_id)

                    stored_config = await self.aput(
                        config,
                        copy_checkpoint(checkpoint_tuple.checkpoint),
                        metadata,
                        checkpoint_tuple.checkpoint.get("channel_versions", {}),
                    )
                    for task_id, writes in _group_pending_writes(checkpoint_tuple.pending_writes).items():
                        await self.aput_writes(stored_config, writes, task_id)

                    last_checkpoint_ids[checkpoint_ns] = str(
                        stored_config["configurable"]["checkpoint_id"]
                    )

            async def adelete_for_runs(self, run_ids: Sequence[str]) -> None:
                normalized_run_ids = _normalize_string_values(run_ids)
                if not normalized_run_ids:
                    return

                run_id_set = set(normalized_run_ids)
                async with self._cursor() as cur:
                    await cur.execute(
                        "SELECT DISTINCT thread_id FROM checkpoints WHERE metadata->>'run_id' = ANY(%s)",
                        (list(normalized_run_ids),),
                    )
                    affected_thread_ids = [str(row["thread_id"]) for row in await cur.fetchall()]

                for thread_id in affected_thread_ids:
                    checkpoints = await self._list_thread_checkpoints(thread_id)
                    retained = [
                        checkpoint_tuple
                        for checkpoint_tuple in checkpoints
                        if _checkpoint_run_id(checkpoint_tuple) not in run_id_set
                    ]
                    if len(retained) == len(checkpoints):
                        continue
                    await self.adelete_thread(thread_id)
                    await self._restore_thread(thread_id, retained)

            async def acopy_thread(self, source_thread_id: str, target_thread_id: str) -> None:
                source_thread_id = str(source_thread_id)
                target_thread_id = str(target_thread_id)
                if source_thread_id == target_thread_id:
                    return

                checkpoints = await self._list_thread_checkpoints(source_thread_id)
                await self.adelete_thread(target_thread_id)
                await self._restore_thread(target_thread_id, checkpoints)

            async def aprune(
                self,
                thread_ids: Sequence[str],
                *,
                strategy: str = "keep_latest",
            ) -> None:
                normalized_thread_ids = _normalize_string_values(thread_ids)
                if not normalized_thread_ids:
                    return
                if strategy not in {"delete", "keep_latest"}:
                    raise ValueError(f"不支持的裁剪策略: {strategy}")

                if strategy == "delete":
                    for thread_id in normalized_thread_ids:
                        await self.adelete_thread(thread_id)
                    return

                for thread_id in normalized_thread_ids:
                    checkpoints = await self._list_thread_checkpoints(thread_id)
                    if not checkpoints:
                        continue
                    retained = _select_latest_checkpoints_per_namespace(checkpoints)
                    await self.adelete_thread(thread_id)
                    await self._restore_thread(thread_id, retained)

        async with ManagedAsyncPostgresSaver.from_conn_string(config.connection_string) as saver:
            await saver.setup()
            yield saver
        return

    raise ValueError(f"未知的 checkpointer 类型：{config.type!r}（仅支持 memory/postgres）")


# ---------------------------------------------------------------------------
# 对外异步上下文管理器
# ---------------------------------------------------------------------------


@contextlib.asynccontextmanager
async def make_checkpointer() -> AsyncIterator[Checkpointer]:
    """创建检查点异步上下文。

    进入上下文时打开资源，退出时释放资源，不依赖全局状态::

        async with make_checkpointer() as checkpointer:
            app.state.checkpointer = checkpointer

    当 *config.yaml* 未配置检查点时，产出 ``InMemorySaver``。
    """

    config = get_app_config()

    if config.checkpointer is None:
        from langgraph.checkpoint.memory import InMemorySaver

        yield InMemorySaver()
        return

    async with _async_checkpointer(config.checkpointer) as saver:
        yield saver
