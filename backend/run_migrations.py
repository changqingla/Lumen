"""Deterministic database migration runner."""

from __future__ import annotations

import asyncio
import hashlib
import math
import os
import re
import subprocess
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, Awaitable, Dict, List

import asyncpg

from config.settings import settings

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
MIGRATION_FILE_PATTERN = re.compile(
    r"^(?P<version>\d{3})_(?P<name>.+)\.(?P<kind>sql|py)$"
)
MIGRATION_TABLE_NAME = "schema_migrations"
MIGRATION_LOCK_DOMAIN = b"lumen/backend/schema-migrations/session-lock/v1"
MIGRATION_LOCK_TIMEOUT_ENV = "MIGRATION_LOCK_TIMEOUT_SECONDS"
DEFAULT_MIGRATION_LOCK_TIMEOUT_SECONDS = 60.0
MIGRATION_LOCK_POLL_INTERVAL_SECONDS = 0.1
_TRY_MIGRATION_LOCK_SQL = "SELECT pg_try_advisory_lock($1::bigint)"
_RELEASE_MIGRATION_LOCK_SQL = "SELECT pg_advisory_unlock($1::bigint)"


class MigrationLockTimeout(RuntimeError):
    """Raised when another migration runner holds the database lock too long."""


@dataclass(frozen=True)
class MigrationFile:
    version: int
    filename: str
    kind: str
    path: Path
    checksum: str


def _database_dsn_for_asyncpg() -> str:
    raw_url = str(settings.DATABASE_URL).strip()
    if raw_url.startswith("postgresql+asyncpg://"):
        return "postgresql://" + raw_url[len("postgresql+asyncpg://") :]
    if raw_url.startswith("postgres://"):
        return "postgresql://" + raw_url[len("postgres://") :]
    return raw_url


def _sha256_for_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _migration_advisory_lock_key() -> int:
    digest = hashlib.sha256(MIGRATION_LOCK_DOMAIN).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def _migration_lock_timeout_seconds() -> float:
    raw_value = os.environ.get(
        MIGRATION_LOCK_TIMEOUT_ENV,
        str(DEFAULT_MIGRATION_LOCK_TIMEOUT_SECONDS),
    )
    try:
        timeout_seconds = float(raw_value)
    except ValueError as exc:
        raise RuntimeError(
            f"{MIGRATION_LOCK_TIMEOUT_ENV} must be a positive finite number"
        ) from exc

    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise RuntimeError(
            f"{MIGRATION_LOCK_TIMEOUT_ENV} must be a positive finite number"
        )
    return timeout_seconds


async def _acquire_migration_advisory_lock(
    conn: asyncpg.Connection,
    *,
    timeout_seconds: float,
    poll_interval_seconds: float = MIGRATION_LOCK_POLL_INTERVAL_SECONDS,
) -> None:
    lock_key = _migration_advisory_lock_key()

    async def _poll_until_acquired() -> None:
        while not bool(await conn.fetchval(_TRY_MIGRATION_LOCK_SQL, lock_key)):
            await asyncio.sleep(poll_interval_seconds)

    try:
        async with asyncio.timeout(timeout_seconds):
            await _poll_until_acquired()
    except TimeoutError as exc:
        raise MigrationLockTimeout(
            "Timed out waiting for the PostgreSQL migration advisory lock after "
            f"{timeout_seconds:g} seconds"
        ) from exc


async def _release_migration_advisory_lock(conn: asyncpg.Connection) -> None:
    released = await conn.fetchval(
        _RELEASE_MIGRATION_LOCK_SQL,
        _migration_advisory_lock_key(),
    )
    if not released:
        raise RuntimeError(
            "PostgreSQL reported that the migration advisory lock was not held"
        )


async def _complete_cleanup_despite_cancellation(
    cleanup: Awaitable[None],
) -> None:
    """Finish session cleanup before propagating any repeated cancellation."""
    cleanup_task = asyncio.ensure_future(cleanup)
    cancellation: asyncio.CancelledError | None = None

    while not cleanup_task.done():
        try:
            await asyncio.shield(cleanup_task)
        except asyncio.CancelledError as exc:
            if cleanup_task.cancelled():
                raise
            cancellation = exc

    cleanup_task.result()
    if cancellation is not None:
        raise cancellation


@asynccontextmanager
async def _migration_advisory_lock(
    conn: asyncpg.Connection,
    *,
    timeout_seconds: float,
    poll_interval_seconds: float = MIGRATION_LOCK_POLL_INTERVAL_SECONDS,
) -> AsyncIterator[None]:
    await _acquire_migration_advisory_lock(
        conn,
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
    )
    try:
        yield
    finally:
        await _complete_cleanup_despite_cancellation(
            _release_migration_advisory_lock(conn)
        )


def _discover_migrations() -> List[MigrationFile]:
    if not MIGRATIONS_DIR.exists():
        raise RuntimeError(f"Migration directory not found: {MIGRATIONS_DIR}")

    migrations: List[MigrationFile] = []
    seen_versions: Dict[int, str] = {}

    for path in sorted(MIGRATIONS_DIR.iterdir(), key=lambda item: item.name):
        if not path.is_file():
            continue

        match = MIGRATION_FILE_PATTERN.match(path.name)
        if match is None:
            continue

        version = int(match.group("version"))
        if version in seen_versions:
            raise RuntimeError(
                f"Duplicate migration version detected: {version:03d} "
                f"({seen_versions[version]} and {path.name})"
            )

        seen_versions[version] = path.name
        migrations.append(
            MigrationFile(
                version=version,
                filename=path.name,
                kind=match.group("kind"),
                path=path,
                checksum=_sha256_for_file(path),
            )
        )

    if not migrations:
        raise RuntimeError(f"No migrations found in {MIGRATIONS_DIR}")

    expected_versions = list(range(migrations[0].version, migrations[-1].version + 1))
    actual_versions = [migration.version for migration in migrations]
    if actual_versions != expected_versions:
        raise RuntimeError(
            "Migration versions must be contiguous. "
            f"Expected {expected_versions}, got {actual_versions}"
        )

    return migrations


async def _ensure_migration_table(conn: asyncpg.Connection) -> None:
    await conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {MIGRATION_TABLE_NAME} (
            version INTEGER PRIMARY KEY,
            filename VARCHAR(255) NOT NULL UNIQUE,
            kind VARCHAR(16) NOT NULL,
            checksum CHAR(64) NOT NULL,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )


async def _load_applied_migrations(
    conn: asyncpg.Connection,
) -> Dict[int, Dict[str, str]]:
    rows = await conn.fetch(
        f"""
        SELECT version, filename, kind, checksum
        FROM {MIGRATION_TABLE_NAME}
        ORDER BY version ASC
        """
    )
    applied_migrations: Dict[int, Dict[str, str]] = {}
    seen_filenames: set[str] = set()
    for row in rows:
        version = int(row["version"])
        filename = str(row["filename"])
        if version in applied_migrations:
            raise RuntimeError(
                f"Database migration history contains duplicate version {version:03d}"
            )
        if filename in seen_filenames:
            raise RuntimeError(
                f"Database migration history contains duplicate filename {filename}"
            )
        applied_migrations[version] = {
            "filename": filename,
            "kind": str(row["kind"]),
            "checksum": str(row["checksum"]),
        }
        seen_filenames.add(filename)
    return applied_migrations


async def _record_applied_migration(
    conn: asyncpg.Connection,
    migration: MigrationFile,
) -> None:
    await conn.execute(
        f"""
        INSERT INTO {MIGRATION_TABLE_NAME} (version, filename, kind, checksum)
        VALUES ($1, $2, $3, $4)
        """,
        migration.version,
        migration.filename,
        migration.kind,
        migration.checksum,
    )


def _validate_applied_migration(
    migration: MigrationFile,
    applied: Dict[str, str],
) -> None:
    if applied["filename"] != migration.filename:
        raise RuntimeError(
            f"Migration version {migration.version:03d} filename mismatch: "
            f"database={applied['filename']} repo={migration.filename}"
        )
    if applied["kind"] != migration.kind:
        raise RuntimeError(
            f"Migration {migration.filename} kind mismatch: "
            f"database={applied['kind']} repo={migration.kind}"
        )
    if applied["checksum"] != migration.checksum:
        raise RuntimeError(
            f"Migration {migration.filename} checksum mismatch. "
            "The applied migration was modified after execution."
        )


def _validate_applied_history(
    migrations: List[MigrationFile],
    applied_migrations: Dict[int, Dict[str, str]],
) -> None:
    repository_versions = [migration.version for migration in migrations]
    repository_version_set = set(repository_versions)
    applied_versions = sorted(applied_migrations)
    unknown_versions = [
        version for version in applied_versions if version not in repository_version_set
    ]
    if unknown_versions:
        formatted_versions = ", ".join(f"{version:03d}" for version in unknown_versions)
        raise RuntimeError(
            "Database migration history contains version(s) that are not present in "
            f"this repository: {formatted_versions}. Refusing to run an older or "
            "incomplete application image."
        )

    expected_prefix = repository_versions[: len(applied_versions)]
    if applied_versions != expected_prefix:
        expected = ", ".join(f"{version:03d}" for version in expected_prefix) or "none"
        actual = ", ".join(f"{version:03d}" for version in applied_versions) or "none"
        raise RuntimeError(
            "Database migration history is not an exact prefix of this repository. "
            f"Expected applied versions [{expected}], found [{actual}]."
        )

    migrations_by_version = {migration.version: migration for migration in migrations}
    for version in applied_versions:
        _validate_applied_migration(
            migrations_by_version[version],
            applied_migrations[version],
        )


async def _apply_sql_migration(
    conn: asyncpg.Connection,
    migration: MigrationFile,
) -> None:
    sql = migration.path.read_text(encoding="utf-8")
    async with conn.transaction():
        await conn.execute(sql)
        await _record_applied_migration(conn, migration)


async def _apply_python_migration(
    conn: asyncpg.Connection,
    migration: MigrationFile,
) -> None:
    env = dict(os.environ)
    env.setdefault("DATABASE_URL", settings.DATABASE_URL)

    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(migration.path),
        cwd=str(MIGRATIONS_DIR),
        env=env,
    )
    return_code = await process.wait()
    if return_code != 0:
        raise RuntimeError(
            f"Python migration {migration.filename} failed with exit code {return_code}"
        )

    async with conn.transaction():
        await _record_applied_migration(conn, migration)


async def run_migrations() -> None:
    dsn = _database_dsn_for_asyncpg()
    lock_timeout_seconds = _migration_lock_timeout_seconds()

    print(f"Connecting to database using {MIGRATION_TABLE_NAME}...")
    conn = await asyncpg.connect(dsn)
    try:
        async with _migration_advisory_lock(
            conn,
            timeout_seconds=lock_timeout_seconds,
        ):
            migrations = _discover_migrations()
            await _ensure_migration_table(conn)
            applied_migrations = await _load_applied_migrations(conn)
            _validate_applied_history(migrations, applied_migrations)

            for migration in migrations:
                if migration.version in applied_migrations:
                    print(f"Skipping applied migration {migration.filename}")
                    continue

                print(f"Applying migration {migration.filename}")
                if migration.kind == "sql":
                    await _apply_sql_migration(conn, migration)
                elif migration.kind == "py":
                    await _apply_python_migration(conn, migration)
                else:
                    raise RuntimeError(f"Unsupported migration kind: {migration.kind}")

                print(f"Applied migration {migration.filename}")

            print("All migrations are up to date.")
    finally:
        await _complete_cleanup_despite_cancellation(conn.close())


def main() -> int:
    try:
        asyncio.run(run_migrations())
    except (
        RuntimeError,
        asyncpg.PostgresError,
        OSError,
        subprocess.SubprocessError,
    ) as exc:
        print(
            f"Migration failed (error_type={type(exc).__name__})",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
