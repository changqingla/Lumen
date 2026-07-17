"""Checkpointer shared helpers."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from langgraph.checkpoint.base import CheckpointTuple, PendingWrite


def _normalize_string_values(values: Sequence[str | None]) -> tuple[str, ...]:
    normalized = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return tuple(normalized)


def _checkpoint_namespace(checkpoint_tuple: CheckpointTuple) -> str:
    return str(checkpoint_tuple.config.get("configurable", {}).get("checkpoint_ns", ""))


def _checkpoint_id(checkpoint_tuple: CheckpointTuple) -> str:
    return str(checkpoint_tuple.config["configurable"]["checkpoint_id"])


def _checkpoint_run_id(checkpoint_tuple: CheckpointTuple) -> str:
    return str((checkpoint_tuple.metadata or {}).get("run_id") or "").strip()


def _sort_checkpoints_oldest_first(
    checkpoints: Sequence[CheckpointTuple],
) -> list[CheckpointTuple]:
    return sorted(checkpoints, key=_checkpoint_id)


def _select_latest_checkpoints_per_namespace(
    checkpoints: Sequence[CheckpointTuple],
) -> list[CheckpointTuple]:
    latest_by_namespace: dict[str, CheckpointTuple] = {}
    for checkpoint_tuple in sorted(checkpoints, key=_checkpoint_id, reverse=True):
        latest_by_namespace.setdefault(_checkpoint_namespace(checkpoint_tuple), checkpoint_tuple)
    return _sort_checkpoints_oldest_first(list(latest_by_namespace.values()))


def _group_pending_writes(
    pending_writes: Sequence[PendingWrite] | None,
) -> dict[str, list[tuple[str, Any]]]:
    writes_by_task: dict[str, list[tuple[str, Any]]] = {}
    for task_id, channel, value in pending_writes or []:
        writes_by_task.setdefault(str(task_id), []).append((channel, value))
    return writes_by_task
