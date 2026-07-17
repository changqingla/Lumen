"""Document repository for database operations."""

import re
from typing import List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from modules.knowledge.entities.document import Document

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class DocumentRepository:
    """Repository for Document model."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_id(self, doc_id: str, kb_id: str) -> Optional[Document]:
        """Get document by ID within specific KB."""
        result = await self.db.execute(
            select(Document).where(Document.id == doc_id, Document.kb_id == kb_id)
        )
        return result.scalar_one_or_none()

    async def list_documents(
        self, kb_id: str, page: int = 1, page_size: int = 20
    ) -> Tuple[List[Document], int]:
        """List documents in knowledge base."""
        stmt = select(Document).where(Document.kb_id == kb_id)

        # Count total
        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await self.db.execute(count_stmt)).scalar()

        # Paginate
        stmt = stmt.order_by(Document.created_at.desc())
        stmt = stmt.limit(page_size).offset((page - 1) * page_size)

        result = await self.db.execute(stmt)
        documents = result.scalars().all()

        return list(documents), total or 0

    async def list_materialized_document_ids(
        self,
        kb_id: str,
        page: int = 1,
        page_size: int = 100,
    ) -> Tuple[List[str], int]:
        """List documents whose committed Markdown can be exposed to Runtime."""
        materialized = (
            Document.kb_id == kb_id,
            Document.markdown_path.is_not(None),
            func.btrim(Document.markdown_path) != "",
            Document.materialization_revision >= 1,
        )
        total_result = await self.db.execute(
            select(func.count(Document.id)).where(*materialized)
        )
        total = total_result.scalar() or 0

        result = await self.db.execute(
            select(Document.id)
            .where(*materialized)
            .order_by(Document.created_at.desc(), Document.id.desc())
            .limit(page_size)
            .offset((page - 1) * page_size)
        )
        return [str(doc_id) for doc_id in result.scalars().all()], total

    async def get_all_doc_ids(self, kb_id: str) -> List[str]:
        """Get all document IDs in a knowledge base."""
        result = await self.db.execute(
            select(Document.id).where(Document.kb_id == kb_id)
        )
        return [str(doc_id) for doc_id in result.scalars().all()]

    async def get_by_kb_and_source(self, kb_id: str, source: str) -> Optional[Document]:
        """Get a document in a knowledge base by its source marker."""
        result = await self.db.execute(
            select(Document).where(
                Document.kb_id == kb_id,
                Document.source == source,
            )
        )
        return result.scalar_one_or_none()

    async def create(
        self,
        kb_id: str,
        name: str,
        size: int,
        source: str,
        file_path: Optional[str] = None,
    ) -> Document:
        """Create a new document record."""
        document = Document(
            kb_id=kb_id,
            name=name,
            size=size,
            source=source,
            file_path=file_path,
            status=Document.STATUS_UPLOADING,
        )
        self.db.add(document)
        await self.db.commit()
        await self.db.refresh(document)
        return document

    async def update_status(self, doc: Document, status: str, **kwargs) -> Document:
        """Update document status and related fields."""
        doc.status = status
        for key, value in kwargs.items():
            if hasattr(doc, key):
                setattr(doc, key, value)
        await self.db.commit()
        await self.db.refresh(doc)
        return doc

    async def update_markdown_path(
        self,
        doc: Document,
        markdown_path: str,
        markdown_sha256: str,
    ) -> Document:
        """Atomically persist Markdown identity and advance its content revision."""
        normalized_path = str(markdown_path or "").strip()
        normalized_sha256 = str(markdown_sha256 or "").strip().lower()
        if not normalized_path:
            raise ValueError("markdown_path is required")
        if _SHA256_RE.fullmatch(normalized_sha256) is None:
            raise ValueError("markdown_sha256 must be a lowercase SHA-256 digest")

        result = await self.db.execute(
            select(Document).where(Document.id == doc.id).with_for_update()
        )
        current = result.scalar_one()
        current_revision = max(int(current.materialization_revision or 0), 0)
        if current.markdown_sha256 != normalized_sha256 or current_revision == 0:
            current.materialization_revision = current_revision + 1
        current.markdown_path = normalized_path
        current.markdown_sha256 = normalized_sha256
        await self.db.commit()
        await self.db.refresh(current)
        return current

    async def delete(self, doc: Document):
        """Delete a document."""
        await self.db.delete(doc)
        await self.db.commit()

    async def update_kb_id(self, doc: Document, new_kb_id: str) -> Document:
        """Move document to another knowledge base."""
        doc.kb_id = new_kb_id
        await self.db.commit()
        await self.db.refresh(doc)
        return doc
