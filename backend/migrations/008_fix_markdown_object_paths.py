"""修复历史 markdown_path 路径错误（kb/kb/...）并迁移对象到规范路径。"""

from __future__ import annotations

import argparse
import asyncio
import os
from dataclasses import dataclass

import asyncpg
from minio import Minio
from minio.commonconfig import CopySource


@dataclass
class Row:
    doc_id: str
    kb_id: str
    owner_id: str
    markdown_path: str


def _to_object_name(markdown_path: str, bucket: str) -> str:
    prefix = f"{bucket}/"
    if not markdown_path.startswith(prefix):
        raise ValueError(f"invalid markdown_path (missing bucket prefix): {markdown_path}")
    object_name = markdown_path[len(prefix):]
    if not object_name or object_name.startswith("/") or ".." in object_name or "\\" in object_name:
        raise ValueError(f"invalid markdown object: {markdown_path}")
    return object_name


def _exists(client: Minio, bucket: str, object_name: str) -> bool:
    try:
        client.stat_object(bucket, object_name)
        return True
    except Exception:
        return False


def _same_object(client: Minio, bucket: str, left: str, right: str) -> bool:
    """用 size + etag 判断两个对象内容是否一致。"""
    try:
        left_stat = client.stat_object(bucket, left)
        right_stat = client.stat_object(bucket, right)
    except Exception:
        return False
    return (
        int(left_stat.size) == int(right_stat.size)
        and str(left_stat.etag or "") == str(right_stat.etag or "")
    )


async def run(dry_run: bool) -> int:
    dsn = os.environ["DATABASE_URL"].replace("+asyncpg", "")
    bucket = os.environ["MINIO_BUCKET"]
    endpoint = os.environ["MINIO_ENDPOINT"]
    access_key = os.environ["MINIO_ACCESS_KEY"]
    secret_key = os.environ["MINIO_SECRET_KEY"]
    secure = os.environ.get("MINIO_SECURE", "false").lower() == "true"

    client = Minio(
        endpoint,
        access_key=access_key,
        secret_key=secret_key,
        secure=secure,
    )

    conn = await asyncpg.connect(dsn)
    try:
        rows_raw = await conn.fetch(
            """
            select
                d.id::text as doc_id,
                d.kb_id::text as kb_id,
                kb.owner_id::text as owner_id,
                d.markdown_path
            from kb_documents d
            join knowledge_bases kb on kb.id = d.kb_id
            where d.markdown_path is not null
            order by d.updated_at asc
            """
        )
        rows = [Row(**dict(row)) for row in rows_raw]

        scanned = len(rows)
        moved = 0
        updated_only = 0
        unchanged = 0
        errors = 0

        for row in rows:
            expected_object = f"kb/{row.owner_id}/{row.kb_id}/markdown/{row.doc_id}.md"
            expected_path = f"{bucket}/{expected_object}"

            try:
                current_object = _to_object_name(row.markdown_path, bucket)
            except Exception as e:
                errors += 1
                print(f"[ERROR] {row.doc_id}: {e}")
                continue

            if current_object == expected_object and row.markdown_path == expected_path:
                unchanged += 1
                continue

            current_exists = _exists(client, bucket, current_object)
            expected_exists = _exists(client, bucket, expected_object)

            if not current_exists and not expected_exists:
                errors += 1
                print(
                    f"[ERROR] {row.doc_id}: both source and target missing "
                    f"(source={current_object}, target={expected_object})"
                )
                continue

            if not dry_run:
                if current_exists and not expected_exists:
                    client.copy_object(
                        bucket,
                        expected_object,
                        CopySource(bucket, current_object),
                    )
                    expected_exists = True
                elif current_exists and expected_exists and current_object != expected_object:
                    if not _same_object(client, bucket, current_object, expected_object):
                        errors += 1
                        print(
                            f"[ERROR] {row.doc_id}: source/target both exist but content differs "
                            f"(source={current_object}, target={expected_object})"
                        )
                        continue

                await conn.execute(
                    "update kb_documents set markdown_path=$1 where id=$2::uuid",
                    expected_path,
                    row.doc_id,
                )

                if current_exists and current_object != expected_object:
                    try:
                        client.remove_object(bucket, current_object)
                    except Exception as e:
                        print(
                            f"[WARN] {row.doc_id}: failed to remove source after DB update "
                            f"(source={current_object}, error={e})"
                        )

            if current_exists and current_object != expected_object:
                moved += 1
            else:
                updated_only += 1

        print(
            "summary:",
            {
                "scanned": scanned,
                "moved": moved,
                "updated_only": updated_only,
                "unchanged": unchanged,
                "errors": errors,
                "dry_run": dry_run,
            },
        )

        return 0 if errors == 0 else 1
    finally:
        await conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Fix markdown object paths")
    parser.add_argument("--dry-run", action="store_true", help="Only print planned operations")
    args = parser.parse_args()
    return asyncio.run(run(dry_run=args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
