"""Checkpointer shared helpers."""

from __future__ import annotations

import json
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


def _select_checkpoint_keys_for_run_ids(
    rows: Sequence[tuple[str, str, str, bytes | str | None]],
    run_ids: Sequence[str],
) -> list[tuple[str, str, str]]:
    normalized_run_ids = set(_normalize_string_values(run_ids))
    if not normalized_run_ids:
        return []

    checkpoint_keys: list[tuple[str, str, str]] = []
    for thread_id, checkpoint_ns, checkpoint_id, metadata in rows:
        try:
            payload = json.loads(metadata) if metadata is not None else {}
        except (TypeError, ValueError):
            payload = {}

        run_id = str(payload.get("run_id") or "").strip()
        if run_id in normalized_run_ids:
            checkpoint_keys.append((thread_id, checkpoint_ns, checkpoint_id))
    return checkpoint_keys


def _select_prunable_checkpoint_keys(
    rows: Sequence[tuple[str, str, str]],
    *,
    strategy: str,
) -> list[tuple[str, str, str]]:
    if strategy not in {"keep_latest", "delete"}:
        raise ValueError(f"不支持的裁剪策略: {strategy}")

    if strategy == "delete":
        return list(rows)

    keep_latest_seen: set[tuple[str, str]] = set()
    checkpoint_keys: list[tuple[str, str, str]] = []
    for thread_id, checkpoint_ns, checkpoint_id in rows:
        namespace_key = (thread_id, checkpoint_ns)
        if namespace_key in keep_latest_seen:
            checkpoint_keys.append((thread_id, checkpoint_ns, checkpoint_id))
            continue
        keep_latest_seen.add(namespace_key)
    return checkpoint_keys


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
