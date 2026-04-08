"""同步版检查点提供器。

为 LangGraph 图编译与 CLI 工具提供：
- 同步单例
- 同步上下文管理器

支持后端：memory、postgres。

用法::

    from src.agents.checkpointer.provider import get_checkpointer, checkpointer_context

    # 单例：多次调用复用，进程退出时关闭
    cp = get_checkpointer()

    # 一次性实例：每个 with 块独立创建并在退出时关闭
    with checkpointer_context() as cp:
        graph.invoke(input, config={"configurable": {"thread_id": "1"}})
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Iterator, Sequence

from langgraph.checkpoint.base import CheckpointTuple, copy_checkpoint
from langgraph.types import Checkpointer

from src.config.app_config import get_app_config
from src.config.checkpointer_config import CheckpointerConfig
from src.agents.checkpointer.utils import (
    _checkpoint_id,
    _checkpoint_namespace,
    _checkpoint_run_id,
    _group_pending_writes,
    _normalize_string_values,
    _select_latest_checkpoints_per_namespace,
    _sort_checkpoints_oldest_first,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 错误消息常量（aio.provider 也会复用）
# ---------------------------------------------------------------------------

POSTGRES_INSTALL = "PostgreSQL checkpointer 需要安装 `langgraph-checkpoint-postgres`。安装命令：uv add langgraph-checkpoint-postgres psycopg[binary] psycopg-pool"
POSTGRES_CONN_REQUIRED = "postgres 后端必须配置 checkpointer.connection_string"


@contextlib.contextmanager
def _sync_checkpointer_cm(config: CheckpointerConfig) -> Iterator[Checkpointer]:
    """创建并返回配置好的 ``Checkpointer``。

    底层连接或连接池的资源清理由本模块更高层的辅助方法负责
    （例如单例工厂或上下文管理器）；本函数不返回独立清理回调。
    """
    if config.type == "memory":
        from langgraph.checkpoint.memory import InMemorySaver

        logger.info("Checkpointer：使用 InMemorySaver（仅进程内，不持久化）")
        yield InMemorySaver()
        return

    if config.type == "postgres":
        try:
            from langgraph.checkpoint.postgres import PostgresSaver
        except ImportError as exc:
            raise ImportError(POSTGRES_INSTALL) from exc

        if not config.connection_string:
            raise ValueError(POSTGRES_CONN_REQUIRED)

        class ManagedPostgresSaver(PostgresSaver):
            def _list_thread_checkpoints(self, thread_id: str) -> list[CheckpointTuple]:
                return list(self.list({"configurable": {"thread_id": str(thread_id)}}))

            def _restore_thread(
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

                    stored_config = self.put(
                        config,
                        copy_checkpoint(checkpoint_tuple.checkpoint),
                        metadata,
                        checkpoint_tuple.checkpoint.get("channel_versions", {}),
                    )
                    for task_id, writes in _group_pending_writes(checkpoint_tuple.pending_writes).items():
                        self.put_writes(stored_config, writes, task_id)

                    last_checkpoint_ids[checkpoint_ns] = str(
                        stored_config["configurable"]["checkpoint_id"]
                    )

            def delete_for_runs(self, run_ids: Sequence[str]) -> None:
                normalized_run_ids = _normalize_string_values(run_ids)
                if not normalized_run_ids:
                    return

                run_id_set = set(normalized_run_ids)
                with self._cursor() as cur:
                    cur.execute(
                        "SELECT DISTINCT thread_id FROM checkpoints WHERE metadata->>'run_id' = ANY(%s)",
                        (list(normalized_run_ids),),
                    )
                    affected_thread_ids = [str(row["thread_id"]) for row in cur.fetchall()]

                for thread_id in affected_thread_ids:
                    checkpoints = self._list_thread_checkpoints(thread_id)
                    retained = [
                        checkpoint_tuple
                        for checkpoint_tuple in checkpoints
                        if _checkpoint_run_id(checkpoint_tuple) not in run_id_set
                    ]
                    if len(retained) == len(checkpoints):
                        continue
                    self.delete_thread(thread_id)
                    self._restore_thread(thread_id, retained)

            def copy_thread(self, source_thread_id: str, target_thread_id: str) -> None:
                source_thread_id = str(source_thread_id)
                target_thread_id = str(target_thread_id)
                if source_thread_id == target_thread_id:
                    return

                checkpoints = self._list_thread_checkpoints(source_thread_id)
                self.delete_thread(target_thread_id)
                self._restore_thread(target_thread_id, checkpoints)

            def prune(
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
                        self.delete_thread(thread_id)
                    return

                for thread_id in normalized_thread_ids:
                    checkpoints = self._list_thread_checkpoints(thread_id)
                    if not checkpoints:
                        continue
                    retained = _select_latest_checkpoints_per_namespace(checkpoints)
                    self.delete_thread(thread_id)
                    self._restore_thread(thread_id, retained)

        with ManagedPostgresSaver.from_conn_string(config.connection_string) as saver:
            saver.setup()
            logger.info("Checkpointer：使用 PostgresSaver")
            yield saver
        return

    raise ValueError(f"未知的 checkpointer 类型：{config.type!r}（仅支持 memory/postgres）")


# ---------------------------------------------------------------------------
# 同步单例
# ---------------------------------------------------------------------------

_checkpointer: Checkpointer | None = None
_checkpointer_ctx = None  # 保持连接存活的已打开上下文管理器


def get_checkpointer() -> Checkpointer:
    """获取检查点单例。

    当 *config.yaml* 未配置检查点时，返回 ``InMemorySaver``。

    异常：
        ImportError: 已配置后端但缺少对应依赖包时抛出。
        ValueError: 后端需要 ``connection_string`` 但未提供时抛出。
    """
    global _checkpointer, _checkpointer_ctx

    if _checkpointer is not None:
        return _checkpointer

    from src.config.checkpointer_config import get_checkpointer_config

    config = get_checkpointer_config()
    if config is None:
        # 在读取 checkpointer 配置前确保 app 配置已加载，
        # 但若测试或调用方已经显式注入了配置，则优先尊重显式设置。
        from src.config.app_config import _app_config

        if _app_config is None:
            try:
                get_app_config()
            except FileNotFoundError:
                # 测试环境没有 config.yaml 属于预期场景
                pass
        config = get_checkpointer_config()

    if config is None:
        from langgraph.checkpoint.memory import InMemorySaver

        logger.info("Checkpointer：使用 InMemorySaver（仅进程内，不持久化）")
        _checkpointer = InMemorySaver()
        return _checkpointer

    _checkpointer_ctx = _sync_checkpointer_cm(config)
    _checkpointer = _checkpointer_ctx.__enter__()

    return _checkpointer


def reset_checkpointer() -> None:
    """重置检查点单例并释放资源。

    会关闭已打开的后端连接并清空缓存实例。
    适用于测试场景或配置变更之后。
    """
    global _checkpointer, _checkpointer_ctx
    if _checkpointer_ctx is not None:
        try:
            _checkpointer_ctx.__exit__(None, None, None)
        except Exception:
            logger.warning("清理 checkpointer 时发生错误", exc_info=True)
        _checkpointer_ctx = None
    _checkpointer = None


# ---------------------------------------------------------------------------
# 同步上下文管理器
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def checkpointer_context() -> Iterator[Checkpointer]:
    """获取非缓存型检查点上下文。

    与 :func:`get_checkpointer` 不同，此方法**不会缓存实例**；
    每个 ``with`` 块都会独立创建并销毁连接。适用于 CLI 脚本或
    需要确定性清理的测试场景::

        with checkpointer_context() as cp:
            graph.invoke(input, config={"configurable": {"thread_id": "1"}})

    当 *config.yaml* 未配置检查点时，产出 ``InMemorySaver``。
    """

    config = get_app_config()
    if config.checkpointer is None:
        from langgraph.checkpoint.memory import InMemorySaver

        yield InMemorySaver()
        return

    with _sync_checkpointer_cm(config.checkpointer) as saver:
        yield saver
