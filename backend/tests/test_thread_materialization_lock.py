import asyncio
from collections import Counter

import pytest
from pydantic import ValidationError

from config.settings import Settings, settings
from modules.chat.services.thread_materialization_service import (
    ThreadMaterializationLockConfigurationError,
    ThreadMaterializationLockTimeout,
    _acquire_postgres_advisory_lock,
    _build_thread_guard_factory,
    _thread_advisory_lock_key,
)


class _FakeAdvisoryDatabase:
    def __init__(self) -> None:
        self._mutex = asyncio.Lock()
        self._owners: dict[int, _FakeConnection] = {}
        self.connections: list[_FakeConnection] = []
        self.calls: Counter[str] = Counter()
        self.fail_unlock = False

    async def connect(self):
        connection = _FakeConnection(self)
        self.connections.append(connection)
        return connection

    async def try_lock(self, connection: "_FakeConnection", lock_key: int) -> bool:
        async with self._mutex:
            self.calls["try_lock"] += 1
            owner = self._owners.get(lock_key)
            if owner is None:
                self._owners[lock_key] = connection
                connection.held_keys.add(lock_key)
                return True
            return owner is connection

    async def unlock(self, connection: "_FakeConnection", lock_key: int) -> bool:
        async with self._mutex:
            self.calls["unlock"] += 1
            if self.fail_unlock:
                return False
            if self._owners.get(lock_key) is not connection:
                return False
            del self._owners[lock_key]
            connection.held_keys.discard(lock_key)
            return True

    async def invalidate(self, connection: "_FakeConnection") -> None:
        async with self._mutex:
            self.calls["invalidate"] += 1
            for lock_key in tuple(connection.held_keys):
                if self._owners.get(lock_key) is connection:
                    del self._owners[lock_key]
            connection.held_keys.clear()

    @property
    def held_lock_count(self) -> int:
        return len(self._owners)


class _FakeConnection:
    def __init__(self, database: _FakeAdvisoryDatabase) -> None:
        self.database = database
        self.held_keys: set[int] = set()
        self.closed = False
        self.invalidated = False

    async def scalar(self, statement, parameters):
        sql = str(statement)
        lock_key = int(parameters["lock_key"])
        if "pg_try_advisory_lock" in sql:
            return await self.database.try_lock(self, lock_key)
        if "pg_advisory_unlock" in sql:
            return await self.database.unlock(self, lock_key)
        raise AssertionError(f"Unexpected SQL: {sql}")

    async def invalidate(self) -> None:
        self.invalidated = True
        await self.database.invalidate(self)

    async def close(self) -> None:
        self.closed = True


def _guard(database: _FakeAdvisoryDatabase, thread_id: str, *, timeout: float = 1):
    return _acquire_postgres_advisory_lock(
        thread_id,
        connection_factory=database.connect,
        timeout_seconds=timeout,
        poll_interval_seconds=0.001,
    )


def test_advisory_lock_key_is_stable_signed_64_bit_and_domain_specific():
    key = _thread_advisory_lock_key("thread-1")

    assert key == _thread_advisory_lock_key("thread-1")
    assert -(1 << 63) <= key < (1 << 63)
    assert key != _thread_advisory_lock_key("thread-2")


@pytest.mark.asyncio
async def test_same_thread_is_serialized_across_independent_connections():
    database = _FakeAdvisoryDatabase()
    first_entered = asyncio.Event()
    release_first = asyncio.Event()
    second_entered = asyncio.Event()

    async def first_worker():
        async with _guard(database, "shared-thread"):
            first_entered.set()
            await release_first.wait()

    async def second_worker():
        await first_entered.wait()
        async with _guard(database, "shared-thread"):
            second_entered.set()

    first_task = asyncio.create_task(first_worker())
    second_task = asyncio.create_task(second_worker())
    await asyncio.wait_for(first_entered.wait(), timeout=1)
    await asyncio.sleep(0.02)
    assert not second_entered.is_set()

    release_first.set()
    await asyncio.gather(first_task, second_task)

    assert second_entered.is_set()
    assert database.held_lock_count == 0
    assert database.calls["unlock"] == 2


@pytest.mark.asyncio
async def test_different_threads_can_hold_guards_concurrently():
    database = _FakeAdvisoryDatabase()
    both_entered = asyncio.Event()
    release = asyncio.Event()
    active = 0

    async def worker(thread_id: str):
        nonlocal active
        async with _guard(database, thread_id):
            active += 1
            if active == 2:
                both_entered.set()
            await release.wait()
            active -= 1

    tasks = [
        asyncio.create_task(worker("thread-a")),
        asyncio.create_task(worker("thread-b")),
    ]
    await asyncio.wait_for(both_entered.wait(), timeout=1)
    assert database.held_lock_count == 2

    release.set()
    await asyncio.gather(*tasks)

    assert database.held_lock_count == 0


@pytest.mark.asyncio
async def test_guard_releases_lock_when_guarded_body_raises():
    database = _FakeAdvisoryDatabase()

    with pytest.raises(RuntimeError, match="body failed"):
        async with _guard(database, "thread-1"):
            raise RuntimeError("body failed")

    assert database.held_lock_count == 0
    async with _guard(database, "thread-1"):
        assert database.held_lock_count == 1
    assert database.held_lock_count == 0


@pytest.mark.asyncio
async def test_guard_releases_lock_when_guarded_task_is_cancelled():
    database = _FakeAdvisoryDatabase()
    entered = asyncio.Event()

    async def worker():
        async with _guard(database, "thread-1"):
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(worker())
    await asyncio.wait_for(entered.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert database.held_lock_count == 0
    assert database.calls["unlock"] == 1


@pytest.mark.asyncio
async def test_unlock_failure_invalidates_connection_instead_of_pooling_lock():
    database = _FakeAdvisoryDatabase()
    database.fail_unlock = True

    with pytest.raises(RuntimeError, match="advisory lock was not held"):
        async with _guard(database, "thread-1"):
            pass

    assert database.held_lock_count == 0
    assert database.connections[0].invalidated is True


@pytest.mark.asyncio
async def test_contended_guard_times_out_and_discards_its_connection():
    database = _FakeAdvisoryDatabase()

    async with _guard(database, "thread-1"):
        with pytest.raises(ThreadMaterializationLockTimeout):
            async with _guard(database, "thread-1", timeout=0.01):
                raise AssertionError("unreachable")

        assert database.held_lock_count == 1
        assert database.connections[-1].invalidated is True

    assert database.held_lock_count == 0


@pytest.mark.parametrize(
    ("backend", "debug", "dialect"),
    [
        ("process", False, None),
        ("postgresql", False, "sqlite"),
        ("unsupported", True, None),
    ],
)
def test_invalid_or_unsafe_lock_configuration_fails_closed(
    backend: str,
    debug: bool,
    dialect: str | None,
):
    with pytest.raises(ThreadMaterializationLockConfigurationError):
        _build_thread_guard_factory(
            backend=backend,
            debug=debug,
            timeout_seconds=1,
            poll_interval_seconds=0.01,
            connection_factory=None,
            database_dialect=dialect,
        )


@pytest.mark.asyncio
async def test_debug_process_backend_operates_without_database_connection():
    guard_factory = _build_thread_guard_factory(
        backend="process",
        debug=True,
        timeout_seconds=1,
        poll_interval_seconds=0.01,
    )

    async with guard_factory("local-development-thread"):
        pass


@pytest.mark.parametrize(
    "updates",
    [
        {
            "DEBUG": False,
            "THREAD_MATERIALIZATION_LOCK_BACKEND": "process",
        },
        {
            "DATABASE_URL": "sqlite+aiosqlite:///:memory:",
            "THREAD_MATERIALIZATION_LOCK_BACKEND": "postgresql",
        },
        {
            "THREAD_MATERIALIZATION_LOCK_TIMEOUT_SECONDS": 0.1,
            "THREAD_MATERIALIZATION_LOCK_POLL_INTERVAL_SECONDS": 0.2,
        },
    ],
)
def test_settings_reject_unsafe_thread_lock_configuration(updates: dict):
    values = settings.model_dump()
    values.update(updates)

    with pytest.raises(ValidationError):
        Settings.model_validate(values)
