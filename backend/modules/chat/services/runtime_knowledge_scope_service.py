"""Resolve and validate the server-owned knowledge scope for Runtime runs."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.user import User
from modules.knowledge.entities.document import Document
from modules.knowledge.entities.knowledge_base import KnowledgeBase
from modules.knowledge.repositories.kb_repository import knowledge_base_access_condition
from modules.organization.repositories.organization_member_repository import (
    OrganizationMemberRepository,
)


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MANAGED_KB_FILENAME_RE = re.compile(
    r"^kb__[0-9a-fA-F-]{36}__[0-9a-fA-F-]{36}__"
    r"[0-9a-f]{16}__[A-Za-z0-9._-]+\.md$"
)


def _scope_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"error": {"code": code, "message": message}},
    )


def _normalize_uuid_list(value: Any, *, field_name: str) -> list[str]:
    if not isinstance(value, list):
        raise _scope_error(
            status.HTTP_409_CONFLICT,
            "RUNTIME_PREPARATION_REQUIRED",
            f"Session {field_name} is invalid; prepare the Runtime again",
        )

    normalized: list[str] = []
    seen: set[str] = set()
    for raw_value in value:
        try:
            item = str(UUID(str(raw_value).strip()))
        except (ValueError, AttributeError, TypeError) as exc:
            raise _scope_error(
                status.HTTP_409_CONFLICT,
                "RUNTIME_PREPARATION_REQUIRED",
                f"Session {field_name} contains an invalid identifier",
            ) from exc
        if item in seen:
            raise _scope_error(
                status.HTTP_409_CONFLICT,
                "RUNTIME_PREPARATION_REQUIRED",
                f"Session {field_name} contains duplicate identifiers",
            )
        seen.add(item)
        normalized.append(item)
    return normalized


def _format_revision(value: Any) -> str:
    if isinstance(value, bool):
        revision = 0
    else:
        try:
            revision = int(value)
        except (TypeError, ValueError):
            revision = 0
    if revision < 1:
        raise _scope_error(
            status.HTTP_409_CONFLICT,
            "RUNTIME_KNOWLEDGE_STALE",
            "A selected knowledge document has no stable revision",
        )
    return str(revision)


def _format_content_sha256(value: Any) -> str | None:
    digest = str(value or "").strip().lower()
    if not digest:
        return None
    if _SHA256_RE.fullmatch(digest) is None:
        raise _scope_error(
            status.HTTP_409_CONFLICT,
            "RUNTIME_KNOWLEDGE_STALE",
            "A selected knowledge document has an invalid content digest",
        )
    return digest


@dataclass(frozen=True)
class KnowledgeDocumentRevision:
    kb_id: str
    doc_id: str
    document_revision: str
    content_sha256: str | None = None


@dataclass(frozen=True)
class KnowledgeScopeSnapshot:
    kb_ids: tuple[str, ...]
    requested_doc_ids: tuple[str, ...]
    documents: tuple[KnowledgeDocumentRevision, ...]

    @property
    def scope_mode(self) -> str:
        return "explicit" if self.requested_doc_ids else "all_materialized"


@dataclass(frozen=True)
class RuntimeKnowledgeFile:
    kb_id: str
    doc_id: str
    document_revision: str
    content_sha256: str
    thread_filename: str
    size_bytes: int


class RuntimeKnowledgeScopeService:
    """Validate KB access, document revisions, and the persisted Runtime manifest."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.org_member_repo = OrganizationMemberRepository(db)

    async def resolve_current_scope(
        self,
        *,
        session_config: dict[str, Any],
        current_user: User,
    ) -> KnowledgeScopeSnapshot:
        kb_ids = _normalize_uuid_list(
            session_config.get("kbIds", []),
            field_name="kbIds",
        )
        requested_doc_ids = _normalize_uuid_list(
            session_config.get("docIds", []),
            field_name="docIds",
        )
        if requested_doc_ids and not kb_ids:
            raise _scope_error(
                status.HTTP_409_CONFLICT,
                "RUNTIME_PREPARATION_REQUIRED",
                "Selected documents do not belong to a selected knowledge base",
            )

        if not kb_ids:
            return KnowledgeScopeSnapshot((), (), ())

        user_id = UUID(str(current_user.id))
        user_org_ids = await self.org_member_repo.get_user_org_ids(user_id)
        kb_result = await self.db.execute(
            select(KnowledgeBase.id).where(
                KnowledgeBase.id.in_([UUID(item) for item in kb_ids]),
                knowledge_base_access_condition(
                    user_id,
                    user_org_ids,
                    is_admin=bool(getattr(current_user, "is_admin", False)),
                ),
            )
        )
        accessible_kb_ids = {str(row[0]) for row in kb_result.all()}
        if accessible_kb_ids != set(kb_ids):
            raise _scope_error(
                status.HTTP_403_FORBIDDEN,
                "KNOWLEDGE_ACCESS_REVOKED",
                "Knowledge base access has changed; update the session selection",
            )

        statement = select(
            Document.id,
            Document.kb_id,
            Document.markdown_path,
            Document.materialization_revision,
            Document.markdown_sha256,
        ).where(Document.kb_id.in_([UUID(item) for item in kb_ids]))
        if requested_doc_ids:
            statement = statement.where(
                Document.id.in_([UUID(item) for item in requested_doc_ids])
            )
        else:
            statement = statement.where(
                Document.markdown_path.is_not(None),
                func.btrim(Document.markdown_path) != "",
                Document.materialization_revision >= 1,
            )

        document_result = await self.db.execute(statement)
        rows = list(document_result.all())
        if requested_doc_ids:
            rows_by_id = {str(row.id): row for row in rows}
            if set(rows_by_id) != set(requested_doc_ids):
                raise _scope_error(
                    status.HTTP_409_CONFLICT,
                    "RUNTIME_KNOWLEDGE_STALE",
                    "A selected knowledge document no longer exists in the selected scope",
                )
            if any(
                not str(row.markdown_path or "").strip()
                for row in rows_by_id.values()
            ):
                raise _scope_error(
                    status.HTTP_409_CONFLICT,
                    "RUNTIME_KNOWLEDGE_STALE",
                    "A selected knowledge document has no materialized Markdown",
                )

        documents = tuple(
            sorted(
                (
                    KnowledgeDocumentRevision(
                        kb_id=str(row.kb_id),
                        doc_id=str(row.id),
                        document_revision=_format_revision(
                            row.materialization_revision
                        ),
                        content_sha256=_format_content_sha256(row.markdown_sha256),
                    )
                    for row in rows
                ),
                key=lambda item: (item.kb_id, item.doc_id),
            )
        )
        return KnowledgeScopeSnapshot(
            tuple(kb_ids),
            tuple(requested_doc_ids),
            documents,
        )

    @staticmethod
    def validate_manifest(
        *,
        scope: KnowledgeScopeSnapshot,
        raw_manifest: Any,
    ) -> tuple[RuntimeKnowledgeFile, ...]:
        if not isinstance(raw_manifest, list):
            raise _scope_error(
                status.HTTP_409_CONFLICT,
                "RUNTIME_PREPARATION_REQUIRED",
                "Runtime knowledge preparation is missing",
            )

        expected_by_doc = {item.doc_id: item for item in scope.documents}
        parsed_by_doc: dict[str, RuntimeKnowledgeFile] = {}
        for raw_record in raw_manifest:
            if not isinstance(raw_record, dict):
                raise _scope_error(
                    status.HTTP_409_CONFLICT,
                    "RUNTIME_PREPARATION_REQUIRED",
                    "Runtime knowledge preparation is malformed",
                )
            try:
                kb_id = str(UUID(str(raw_record.get("kb_id", "")).strip()))
                doc_id = str(UUID(str(raw_record.get("doc_id", "")).strip()))
            except (ValueError, AttributeError, TypeError) as exc:
                raise _scope_error(
                    status.HTTP_409_CONFLICT,
                    "RUNTIME_PREPARATION_REQUIRED",
                    "Runtime knowledge preparation contains invalid identifiers",
                ) from exc

            document_revision = str(raw_record.get("document_revision", "")).strip()
            content_sha256 = str(raw_record.get("content_sha256", "")).strip().lower()
            thread_filename = str(raw_record.get("thread_filename", "")).strip()
            size_bytes = raw_record.get("size_bytes")
            if (
                not document_revision
                or not _SHA256_RE.fullmatch(content_sha256)
                or not isinstance(size_bytes, int)
                or isinstance(size_bytes, bool)
                or size_bytes < 0
                or not _MANAGED_KB_FILENAME_RE.fullmatch(thread_filename)
                or not thread_filename.startswith(
                    f"kb__{kb_id}__{doc_id}__{content_sha256[:16]}__"
                )
            ):
                raise _scope_error(
                    status.HTTP_409_CONFLICT,
                    "RUNTIME_PREPARATION_REQUIRED",
                    "Runtime knowledge preparation is malformed",
                )
            if doc_id in parsed_by_doc:
                raise _scope_error(
                    status.HTTP_409_CONFLICT,
                    "RUNTIME_PREPARATION_REQUIRED",
                    "Runtime knowledge preparation contains duplicate documents",
                )
            parsed_by_doc[doc_id] = RuntimeKnowledgeFile(
                kb_id=kb_id,
                doc_id=doc_id,
                document_revision=document_revision,
                content_sha256=content_sha256,
                thread_filename=thread_filename,
                size_bytes=size_bytes,
            )

        if set(parsed_by_doc) != set(expected_by_doc):
            raise _scope_error(
                status.HTTP_409_CONFLICT,
                "RUNTIME_KNOWLEDGE_STALE",
                "The prepared document set no longer matches the session scope",
            )
        for doc_id, expected in expected_by_doc.items():
            prepared = parsed_by_doc[doc_id]
            if (
                prepared.kb_id != expected.kb_id
                or prepared.document_revision != expected.document_revision
                or (
                    expected.content_sha256 is not None
                    and prepared.content_sha256 != expected.content_sha256
                )
            ):
                raise _scope_error(
                    status.HTTP_409_CONFLICT,
                    "RUNTIME_KNOWLEDGE_STALE",
                    "A prepared knowledge document has changed",
                )

        return tuple(parsed_by_doc[item.doc_id] for item in scope.documents)


__all__ = [
    "KnowledgeDocumentRevision",
    "KnowledgeScopeSnapshot",
    "RuntimeKnowledgeFile",
    "RuntimeKnowledgeScopeService",
]
