from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path

os.environ["DEBUG"] = "false"

import pytest

import run_migrations as migration_runner


class _FakeTransaction:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None


class _FakeMigrationDatabase:
    def __init__(self) -> None:
        self._mutex = asyncio.Lock()
        self.owner: _FakeConnection | object | None = None
        self.connections: list[_FakeConnection] = []
        self.history: dict[int, dict[str, str]] = {}
        self.apply_count = 0
        self.active_applies = 0
        self.max_active_applies = 0
        self.try_lock_count = 0
        self.unlock_count = 0

    async def connect(self, dsn: str) -> _FakeConnection:
        connection = _FakeConnection(self, dsn)
        self.connections.append(connection)
        return connection

    async def try_lock(self, connection: _FakeConnection) -> bool:
        async with self._mutex:
            self.try_lock_count += 1
            if self.owner is None:
                self.owner = connection
                return True
            return self.owner is connection

    async def unlock(self, connection: _FakeConnection) -> bool:
        async with self._mutex:
            if self.owner is not connection:
                return False
            self.owner = None
            self.unlock_count += 1
            return True

    async def close(self, connection: _FakeConnection) -> None:
        async with self._mutex:
            if self.owner is connection:
                self.owner = None


class _FakeConnection:
    def __init__(self, database: _FakeMigrationDatabase, dsn: str) -> None:
        self.database = database
        self.dsn = dsn
        self.closed = False

    async def fetchval(self, sql: str, lock_key: int) -> bool:
        assert lock_key == migration_runner._migration_advisory_lock_key()
        if "pg_try_advisory_lock" in sql:
            return await self.database.try_lock(self)
        if "pg_advisory_unlock" in sql:
            return await self.database.unlock(self)
        raise AssertionError(f"Unexpected scalar SQL: {sql}")

    async def fetch(self, sql: str) -> list[dict[str, str | int]]:
        assert "FROM schema_migrations" in sql
        return [
            {"version": version, **self.database.history[version]}
            for version in sorted(self.database.history)
        ]

    async def execute(self, sql: str, *args: object) -> None:
        if "CREATE TABLE IF NOT EXISTS schema_migrations" in sql:
            return

        if "INSERT INTO schema_migrations" in sql:
            version, filename, kind, checksum = args
            version = int(version)
            if version in self.database.history:
                raise AssertionError(f"duplicate migration version {version}")
            self.database.history[version] = {
                "filename": str(filename),
                "kind": str(kind),
                "checksum": str(checksum),
            }
            return

        self.database.active_applies += 1
        self.database.max_active_applies = max(
            self.database.max_active_applies,
            self.database.active_applies,
        )
        try:
            await asyncio.sleep(0.02)
            self.database.apply_count += 1
        finally:
            self.database.active_applies -= 1

    def transaction(self) -> _FakeTransaction:
        return _FakeTransaction()

    async def close(self) -> None:
        self.closed = True
        await self.database.close(self)


def _migration(
    version: int,
    *,
    path: Path | None = None,
    kind: str = "sql",
) -> migration_runner.MigrationFile:
    filename = f"{version:03d}_migration.{kind}"
    return migration_runner.MigrationFile(
        version=version,
        filename=filename,
        kind=kind,
        path=path or Path(filename),
        checksum=hashlib.sha256(filename.encode()).hexdigest(),
    )


def _applied(migration: migration_runner.MigrationFile) -> dict[str, str]:
    return {
        "filename": migration.filename,
        "kind": migration.kind,
        "checksum": migration.checksum,
    }


class _RowsConnection:
    def __init__(self, rows: list[dict[str, str | int]]) -> None:
        self.rows = rows

    async def fetch(self, sql: str) -> list[dict[str, str | int]]:
        assert "FROM schema_migrations" in sql
        return self.rows


def test_migration_lock_key_is_fixed_domain_separated_signed_64_bit() -> None:
    expected = int.from_bytes(
        hashlib.sha256(migration_runner.MIGRATION_LOCK_DOMAIN).digest()[:8],
        byteorder="big",
        signed=True,
    )

    assert expected == 2726746670395167455
    assert migration_runner._migration_advisory_lock_key() == 2726746670395167455
    assert -(1 << 63) <= expected < (1 << 63)


def test_main_redacts_connection_failure(monkeypatch, capsys) -> None:
    marker = "private-database-connection-detail"

    def fail(coroutine) -> None:
        coroutine.close()
        raise OSError(marker)

    monkeypatch.setattr(migration_runner.asyncio, "run", fail)

    assert migration_runner.main() == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert marker not in captured.err
    assert "error_type=OSError" in captured.err


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("rows", "message"),
    [
        (
            [
                {
                    "version": 0,
                    "filename": "000_first.sql",
                    "kind": "sql",
                    "checksum": "0" * 64,
                },
                {
                    "version": 0,
                    "filename": "000_second.sql",
                    "kind": "sql",
                    "checksum": "1" * 64,
                },
            ],
            "duplicate version 000",
        ),
        (
            [
                {
                    "version": 0,
                    "filename": "shared.sql",
                    "kind": "sql",
                    "checksum": "0" * 64,
                },
                {
                    "version": 1,
                    "filename": "shared.sql",
                    "kind": "sql",
                    "checksum": "1" * 64,
                },
            ],
            "duplicate filename shared.sql",
        ),
    ],
)
async def test_load_applied_history_rejects_duplicate_ledger_rows(
    rows: list[dict[str, str | int]],
    message: str,
) -> None:
    with pytest.raises(RuntimeError, match=message):
        await migration_runner._load_applied_migrations(_RowsConnection(rows))


@pytest.mark.asyncio
async def test_concurrent_runners_serialize_discovery_validation_and_apply(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    database = _FakeMigrationDatabase()
    migration_path = tmp_path / "000_migration.sql"
    migration_path.write_text("SELECT 1;", encoding="utf-8")
    migration = _migration(0, path=migration_path)
    discovery_lock_states: list[bool] = []

    def discover() -> list[migration_runner.MigrationFile]:
        discovery_lock_states.append(database.owner is not None)
        return [migration]

    monkeypatch.setattr(migration_runner.asyncpg, "connect", database.connect)
    monkeypatch.setattr(migration_runner, "_discover_migrations", discover)
    monkeypatch.setenv(migration_runner.MIGRATION_LOCK_TIMEOUT_ENV, "1")

    await asyncio.gather(
        migration_runner.run_migrations(),
        migration_runner.run_migrations(),
    )

    assert discovery_lock_states == [True, True]
    assert database.apply_count == 1
    assert database.max_active_applies == 1
    assert database.history == {0: _applied(migration)}
    assert database.try_lock_count >= 3
    assert database.unlock_count == 2
    assert database.owner is None
    assert all(connection.closed for connection in database.connections)


@pytest.mark.asyncio
async def test_lock_timeout_fails_closed_and_closes_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _FakeMigrationDatabase()
    database.owner = object()

    def unexpected_discovery() -> list[migration_runner.MigrationFile]:
        raise AssertionError("migration discovery must not run without the lock")

    monkeypatch.setattr(migration_runner.asyncpg, "connect", database.connect)
    monkeypatch.setattr(
        migration_runner,
        "_discover_migrations",
        unexpected_discovery,
    )
    monkeypatch.setenv(migration_runner.MIGRATION_LOCK_TIMEOUT_ENV, "0.01")

    with pytest.raises(migration_runner.MigrationLockTimeout, match="Timed out"):
        await migration_runner.run_migrations()

    assert database.unlock_count == 0
    assert len(database.connections) == 1
    assert database.connections[0].closed is True


@pytest.mark.asyncio
async def test_repeated_cancellation_cannot_interrupt_unlock_or_connection_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _FakeMigrationDatabase()
    body_started = asyncio.Event()
    unlock_started = asyncio.Event()
    allow_unlock = asyncio.Event()
    close_started = asyncio.Event()
    allow_close = asyncio.Event()
    close_completed = asyncio.Event()
    original_unlock = database.unlock
    original_close = database.close

    async def block_while_holding_lock(connection: _FakeConnection) -> None:
        body_started.set()
        await asyncio.Event().wait()

    async def delayed_unlock(connection: _FakeConnection) -> bool:
        unlock_started.set()
        await allow_unlock.wait()
        return await original_unlock(connection)

    async def delayed_close(connection: _FakeConnection) -> None:
        close_started.set()
        await allow_close.wait()
        await original_close(connection)
        close_completed.set()

    monkeypatch.setattr(migration_runner.asyncpg, "connect", database.connect)
    monkeypatch.setattr(
        migration_runner,
        "_discover_migrations",
        lambda: [_migration(0)],
    )
    monkeypatch.setattr(
        migration_runner,
        "_ensure_migration_table",
        block_while_holding_lock,
    )
    monkeypatch.setattr(database, "unlock", delayed_unlock)
    monkeypatch.setattr(database, "close", delayed_close)

    runner_task = asyncio.create_task(migration_runner.run_migrations())
    await asyncio.wait_for(body_started.wait(), timeout=1)
    runner_task.cancel()
    await asyncio.wait_for(unlock_started.wait(), timeout=1)

    runner_task.cancel()
    await asyncio.sleep(0)
    runner_task.cancel()
    await asyncio.sleep(0)
    assert not runner_task.done()

    allow_unlock.set()
    await asyncio.wait_for(close_started.wait(), timeout=1)
    runner_task.cancel()
    await asyncio.sleep(0)
    runner_task.cancel()
    await asyncio.sleep(0)
    assert not runner_task.done()

    allow_close.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(runner_task, timeout=1)

    assert database.unlock_count == 1
    assert database.owner is None
    assert close_completed.is_set()
    assert database.connections[0].closed is True


@pytest.mark.asyncio
async def test_database_at_020_is_rejected_by_repository_ending_at_019(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _FakeMigrationDatabase()
    repository_migrations = [_migration(version) for version in range(20)]
    database_migrations = repository_migrations + [_migration(20)]
    database.history = {
        migration.version: _applied(migration) for migration in database_migrations
    }

    monkeypatch.setattr(migration_runner.asyncpg, "connect", database.connect)
    monkeypatch.setattr(
        migration_runner,
        "_discover_migrations",
        lambda: repository_migrations,
    )

    with pytest.raises(RuntimeError, match=r"not present.*020"):
        await migration_runner.run_migrations()

    assert database.apply_count == 0
    assert database.unlock_count == 1
    assert database.connections[0].closed is True


def test_applied_history_rejects_a_hole() -> None:
    migrations = [_migration(version) for version in range(3)]
    applied = {
        migrations[0].version: _applied(migrations[0]),
        migrations[2].version: _applied(migrations[2]),
    }

    with pytest.raises(RuntimeError, match="not an exact prefix"):
        migration_runner._validate_applied_history(migrations, applied)


@pytest.mark.parametrize(
    ("field", "invalid_value", "message"),
    [
        ("filename", "000_renamed.sql", "filename mismatch"),
        ("kind", "py", "kind mismatch"),
        ("checksum", "0" * 64, "checksum mismatch"),
    ],
)
def test_applied_history_rejects_metadata_mismatch(
    field: str,
    invalid_value: str,
    message: str,
) -> None:
    migration = _migration(0)
    metadata = _applied(migration)
    metadata[field] = invalid_value

    with pytest.raises(RuntimeError, match=message):
        migration_runner._validate_applied_history(
            [migration],
            {migration.version: metadata},
        )
