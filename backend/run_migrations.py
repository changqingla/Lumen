"""Deterministic database migration runner."""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

import asyncpg

from config.settings import settings

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
MIGRATION_FILE_PATTERN = re.compile(r"^(?P<version>\d{3})_(?P<name>.+)\.(?P<kind>sql|py)$")
MIGRATION_TABLE_NAME = "schema_migrations"


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


async def _load_applied_migrations(conn: asyncpg.Connection) -> Dict[int, Dict[str, str]]:
    rows = await conn.fetch(
        f"""
        SELECT version, filename, kind, checksum
        FROM {MIGRATION_TABLE_NAME}
        ORDER BY version ASC
        """
    )
    return {
        int(row["version"]): {
            "filename": str(row["filename"]),
            "kind": str(row["kind"]),
            "checksum": str(row["checksum"]),
        }
        for row in rows
    }


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
    migrations = _discover_migrations()
    dsn = _database_dsn_for_asyncpg()

    print(f"Connecting to database using {MIGRATION_TABLE_NAME}...")
    conn = await asyncpg.connect(dsn)
    try:
        await _ensure_migration_table(conn)
        applied_migrations = await _load_applied_migrations(conn)

        for migration in migrations:
            applied = applied_migrations.get(migration.version)
            if applied is not None:
                _validate_applied_migration(migration, applied)
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
        await conn.close()


def main() -> int:
    try:
        asyncio.run(run_migrations())
    except (RuntimeError, asyncpg.PostgresError, OSError, subprocess.SubprocessError) as exc:
        print(f"Migration failed: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
