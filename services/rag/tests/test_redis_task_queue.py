from __future__ import annotations

import json
import logging

import fakeredis.aioredis
import pytest

from redis_task_queue import (
    QueuePriority,
    RedisTaskQueue,
    TaskLease,
    _log_queue_failure,
    make_queue_score,
)


def test_queue_operational_failure_log_omits_exception_and_raw_task_id(caplog):
    marker = "redis://user:private-password@redis.internal/1"
    task_id = "private-task-id"

    with caplog.at_level(logging.ERROR):
        _log_queue_failure(RuntimeError(marker), task_id)

    assert marker not in caplog.text
    assert "private-password" not in caplog.text
    assert task_id not in caplog.text
    assert "error_type=RuntimeError" in caplog.text


async def _install_queue_script_emulator(queue: RedisTaskQueue) -> None:
    client = queue.redis_client

    async def decrement_nonnegative(stats_key: str, field: str) -> None:
        current = int(await client.hget(stats_key, field) or 0)
        await client.hset(stats_key, field, max(0, current - 1))

    async def eval_script(script, number_of_keys, *args):
        if script == queue._enqueue_task_script:
            assert number_of_keys == 4
            (
                queue_key,
                task_data_key,
                stats_key,
                sequence_key,
                task_id,
                priority,
                task_data,
                now,
                _priority_stride,
            ) = args
            sequence = await client.incr(sequence_key)
            await client.zadd(
                queue_key, {task_id: make_queue_score(int(priority), sequence)}
            )
            await client.hset(task_data_key, task_id, task_data)
            await client.hincrby(stats_key, "total_enqueued", 1)
            queue_length = max(
                0, int(await client.hget(stats_key, "queue_length") or 0)
            )
            await client.hset(stats_key, "queue_length", queue_length + 1)
            await client.hset(stats_key, "last_updated", float(now))
            return sequence

        if script == queue._claim_idempotent_task_script:
            assert number_of_keys == 7
            (
                queue_key,
                processing_key,
                completed_key,
                task_data_key,
                idempotency_key,
                stats_key,
                sequence_key,
                candidate_task_id,
                request_key,
                priority,
                task_data,
                now,
                _priority_stride,
            ) = args
            existing_task_id = await client.hget(idempotency_key, request_key)
            if existing_task_id:
                active = any(
                    score is not None
                    for score in (
                        await client.zscore(queue_key, existing_task_id),
                        await client.zscore(processing_key, existing_task_id),
                        await client.zscore(completed_key, existing_task_id),
                    )
                )
                existing_raw = await client.hget(task_data_key, existing_task_id)
                existing_status = (
                    json.loads(existing_raw).get("status") if existing_raw else None
                )
                if active or existing_status not in {"failed", "cancelled", None}:
                    return [existing_task_id, "0"]

            sequence = await client.incr(sequence_key)
            await client.zadd(
                queue_key,
                {candidate_task_id: make_queue_score(int(priority), sequence)},
            )
            await client.hset(task_data_key, candidate_task_id, task_data)
            await client.hset(idempotency_key, request_key, candidate_task_id)
            await client.hincrby(stats_key, "total_enqueued", 1)
            queue_length = max(
                0, int(await client.hget(stats_key, "queue_length") or 0)
            )
            await client.hset(stats_key, "queue_length", queue_length + 1)
            await client.hset(stats_key, "last_updated", float(now))
            return [candidate_task_id, "1"]

        if script == queue._dequeue_task_script:
            assert number_of_keys == 4
            (
                queue_key,
                processing_key,
                leases_key,
                stats_key,
                max_concurrent,
                now,
                deadline,
                lease_token,
            ) = args
            if await client.zcard(processing_key) >= int(max_concurrent):
                return None
            result = await client.zpopmax(queue_key, 1)
            if not result:
                return None
            task_id = result[0][0]
            await client.zadd(processing_key, {task_id: float(deadline)})
            await client.hset(leases_key, task_id, lease_token)
            processing = max(
                0, int(await client.hget(stats_key, "current_processing") or 0)
            )
            await client.hset(stats_key, "current_processing", processing + 1)
            await decrement_nonnegative(stats_key, "queue_length")
            await client.hset(stats_key, "last_updated", float(now))
            return [task_id, lease_token]

        if script == queue._heartbeat_task_script:
            assert number_of_keys == 3
            (
                processing_key,
                leases_key,
                stats_key,
                task_id,
                lease_token,
                now,
                deadline,
            ) = args
            current_token = await client.hget(leases_key, task_id)
            current_deadline = await client.zscore(processing_key, task_id)
            if (
                current_token != lease_token
                or current_deadline is None
                or current_deadline <= float(now)
            ):
                return 0
            await client.zadd(processing_key, {task_id: float(deadline)}, xx=True)
            await client.hset(stats_key, "last_updated", float(now))
            return 1

        if script == queue._complete_task_script:
            assert number_of_keys == 4
            (
                processing_key,
                completed_key,
                leases_key,
                stats_key,
                task_id,
                lease_token,
                now,
            ) = args
            if await client.hget(leases_key, task_id) != lease_token:
                return 0
            if not await client.zrem(processing_key, task_id):
                return 0
            await client.hdel(leases_key, task_id)
            added = await client.zadd(completed_key, {task_id: float(now)}, nx=True)
            await decrement_nonnegative(stats_key, "current_processing")
            if added:
                await client.hincrby(stats_key, "total_processed", 1)
            await client.hset(stats_key, "last_updated", float(now))
            return 1

        if script == queue._cancel_processing_task_script:
            assert number_of_keys == 3
            (
                processing_key,
                leases_key,
                stats_key,
                task_id,
                lease_token,
                now,
            ) = args
            if await client.hget(leases_key, task_id) != lease_token:
                return 0
            if not await client.zrem(processing_key, task_id):
                return 0
            await client.hdel(leases_key, task_id)
            await decrement_nonnegative(stats_key, "current_processing")
            await client.hset(stats_key, "last_updated", float(now))
            return 1

        if script == queue._transition_processing_task_script:
            assert number_of_keys == 7
            (
                queue_key,
                processing_key,
                failed_key,
                task_data_key,
                leases_key,
                stats_key,
                sequence_key,
                task_id,
                lease_token,
                now,
                message,
                _priority_stride,
                increment_retry,
                force_terminal,
                require_stale,
                completed_at,
            ) = args
            current_token = await client.hget(leases_key, task_id)
            if (current_token and current_token != lease_token) or (
                not current_token and (lease_token or not int(require_stale))
            ):
                return "lease_mismatch"
            current_deadline = await client.zscore(processing_key, task_id)
            if current_deadline is None:
                return "not_processing"
            if int(require_stale) and current_deadline > float(now):
                return "not_stale"

            raw_data = await client.hget(task_data_key, task_id)
            try:
                task_data = json.loads(raw_data) if raw_data else None
            except json.JSONDecodeError:
                task_data = None
            if task_data is None:
                force_terminal = 1
            if (
                task_data
                and int(increment_retry)
                and not int(require_stale)
                and isinstance(task_data.get("message"), str)
            ):
                message = task_data["message"]

            should_retry = False
            if not int(force_terminal) and task_data is not None:
                should_retry = not int(increment_retry) or int(
                    task_data.get("retry_count", 0)
                ) < int(task_data.get("max_retries", 3))

            if not await client.zrem(processing_key, task_id):
                return "not_processing"
            await client.hdel(leases_key, task_id)
            await decrement_nonnegative(stats_key, "current_processing")

            if should_retry:
                priority = int(task_data.get("priority", QueuePriority.NORMAL.value))
                if int(increment_retry):
                    task_data["retry_count"] = int(task_data.get("retry_count", 0)) + 1
                    priority = max(QueuePriority.LOW.value, priority - 1)
                    task_data["priority"] = priority
                task_data.update(
                    {
                        "status": "queued",
                        "message": message,
                        "progress": 0,
                        "started_at": None,
                        "completed_at": None,
                    }
                )
                await client.hset(task_data_key, task_id, json.dumps(task_data))
                sequence = await client.incr(sequence_key)
                added = await client.zadd(
                    queue_key,
                    {task_id: make_queue_score(priority, sequence)},
                    nx=True,
                )
                if added:
                    queue_length = max(
                        0, int(await client.hget(stats_key, "queue_length") or 0)
                    )
                    await client.hset(stats_key, "queue_length", queue_length + 1)
                await client.hset(stats_key, "last_updated", float(now))
                return "requeued"

            if task_data is not None:
                task_data.update(
                    {
                        "status": "failed",
                        "message": message,
                        "completed_at": completed_at,
                    }
                )
                await client.hset(task_data_key, task_id, json.dumps(task_data))
            added = await client.zadd(failed_key, {task_id: float(now)}, nx=True)
            if added:
                await client.hincrby(stats_key, "total_failed", 1)
            await client.hset(stats_key, "last_updated", float(now))
            return "failed"

        if script == queue._update_leased_task_data_script:
            assert number_of_keys == 3
            (
                processing_key,
                leases_key,
                task_data_key,
                task_id,
                lease_token,
                replacement_json,
            ) = args
            if await client.hget(leases_key, task_id) != lease_token:
                return None
            if await client.zscore(processing_key, task_id) is None:
                return None
            replacement = json.loads(replacement_json)
            existing_json = await client.hget(task_data_key, task_id)
            existing = json.loads(existing_json) if existing_json else {}
            if existing.get("cancel_requested") is True:
                replacement["cancel_requested"] = True
            persisted = json.dumps(replacement)
            await client.hset(task_data_key, task_id, persisted)
            return persisted

        raise AssertionError("unexpected Lua script")

    client.eval = eval_script


@pytest.mark.asyncio
async def test_queue_is_fifo_within_priority_and_strict_across_priorities():
    queue = RedisTaskQueue(max_concurrent_tasks=10)
    queue.redis_client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    queue._initialized = True
    await _install_queue_script_emulator(queue)

    await queue.enqueue_task("normal-old", QueuePriority.NORMAL)
    await queue.enqueue_task("normal-new", QueuePriority.NORMAL)
    await queue.enqueue_task("urgent", QueuePriority.URGENT)

    first = await queue.dequeue_task()
    second = await queue.dequeue_task()
    third = await queue.dequeue_task()

    assert first.task_id == "urgent"
    assert second.task_id == "normal-old"
    assert third.task_id == "normal-new"
    assert len({first.token, second.token, third.token}) == 3
    assert await queue.redis_client.hget(queue.leases_key, first.task_id) == first.token


def test_queue_score_keeps_priority_dominant_for_queue_lifetime():
    trillionth_task = 1_000_000_000_000

    oldest_low = make_queue_score(QueuePriority.LOW.value, 0)
    newest_high = make_queue_score(QueuePriority.HIGH.value, trillionth_task)

    assert newest_high > oldest_low
    assert make_queue_score(QueuePriority.NORMAL.value, 10) > make_queue_score(
        QueuePriority.NORMAL.value,
        11,
    )


@pytest.mark.asyncio
async def test_heartbeat_requires_current_unexpired_lease_and_extends_deadline():
    queue = RedisTaskQueue(visibility_timeout_seconds=60)
    queue.redis_client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    queue._initialized = True
    await _install_queue_script_emulator(queue)
    await queue.enqueue_task("heartbeat-task")
    lease = await queue.dequeue_task()
    assert isinstance(lease, TaskLease)

    original_deadline = await queue.redis_client.zscore(
        queue.processing_key, lease.task_id
    )
    assert await queue.heartbeat_task(lease.task_id, "old-token") is False
    assert await queue.heartbeat_task(lease.task_id, lease.token) is True
    refreshed_deadline = await queue.redis_client.zscore(
        queue.processing_key, lease.task_id
    )
    assert refreshed_deadline >= original_deadline

    await queue.redis_client.zadd(queue.processing_key, {lease.task_id: 1})
    assert await queue.heartbeat_task(lease.task_id, lease.token) is False


@pytest.mark.asyncio
async def test_stale_recovery_requeues_once_and_rejects_old_worker_completion():
    queue = RedisTaskQueue(max_concurrent_tasks=1)
    queue.redis_client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    queue._initialized = True
    await _install_queue_script_emulator(queue)
    await queue.enqueue_task(
        "recover-task",
        QueuePriority.HIGH,
        task_data={"status": "queued", "source_path": "/payload/paper.md"},
    )
    old_lease = await queue.dequeue_task()
    await queue.redis_client.zadd(queue.processing_key, {old_lease.task_id: 1})
    await queue.redis_client.hset(
        queue.stats_key,
        mapping={"queue_length": -5, "current_processing": -5},
    )

    disposition = await queue.recover_stale_task(
        old_lease.task_id,
        old_lease.token,
        payload_available=True,
    )
    repeated = await queue.recover_stale_task(
        old_lease.task_id,
        old_lease.token,
        payload_available=True,
    )

    assert disposition == "requeued"
    assert repeated in {"lease_mismatch", "not_processing"}
    recovered_data = await queue.get_task_data(old_lease.task_id)
    assert recovered_data["status"] == "queued"
    assert recovered_data["retry_count"] == 1
    assert recovered_data["priority"] == QueuePriority.NORMAL.value
    recovered_stats = await queue.redis_client.hgetall(queue.stats_key)
    assert int(recovered_stats["queue_length"]) == 1
    assert int(recovered_stats["current_processing"]) == 0

    new_lease = await queue.dequeue_task()
    assert new_lease.task_id == old_lease.task_id
    assert new_lease.token != old_lease.token
    assert (
        await queue.update_task_data(
            old_lease.task_id,
            {**recovered_data, "status": "completed"},
            lease_token=old_lease.token,
        )
        is None
    )
    assert (
        await queue.fail_task_with_result(
            old_lease.task_id,
            old_lease.token,
            "stale worker failure",
        )
        == "lease_mismatch"
    )
    assert await queue.complete_task(old_lease.task_id, old_lease.token) is False
    assert await queue.complete_task(new_lease.task_id, new_lease.token) is True

    stats = await queue.get_queue_stats()
    assert stats["queue_length"] == 0
    assert stats["processing_count"] == 0
    assert stats["completed_count"] == 1
    assert stats["total_processed"] == 1


@pytest.mark.asyncio
async def test_stale_recovery_missing_payload_fails_once_without_retry_loop():
    queue = RedisTaskQueue()
    queue.redis_client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    queue._initialized = True
    await _install_queue_script_emulator(queue)
    await queue.enqueue_task(
        "missing-payload",
        task_data={
            "status": "processing",
            "retry_count": 0,
            "max_retries": 100,
            "source_path": "/missing/paper.md",
        },
    )
    lease = await queue.dequeue_task()
    await queue.redis_client.zadd(queue.processing_key, {lease.task_id: 1})

    assert (
        await queue.recover_stale_task(
            lease.task_id,
            lease.token,
            payload_available=False,
        )
        == "failed"
    )
    assert await queue.recover_stale_task(
        lease.task_id,
        lease.token,
        payload_available=False,
    ) in {"lease_mismatch", "not_processing"}

    task_data = await queue.get_task_data(lease.task_id)
    stats = await queue.get_queue_stats()
    assert task_data["status"] == "failed"
    assert task_data["retry_count"] == 0
    assert stats["failed_count"] == 1
    assert stats["total_failed"] == 1
    assert stats["queue_length"] == 0
    assert stats["processing_count"] == 0


@pytest.mark.asyncio
async def test_stale_recovery_honors_existing_max_retries_semantics():
    queue = RedisTaskQueue()
    queue.redis_client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    queue._initialized = True
    await _install_queue_script_emulator(queue)
    await queue.enqueue_task(
        "retries-exhausted",
        task_data={
            "status": "processing",
            "retry_count": 3,
            "max_retries": 3,
            "source_path": "/payload/paper.md",
        },
    )
    lease = await queue.dequeue_task()
    await queue.redis_client.zadd(queue.processing_key, {lease.task_id: 1})

    assert (
        await queue.recover_stale_task(
            lease.task_id,
            lease.token,
            payload_available=True,
        )
        == "failed"
    )
    task_data = await queue.get_task_data(lease.task_id)
    assert task_data["status"] == "failed"
    assert task_data["retry_count"] == 3


@pytest.mark.asyncio
async def test_terminal_failure_log_does_not_include_worker_error_body(caplog):
    queue = RedisTaskQueue(max_concurrent_tasks=1)
    queue.redis_client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    queue._initialized = True
    await _install_queue_script_emulator(queue)
    await queue.enqueue_task(
        "private-failure-task",
        task_data={
            "status": "queued",
            "retry_count": 0,
            "max_retries": 0,
        },
    )
    lease = await queue.dequeue_task()
    secret_marker = "private-provider-body-must-not-enter-logs"

    with caplog.at_level(logging.ERROR, logger="redis_task_queue"):
        disposition = await queue.fail_task_with_result(
            lease.task_id,
            lease.token,
            secret_marker,
        )

    assert disposition == "failed"
    assert secret_marker not in caplog.text


@pytest.mark.asyncio
async def test_stale_recovery_migrates_legacy_processing_entry_without_token():
    queue = RedisTaskQueue()
    queue.redis_client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    queue._initialized = True
    await _install_queue_script_emulator(queue)
    task_data = {
        "task_id": "legacy-processing",
        "priority": QueuePriority.NORMAL.value,
        "retry_count": 0,
        "max_retries": 3,
        "status": "processing",
        "source_path": "/payload/paper.md",
    }
    await queue.redis_client.hset(
        queue.task_data_key,
        task_data["task_id"],
        json.dumps(task_data),
    )
    await queue.redis_client.zadd(queue.processing_key, {task_data["task_id"]: 1})
    await queue.redis_client.hset(queue.stats_key, "current_processing", 1)

    leases = await queue.get_stale_task_leases()
    assert leases == [TaskLease(task_data["task_id"], "")]
    assert (
        await queue.recover_stale_task(
            leases[0].task_id,
            leases[0].token,
            payload_available=True,
        )
        == "requeued"
    )
    recovered = await queue.get_task_data(task_data["task_id"])
    assert recovered["status"] == "queued"
    assert recovered["retry_count"] == 1


@pytest.mark.asyncio
async def test_shutdown_requeue_preserves_retry_count_and_payload_metadata():
    queue = RedisTaskQueue()
    queue.redis_client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    queue._initialized = True
    await _install_queue_script_emulator(queue)
    await queue.enqueue_task(
        "shutdown-task",
        task_data={
            "status": "processing",
            "retry_count": 2,
            "source_path": "/payload/paper.md",
        },
    )
    lease = await queue.dequeue_task()

    assert (
        await queue.requeue_task(
            lease.task_id,
            lease.token,
            payload_available=True,
        )
        == "requeued"
    )
    task_data = await queue.get_task_data(lease.task_id)
    assert task_data["status"] == "queued"
    assert task_data["retry_count"] == 2
    assert task_data["source_path"] == "/payload/paper.md"


@pytest.mark.asyncio
async def test_cancel_requires_current_lease_and_does_not_double_decrement_stats():
    queue = RedisTaskQueue()
    queue.redis_client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    queue._initialized = True
    await _install_queue_script_emulator(queue)
    await queue.enqueue_task("cancel-task", task_data={"status": "cancelled"})
    lease = await queue.dequeue_task()

    assert await queue.mark_task_cancelled(lease.task_id, "old-token") is False
    assert await queue.mark_task_cancelled(lease.task_id, lease.token) is True
    assert await queue.mark_task_cancelled(lease.task_id, lease.token) is False

    stats = await queue.redis_client.hgetall(queue.stats_key)
    assert int(stats["current_processing"]) == 0
    assert await queue.redis_client.zcard(queue.processing_key) == 0


@pytest.mark.asyncio
async def test_all_queue_write_paths_remove_task_secrets():
    queue = RedisTaskQueue()
    queue.redis_client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    queue._initialized = True
    await _install_queue_script_emulator(queue)

    await queue.enqueue_task(
        "task-1",
        task_data={
            "embedding_config": {"api_key": "embedding-secret"},
            "chunk_config": {"cv_model_config": {"api_key": "cv-secret"}},
            "store_config": {"password": "es-secret"},
        },
    )
    raw = await queue.redis_client.hget(queue.task_data_key, "task-1")
    assert "embedding-secret" not in raw
    assert "cv-secret" not in raw
    assert "es-secret" not in raw

    await queue.update_task_data(
        "task-1",
        {"nested": {"access_token": "later-secret", "batch_size": 4}},
    )
    stored = json.loads(await queue.redis_client.hget(queue.task_data_key, "task-1"))
    assert stored["nested"] == {"batch_size": 4}


@pytest.mark.asyncio
async def test_idempotent_claim_reuses_active_and_completed_task():
    queue = RedisTaskQueue()
    queue.redis_client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    queue._initialized = True
    await _install_queue_script_emulator(queue)
    request_key = "a" * 64

    first_id, first_created = await queue.claim_idempotent_task(
        "task-first",
        request_key,
        task_data={"status": "queued"},
    )
    second_id, second_created = await queue.claim_idempotent_task(
        "task-second",
        request_key,
        task_data={"status": "queued"},
    )

    assert (first_id, first_created) == ("task-first", True)
    assert (second_id, second_created) == ("task-first", False)
    assert await queue.redis_client.zcard(queue.queue_key) == 1

    await queue.redis_client.zrem(queue.queue_key, first_id)
    await queue.redis_client.zadd(queue.completed_key, {first_id: 1})
    third_id, third_created = await queue.claim_idempotent_task(
        "task-third",
        request_key,
        task_data={"status": "queued"},
    )

    assert (third_id, third_created) == ("task-first", False)
    assert await queue.redis_client.zcard(queue.queue_key) == 0


@pytest.mark.asyncio
async def test_idempotent_claim_allows_retry_after_terminal_failure():
    queue = RedisTaskQueue()
    queue.redis_client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    queue._initialized = True
    await _install_queue_script_emulator(queue)
    request_key = "b" * 64

    first_id, _ = await queue.claim_idempotent_task(
        "task-failed",
        request_key,
        task_data={"status": "queued"},
    )
    await queue.redis_client.zrem(queue.queue_key, first_id)
    failed_data = json.loads(
        await queue.redis_client.hget(queue.task_data_key, first_id)
    )
    failed_data["status"] = "failed"
    await queue.redis_client.hset(
        queue.task_data_key,
        first_id,
        json.dumps(failed_data),
    )

    retry_id, created = await queue.claim_idempotent_task(
        "task-retry",
        request_key,
        task_data={"status": "queued"},
    )

    assert (retry_id, created) == ("task-retry", True)
    assert await queue.redis_client.hget(queue.idempotency_key, request_key) == (
        "task-retry"
    )
