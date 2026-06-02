"""Document repository for database operations."""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, delete as sql_delete
from typing import Optional, List, Tuple
from modules.knowledge.entities.document import Document


class DocumentRepository:
    """Repository for Document model."""
    
    def __init__(self, db: AsyncSession):
        self.db = db
    
    async def get_by_id(self, doc_id: str, kb_id: str) -> Optional[Document]:
        """Get document by ID within specific KB."""
        result = await self.db.execute(
            select(Document).where(
                Document.id == doc_id,
                Document.kb_id == kb_id
            )
        )
        return result.scalar_one_or_none()
    
    async def list_documents(
        self,
        kb_id: str,
        page: int = 1,
        page_size: int = 20
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

    async def list_ready_docs_with_markdown(
        self,
        kb_id: str,
        doc_ids: Optional[List[str]] = None,
    ) -> List[Document]:
        """列出可用于 Agent 直读的文档（ready + markdown_path 非空）。"""
        stmt = (
            select(Document)
            .where(
                Document.kb_id == kb_id,
                Document.status == Document.STATUS_READY,
                Document.markdown_path.is_not(None),
            )
        )

        if doc_ids:
            stmt = stmt.where(Document.id.in_(doc_ids))

        result = await self.db.execute(stmt.order_by(Document.created_at.asc()))
        return list(result.scalars().all())
    
    async def create(
        self,
        kb_id: str,
        name: str,
        size: int,
        source: str,
        file_path: Optional[str] = None
    ) -> Document:
        """Create a new document record."""
        document = Document(
            kb_id=kb_id,
            name=name,
            size=size,
            source=source,
            file_path=file_path,
            status=Document.STATUS_UPLOADING
        )
        self.db.add(document)
        await self.db.commit()
        await self.db.refresh(document)
        return document
    
    async def update_status(
        self,
        doc: Document,
        status: str,
        **kwargs
    ) -> Document:
        """Update document status and related fields."""
        doc.status = status
        for key, value in kwargs.items():
            if hasattr(doc, key):
                setattr(doc, key, value)
        await self.db.commit()
        await self.db.refresh(doc)
        return doc
    
    async def update_markdown_path(self, doc: Document, markdown_path: str) -> Document:
        """Update document markdown path."""
        doc.markdown_path = markdown_path
        await self.db.commit()
        await self.db.refresh(doc)
        return doc
    
    async def delete(self, doc: Document):
        """Delete a document."""
        await self.db.delete(doc)
        await self.db.commit()
    
    async def batch_delete(self, kb_id: str, doc_ids: List[str]):
        """Batch delete documents."""
        await self.db.execute(
            sql_delete(Document).where(
                Document.kb_id == kb_id,
                Document.id.in_(doc_ids)
            )
        )
        await self.db.commit()
    
    async def update_kb_id(self, doc: Document, new_kb_id: str) -> Document:
        """Move document to another knowledge base."""
        doc.kb_id = new_kb_id
        await self.db.commit()
        await self.db.refresh(doc)
        return doc
