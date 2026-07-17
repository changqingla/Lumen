"""Project session workspace assets into lumen thread uploads."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import AsyncIterable, AsyncIterator, Awaitable, Callable, Iterable
from contextlib import AbstractAsyncContextManager, asynccontextmanager, nullcontext
from pathlib import Path
import re
import tempfile
from typing import Any
from weakref import WeakValueDictionary

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None

from config.database import thread_materialization_lock_engine
from config.settings import settings
from modules.chat.services.insight_runtime_service import InsightRuntimeService, insight_runtime_service
from modules.chat.services.workspace_service import WorkspaceService
from schemas.workspace import WorkspaceAttachmentRecord
from utils.minio_client import download_file

logger = logging.getLogger(__name__)

ThreadGuardFactory = Callable[[str], AbstractAsyncContextManager[None]]
ConnectionFactory = Callable[[], Awaitable[AsyncConnection]]

_thread_materialization_locks: WeakValueDictionary[str, asyncio.Lock] = (
    WeakValueDictionary()
)
_thread_materialization_lock_guard = asyncio.Lock()
_thread_materialization_lock_dir = Path(tempfile.gettempdir()) / "lumen-thread-locks"
_THREAD_LOCK_KEY_NAMESPACE = b"lumen:thread-materialization:v1\0"
_TRY_ADVISORY_LOCK_SQL = text("SELECT pg_try_advisory_lock(:lock_key)")
_RELEASE_ADVISORY_LOCK_SQL = text("SELECT pg_advisory_unlock(:lock_key)")
_MANAGED_KB_FILENAME_RE = re.compile(
    r"^kb__[0-9a-fA-F-]{36}__[0-9a-fA-F-]{36}__"
    r"(?:[0-9a-f]{16}__)?[A-Za-z0-9._-]+\.md$"
)


class ThreadMaterializationLockError(RuntimeError):
    """The distributed thread guard could not be acquired or safely released."""


class ThreadMaterializationLockTimeout(ThreadMaterializationLockError, TimeoutError):
    """The distributed thread guard was not acquired before its deadline."""


class ThreadMaterializationLockConfigurationError(
    ThreadMaterializationLockError,
    ValueError,
):
    """The configured guard cannot provide the required deployment guarantees."""


async def _get_thread_materialization_lock(thread_id: str) -> asyncio.Lock:
    async with _thread_materialization_lock_guard:
        lock = _thread_materialization_locks.get(thread_id)
        if lock is None:
            lock = asyncio.Lock()
            _thread_materialization_locks[thread_id] = lock
        return lock


def _thread_advisory_lock_key(thread_id: str) -> int:
    normalized_thread_id = str(thread_id).strip()
    if not normalized_thread_id:
        raise ValueError("thread_id cannot be empty")
    digest = hashlib.sha256(
        _THREAD_LOCK_KEY_NAMESPACE + normalized_thread_id.encode("utf-8")
    ).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def _materialization_lock_path(thread_id: str) -> Path:
    unsigned_key = _thread_advisory_lock_key(thread_id) & ((1 << 64) - 1)
    return _thread_materialization_lock_dir / f"materialization-{unsigned_key:016x}.lock"


def _open_process_lock(lock_path: Path):
    if fcntl is None:
        raise ThreadMaterializationLockConfigurationError(
            "The process lock backend requires fcntl support"
        )
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    return lock_path.open("a+", encoding="utf-8")


def _try_acquire_process_lock(handle) -> bool:
    if fcntl is None:  # pragma: no cover - checked by _open_process_lock
        return False
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    return True


def _release_process_lock(handle) -> None:
    if handle is None or fcntl is None:
        return
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def _remaining_lock_time(*, deadline: float, thread_id: str) -> float:
    remaining = deadline - asyncio.get_running_loop().time()
    if remaining <= 0:
        raise ThreadMaterializationLockTimeout(
            f"Timed out acquiring the materialization guard for thread {thread_id!r}"
        )
    return remaining


async def _acquire_local_thread_lock(
    *,
    thread_id: str,
    deadline: float,
) -> asyncio.Lock:
    lock = await _get_thread_materialization_lock(thread_id)
    try:
        remaining = _remaining_lock_time(deadline=deadline, thread_id=thread_id)
        await asyncio.wait_for(
            lock.acquire(),
            timeout=remaining,
        )
    except TimeoutError as exc:
        raise ThreadMaterializationLockTimeout(
            f"Timed out acquiring the materialization guard for thread {thread_id!r}"
        ) from exc
    return lock


async def _await_cleanup(cleanup: Awaitable[None]) -> None:
    """Let lock cleanup finish even when the guarded task is being cancelled."""
    cleanup_task = asyncio.create_task(cleanup)
    try:
        await asyncio.shield(cleanup_task)
    except asyncio.CancelledError:
        try:
            await cleanup_task
        except Exception as exc:
            logger.error(
                "thread_materialization_lock stage=cancel_cleanup error_type=%s",
                type(exc).__name__,
            )
        raise


async def _invalidate_and_close_connection(connection: AsyncConnection) -> None:
    try:
        await connection.invalidate()
    finally:
        await connection.close()


async def _close_released_connection(connection: AsyncConnection) -> None:
    try:
        await connection.close()
    except Exception as exc:
        try:
            await _invalidate_and_close_connection(connection)
        except Exception as invalidate_exc:
            logger.error(
                "thread_materialization_lock stage=close_invalidate error_type=%s",
                type(invalidate_exc).__name__,
            )
        raise ThreadMaterializationLockError(
            "Failed to close the released PostgreSQL advisory lock connection"
        ) from exc


async def _release_postgres_advisory_lock(
    *,
    connection: AsyncConnection,
    lock_key: int,
    cleanup_timeout_seconds: float,
) -> None:
    try:
        unlocked = await asyncio.wait_for(
            connection.scalar(
                _RELEASE_ADVISORY_LOCK_SQL,
                {"lock_key": lock_key},
            ),
            timeout=cleanup_timeout_seconds,
        )
        if unlocked is not True:
            raise ThreadMaterializationLockError(
                "PostgreSQL reported that the thread advisory lock was not held"
            )
    except Exception as exc:
        try:
            await _invalidate_and_close_connection(connection)
        except Exception as invalidate_exc:
            logger.error(
                "thread_materialization_lock stage=unlock_invalidate error_type=%s",
                type(invalidate_exc).__name__,
            )
        if isinstance(exc, ThreadMaterializationLockError):
            raise
        raise ThreadMaterializationLockError(
            "Failed to release the PostgreSQL thread advisory lock"
        ) from exc
    await _close_released_connection(connection)


@asynccontextmanager
async def _acquire_postgres_advisory_lock(
    thread_id: str,
    *,
    connection_factory: ConnectionFactory,
    timeout_seconds: float,
    poll_interval_seconds: float,
    cleanup_timeout_seconds: float | None = None,
):
    lock_key = _thread_advisory_lock_key(thread_id)
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    connection: AsyncConnection | None = None

    try:
        remaining = _remaining_lock_time(deadline=deadline, thread_id=thread_id)
        connection = await asyncio.wait_for(
            connection_factory(),
            timeout=remaining,
        )
        while True:
            try:
                remaining = _remaining_lock_time(
                    deadline=deadline,
                    thread_id=thread_id,
                )
                acquired = await asyncio.wait_for(
                    connection.scalar(
                        _TRY_ADVISORY_LOCK_SQL,
                        {"lock_key": lock_key},
                    ),
                    timeout=remaining,
                )
            except asyncio.CancelledError:
                await _await_cleanup(_invalidate_and_close_connection(connection))
                connection = None
                raise
            except TimeoutError as exc:
                await _await_cleanup(_invalidate_and_close_connection(connection))
                connection = None
                raise ThreadMaterializationLockTimeout(
                    f"Timed out acquiring the materialization guard for thread {thread_id!r}"
                ) from exc
            except Exception as exc:
                await _await_cleanup(_invalidate_and_close_connection(connection))
                connection = None
                raise ThreadMaterializationLockError(
                    "Failed to acquire the PostgreSQL thread advisory lock"
                ) from exc

            if acquired is True:
                break
            remaining = _remaining_lock_time(deadline=deadline, thread_id=thread_id)
            await asyncio.sleep(min(poll_interval_seconds, remaining))
    except asyncio.CancelledError:
        if connection is not None:
            await _await_cleanup(_invalidate_and_close_connection(connection))
        raise
    except ThreadMaterializationLockError:
        if connection is not None:
            await _await_cleanup(_invalidate_and_close_connection(connection))
        raise
    except TimeoutError as exc:
        if connection is not None:
            await _await_cleanup(_invalidate_and_close_connection(connection))
        raise ThreadMaterializationLockTimeout(
            f"Timed out acquiring the materialization guard for thread {thread_id!r}"
        ) from exc
    except Exception as exc:
        if connection is not None:
            await _await_cleanup(_invalidate_and_close_connection(connection))
        raise ThreadMaterializationLockError(
            "Failed to open a PostgreSQL connection for the thread advisory lock"
        ) from exc

    try:
        yield
    finally:
        await _await_cleanup(
            _release_postgres_advisory_lock(
                connection=connection,
                lock_key=lock_key,
                cleanup_timeout_seconds=(
                    cleanup_timeout_seconds or timeout_seconds
                ),
            )
        )


@asynccontextmanager
async def _acquire_postgres_thread_materialization_guard(
    thread_id: str,
    *,
    connection_factory: ConnectionFactory,
    timeout_seconds: float,
    poll_interval_seconds: float,
):
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    lock = await _acquire_local_thread_lock(thread_id=thread_id, deadline=deadline)
    try:
        async with _acquire_postgres_advisory_lock(
            thread_id,
            connection_factory=connection_factory,
            timeout_seconds=_remaining_lock_time(
                deadline=deadline,
                thread_id=thread_id,
            ),
            poll_interval_seconds=poll_interval_seconds,
            cleanup_timeout_seconds=timeout_seconds,
        ):
            yield
    finally:
        lock.release()


@asynccontextmanager
async def _acquire_process_thread_materialization_guard(
    thread_id: str,
    *,
    timeout_seconds: float,
    poll_interval_seconds: float,
):
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    lock = await _acquire_local_thread_lock(thread_id=thread_id, deadline=deadline)
    process_lock_handle = None
    try:
        process_lock_handle = _open_process_lock(_materialization_lock_path(thread_id))
        while not _try_acquire_process_lock(process_lock_handle):
            remaining = _remaining_lock_time(deadline=deadline, thread_id=thread_id)
            await asyncio.sleep(min(poll_interval_seconds, remaining))
        yield
    finally:
        _release_process_lock(process_lock_handle)
        lock.release()


def _build_thread_guard_factory(
    *,
    backend: str,
    debug: bool,
    timeout_seconds: float,
    poll_interval_seconds: float,
    connection_factory: ConnectionFactory | None = None,
    database_dialect: str | None = None,
) -> ThreadGuardFactory:
    normalized_backend = str(backend).strip().lower()
    if timeout_seconds <= 0 or poll_interval_seconds <= 0:
        raise ThreadMaterializationLockConfigurationError(
            "Thread materialization lock timing settings must be positive"
        )
    if poll_interval_seconds > timeout_seconds:
        raise ThreadMaterializationLockConfigurationError(
            "Thread materialization lock poll interval cannot exceed its timeout"
        )

    if normalized_backend == "postgresql":
        if database_dialect != "postgresql" or connection_factory is None:
            raise ThreadMaterializationLockConfigurationError(
                "The PostgreSQL thread lock backend requires a PostgreSQL AsyncEngine"
            )

        def postgres_guard(thread_id: str) -> AbstractAsyncContextManager[None]:
            return _acquire_postgres_thread_materialization_guard(
                thread_id,
                connection_factory=connection_factory,
                timeout_seconds=timeout_seconds,
                poll_interval_seconds=poll_interval_seconds,
            )

        return postgres_guard

    if normalized_backend == "process":
        if not debug:
            raise ThreadMaterializationLockConfigurationError(
                "The process thread lock backend is only allowed when DEBUG=true"
            )

        def process_guard(thread_id: str) -> AbstractAsyncContextManager[None]:
            return _acquire_process_thread_materialization_guard(
                thread_id,
                timeout_seconds=timeout_seconds,
                poll_interval_seconds=poll_interval_seconds,
            )

        return process_guard

    raise ThreadMaterializationLockConfigurationError(
        f"Unsupported thread materialization lock backend: {backend!r}"
    )


_default_thread_guard_factory = _build_thread_guard_factory(
    backend=settings.THREAD_MATERIALIZATION_LOCK_BACKEND,
    debug=settings.DEBUG,
    timeout_seconds=settings.THREAD_MATERIALIZATION_LOCK_TIMEOUT_SECONDS,
    poll_interval_seconds=settings.THREAD_MATERIALIZATION_LOCK_POLL_INTERVAL_SECONDS,
    connection_factory=(
        thread_materialization_lock_engine.connect
        if thread_materialization_lock_engine is not None
        else None
    ),
    database_dialect=(
        thread_materialization_lock_engine.dialect.name
        if thread_materialization_lock_engine is not None
        else None
    ),
)


class ThreadMaterializationService:
    """Synchronize session-scoped workspace materials into an Insight thread."""

    def __init__(
        self,
        runtime_service: InsightRuntimeService | None = None,
        thread_guard_factory: ThreadGuardFactory | None = None,
    ) -> None:
        self.runtime_service = runtime_service or insight_runtime_service
        self._thread_guard_factory = (
            thread_guard_factory or _default_thread_guard_factory
        )

    def thread_guard(self, thread_id: str):
        """Serialize Runtime file reconciliation across hosts and API workers."""
        return self._thread_guard_factory(thread_id)

    async def sync_session_workspace(
        self,
        *,
        session_id: str,
        user_id: str,
        thread_id: str,
        guard_acquired: bool = False,
    ) -> list[dict[str, Any]]:
        guard = nullcontext() if guard_acquired else self.thread_guard(thread_id)
        async with guard:
            workspace_service = WorkspaceService(session_id=session_id, user_id=user_id)
            manifest = await workspace_service.load_manifest()
            assets = [
                asset
                for asset in manifest.assets
                if asset.role in {"source", "derived"}
            ]
            if not assets:
                return []

            existing_uploads = await self.runtime_service.list_thread_uploads(thread_id)
            existing_by_filename = {
                str(item.get("filename", "")).strip(): item
                for item in existing_uploads
                if str(item.get("filename", "")).strip()
            }

            materialized: list[dict[str, Any]] = []
            for asset in assets:
                target_filename = self._build_target_filename(asset)
                existing = existing_by_filename.get(target_filename)
                if existing is not None:
                    materialized.append(
                        self._build_materialized_payload(
                            asset,
                            target_filename=target_filename,
                            uploaded_file=existing,
                            synced=False,
                        )
                    )
                    continue

                file_bytes = await download_file(asset.object_path)
                uploaded = await self.runtime_service.upload_bytes(
                    thread_id=thread_id,
                    filename=target_filename,
                    data=file_bytes,
                    content_type=asset.mime_type,
                )
                materialized.append(
                    self._build_materialized_payload(
                        asset,
                        target_filename=target_filename,
                        uploaded_file=uploaded,
                        synced=True,
                    )
                )
            return materialized

    async def sync_knowledge_documents(
        self,
        *,
        thread_id: str,
        knowledge_documents: AsyncIterable[dict[str, Any]] | Iterable[dict[str, Any]],
        previous_materialized: list[dict[str, Any]] | None = None,
        guard_acquired: bool = False,
        defer_cleanup: bool = False,
    ) -> list[dict[str, Any]]:
        guard = nullcontext() if guard_acquired else self.thread_guard(thread_id)
        async with guard:
            existing_uploads = await self.runtime_service.list_thread_uploads(thread_id)
            existing_by_filename = {
                str(item.get("filename", "")).strip(): item
                for item in existing_uploads
                if str(item.get("filename", "")).strip()
            }

            previous_by_version: dict[tuple[str, str, str], str] = {}
            managed_filenames: set[str] = set()
            for record in previous_materialized or []:
                if not isinstance(record, dict):
                    continue
                kb_id = str(record.get("kb_id", "")).strip()
                doc_id = str(record.get("doc_id", "")).strip()
                content_sha256 = str(record.get("content_sha256", "")).strip().lower()
                filename = str(record.get("thread_filename", "")).strip()
                if not filename or Path(filename).name != filename:
                    continue
                managed_filenames.add(filename)
                if kb_id and doc_id and re.fullmatch(r"[0-9a-f]{64}", content_sha256):
                    previous_by_version[(kb_id, doc_id, content_sha256)] = filename

            # ``kb__<uuid>__<uuid>__...md`` is a reserved Runtime namespace.
            # Scanning it on every reconciliation also recovers the crash window
            # where upload succeeded but the session manifest was not committed.
            managed_filenames.update(
                filename
                for filename in existing_by_filename
                if _MANAGED_KB_FILENAME_RE.fullmatch(filename)
            )

            materialized: list[dict[str, Any]] = []
            desired_filenames: set[str] = set()
            async for document in self._iterate_knowledge_documents(knowledge_documents):
                kb_id = str(document.get("kb_id", "")).strip()
                doc_id = str(document.get("doc_id", "")).strip()
                name = str(document.get("name", "")).strip() or f"{doc_id}.md"
                markdown_content = document.get("content")
                document_revision = str(document.get("document_revision", "")).strip()
                if (
                    not kb_id
                    or not doc_id
                    or not document_revision
                    or not isinstance(markdown_content, str)
                ):
                    raise ValueError("Knowledge materialization record is incomplete")

                markdown_bytes = markdown_content.encode("utf-8")
                content_sha256 = hashlib.sha256(markdown_bytes).hexdigest()

                target_filename = self._build_kb_target_filename(
                    kb_id=kb_id,
                    doc_id=doc_id,
                    name=name,
                    content_sha256=content_sha256,
                )
                previous_filename = previous_by_version.get(
                    (kb_id, doc_id, content_sha256)
                )
                reusable_filename = previous_filename or target_filename
                existing = existing_by_filename.get(reusable_filename)
                if previous_filename is None:
                    existing = None
                if existing is not None:
                    try:
                        integrity = await self.runtime_service.get_thread_upload_integrity(
                            thread_id=thread_id,
                            filename=reusable_filename,
                        )
                        if (
                            str(integrity.get("filename", "")).strip()
                            != reusable_filename
                            or int(integrity.get("size")) != len(markdown_bytes)
                            or str(integrity.get("sha256", "")).strip().lower()
                            != content_sha256
                        ):
                            existing = None
                    except (TypeError, ValueError):
                        existing = None
                    except httpx.HTTPStatusError as exc:
                        if exc.response.status_code == 404:
                            existing = None
                        else:
                            raise
                if existing is not None:
                    desired_filenames.add(reusable_filename)
                    materialized.append(
                        self._build_kb_materialized_payload(
                            kb_id=kb_id,
                            doc_id=doc_id,
                            name=name,
                            target_filename=reusable_filename,
                            uploaded_file=existing,
                            synced=False,
                            size_bytes=len(markdown_bytes),
                            content_sha256=content_sha256,
                            document_revision=document_revision,
                        )
                    )
                    continue

                uploaded = await self.runtime_service.upload_bytes(
                    thread_id=thread_id,
                    filename=target_filename,
                    data=markdown_bytes,
                    content_type="text/markdown; charset=utf-8",
                )
                uploaded_filename = str(uploaded.get("filename") or target_filename).strip()
                if not uploaded_filename or Path(uploaded_filename).name != uploaded_filename:
                    raise ValueError("Runtime returned an invalid materialized filename")
                desired_filenames.add(uploaded_filename)
                materialized.append(
                    self._build_kb_materialized_payload(
                        kb_id=kb_id,
                        doc_id=doc_id,
                        name=name,
                        target_filename=uploaded_filename,
                        uploaded_file=uploaded,
                        synced=True,
                        size_bytes=len(markdown_bytes),
                        content_sha256=content_sha256,
                        document_revision=document_revision,
                    )
                )

            if not defer_cleanup:
                for stale_filename in sorted(managed_filenames - desired_filenames):
                    if stale_filename not in existing_by_filename:
                        continue
                    await self.runtime_service.delete_thread_upload(
                        thread_id=thread_id,
                        filename=stale_filename,
                    )
            return materialized

    async def cleanup_stale_knowledge_uploads(
        self,
        *,
        thread_id: str,
        desired_filenames: Iterable[str],
        guard_acquired: bool = False,
    ) -> None:
        """Remove managed files only after the replacement manifest is durable."""
        guard = nullcontext() if guard_acquired else self.thread_guard(thread_id)
        async with guard:
            desired = {
                str(filename).strip()
                for filename in desired_filenames
                if str(filename).strip()
            }
            existing_uploads = await self.runtime_service.list_thread_uploads(thread_id)
            stale = sorted(
                str(item.get("filename", "")).strip()
                for item in existing_uploads
                if _MANAGED_KB_FILENAME_RE.fullmatch(
                    str(item.get("filename", "")).strip()
                )
                and str(item.get("filename", "")).strip() not in desired
            )
            for filename in stale:
                await self.runtime_service.delete_thread_upload(
                    thread_id=thread_id,
                    filename=filename,
                )

    @staticmethod
    async def _iterate_knowledge_documents(
        documents: AsyncIterable[dict[str, Any]] | Iterable[dict[str, Any]],
    ) -> AsyncIterator[dict[str, Any]]:
        if isinstance(documents, AsyncIterable):
            async for document in documents:
                yield document
            return
        for document in documents:
            yield document

    @staticmethod
    def _build_target_filename(asset: WorkspaceAttachmentRecord) -> str:
        normalized_workspace_path = asset.workspace_path.replace("/", "__").strip("_")
        suffix = Path(asset.name).suffix
        if suffix and not normalized_workspace_path.endswith(suffix):
            normalized_workspace_path = f"{normalized_workspace_path}{suffix}"
        if not normalized_workspace_path:
            normalized_workspace_path = asset.name
        return f"{asset.attachment_id}__{normalized_workspace_path}"

    @staticmethod
    def _build_kb_target_filename(
        *,
        kb_id: str,
        doc_id: str,
        name: str,
        content_sha256: str,
    ) -> str:
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(name).stem.strip() or doc_id).strip("._")
        if not safe_name:
            safe_name = doc_id
        prefix = f"kb__{kb_id}__{doc_id}__{content_sha256[:16]}__"
        safe_name = safe_name[: max(1, 240 - len(prefix) - len(".md"))]
        return f"{prefix}{safe_name}.md"

    @staticmethod
    def _build_materialized_payload(
        asset: WorkspaceAttachmentRecord,
        *,
        target_filename: str,
        uploaded_file: dict[str, Any],
        synced: bool,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "attachment_id": asset.attachment_id,
            "name": asset.name,
            "workspace_path": asset.workspace_path,
            "role": asset.role,
            "source_kind": asset.source_kind,
            "mime_type": asset.mime_type,
            "size_bytes": asset.size_bytes,
            "thread_filename": target_filename,
            "synced": synced,
        }
        for key in (
            "artifact_url",
            "virtual_path",
            "markdown_file",
            "markdown_path",
            "markdown_virtual_path",
            "markdown_artifact_url",
        ):
            value = uploaded_file.get(key)
            if isinstance(value, str) and value.strip():
                payload[key] = value.strip()
        size = uploaded_file.get("size")
        try:
            if size is not None:
                resolved_size = int(size)
                if resolved_size >= 0:
                    payload["thread_size_bytes"] = resolved_size
        except (TypeError, ValueError):
            pass
        return payload

    @staticmethod
    def _build_kb_materialized_payload(
        *,
        kb_id: str,
        doc_id: str,
        name: str,
        target_filename: str,
        uploaded_file: dict[str, Any],
        synced: bool,
        size_bytes: int,
        content_sha256: str,
        document_revision: str,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "kb_id": kb_id,
            "doc_id": doc_id,
            "name": name,
            "thread_filename": target_filename,
            "source_kind": "knowledge_base_markdown",
            "mime_type": "text/markdown",
            "size_bytes": size_bytes,
            "content_sha256": content_sha256,
            "document_revision": document_revision,
            "synced": synced,
        }
        for key in (
            "artifact_url",
            "virtual_path",
            "markdown_file",
            "markdown_path",
            "markdown_virtual_path",
            "markdown_artifact_url",
        ):
            value = uploaded_file.get(key)
            if isinstance(value, str) and value.strip():
                payload[key] = value.strip()
        size = uploaded_file.get("size")
        try:
            if size is not None:
                resolved_size = int(size)
                if resolved_size >= 0:
                    payload["thread_size_bytes"] = resolved_size
        except (TypeError, ValueError):
            pass
        return payload


thread_materialization_service = ThreadMaterializationService()
