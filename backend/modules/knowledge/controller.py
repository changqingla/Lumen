"""Knowledge Base API endpoints."""
from fastapi import APIRouter, Depends, Query, UploadFile, File, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, List
from config.database import get_db
from config.settings import settings
from middlewares.auth import get_current_user
from models.user import User
from schemas.schemas import (
    BatchDocumentMarkdownRequest,
    CompleteDirectUploadRequest,
    CreateKnowledgeBaseRequest,
    InitDirectUploadRequest,
    KnowledgeChatSearchRequest,
    MoveDocumentRequest,
    ShareToOrgsRequest,
    UpdateKBVisibilityRequest,
    UpdateKnowledgeBaseRequest,
)
from utils.audit_logger import record_user_prompt_event
from utils.avatar_security import read_avatar_upload_file

router = APIRouter(prefix="/kb", tags=["Knowledge Base"])


def _create_kb_service(db: AsyncSession):
    from modules.knowledge.services.kb_service import KnowledgeBaseService

    return KnowledgeBaseService(db)


def _create_document_service(db: AsyncSession):
    from modules.knowledge.services.document_service import DocumentService

    return DocumentService(db)


def _create_search_service(db: AsyncSession):
    from modules.knowledge.services.search_service import SearchService

    return SearchService(db)


@router.get("")
async def list_knowledge_bases(
    q: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """List user's knowledge bases."""
    service = _create_kb_service(db)
    items, total = await service.list_kbs(str(current_user.id), q, page, pageSize)
    return {
        "total": total,
        "page": page,
        "pageSize": pageSize,
        "items": items
    }


@router.post("")
async def create_knowledge_base(
    request: CreateKnowledgeBaseRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Create a new knowledge base."""
    service = _create_kb_service(db)
    return await service.create_kb(
        str(current_user.id),
        request.name,
        request.description,
        request.category
    )


@router.patch("/{kbId}")
async def update_knowledge_base(
    kbId: str,
    request: UpdateKnowledgeBaseRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Update knowledge base."""
    service = _create_kb_service(db)
    update_data = request.model_dump(exclude_unset=True)
    return await service.update_kb(kbId, str(current_user.id), **update_data)


@router.delete("/{kbId}")
async def delete_knowledge_base(
    kbId: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Delete knowledge base."""
    service = _create_kb_service(db)
    await service.delete_kb(kbId, str(current_user.id))
    return {"success": True}


@router.get("/{kbId}/info")
async def get_knowledge_base_info(
    kbId: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get knowledge base info (supports both owned and public KBs)."""
    service = _create_kb_service(db)
    return await service.get_kb_info(kbId, str(current_user.id))


@router.get("/quota")
async def get_quota(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get storage quota."""
    service = _create_kb_service(db)
    return await service.get_quota(str(current_user.id))


@router.post("/{kbId}/avatar")
async def upload_kb_avatar(
    kbId: str,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Upload knowledge base avatar image."""
    file_data = await read_avatar_upload_file(file, settings.MAX_AVATAR_SIZE)
    
    # Upload avatar
    service = _create_kb_service(db)
    return await service.upload_avatar(
        kbId,
        str(current_user.id),
        file_data,
        file.filename,
        file.content_type
    )


@router.post("/{kbId}/documents/direct-upload/init")
async def init_direct_upload(
    kbId: str,
    request: InitDirectUploadRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Initialize direct browser upload to MinIO."""
    filename = request.filename
    file_size = int(request.size or 0)
    content_type = request.contentType

    service = _create_document_service(db)
    return await service.init_direct_upload(
        kbId,
        str(current_user.id),
        filename,
        file_size,
        content_type
    )


@router.post("/{kbId}/documents/direct-upload/complete")
async def complete_direct_upload(
    kbId: str,
    request: CompleteDirectUploadRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Complete direct upload and start background document processing."""
    service = _create_document_service(db)
    return await service.complete_direct_upload(kbId, str(current_user.id), request.docId, background_tasks)


@router.get("/{kbId}/documents")
async def list_documents(
    kbId: str,
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """List documents in knowledge base."""
    service = _create_document_service(db)
    items, total = await service.list_documents(kbId, str(current_user.id), page, pageSize)
    return {"total": total, "page": page, "pageSize": pageSize, "items": items}


@router.get("/{kbId}/documents/{docId}/status")
async def get_document_status(
    kbId: str,
    docId: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get document processing status."""
    service = _create_document_service(db)
    return await service.get_document_status(docId, kbId, str(current_user.id))


@router.get("/{kbId}/documents/{docId}/url")
async def get_document_url(
    kbId: str,
    docId: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get presigned URL for document file."""
    service = _create_document_service(db)
    return await service.get_document_url(docId, kbId, str(current_user.id))


@router.get("/{kbId}/documents/{docId}/markdown")
async def get_document_markdown(
    kbId: str,
    docId: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get markdown content of document (for agent use)."""
    service = _create_document_service(db)
    content = await service.get_document_markdown(docId, kbId, str(current_user.id))
    return {
        "content": content,
        "docId": docId
    }


@router.post("/{kbId}/documents/batch-markdown")
async def get_documents_markdown_batch(
    kbId: str,
    request: BatchDocumentMarkdownRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Batch get markdown content of multiple documents (for agent use).
    
    Request body:
        {
            "docIds": ["doc-id-1", "doc-id-2", ...]
        }
    
    Returns:
        {
            "documents": {
                "doc-id-1": "markdown content 1",
                "doc-id-2": "markdown content 2",
                ...
            },
            "failed": ["doc-id-3"]  # IDs of documents that failed to load
        }
    """
    service = _create_document_service(db)
    result = await service.get_documents_markdown_batch(request.docIds, kbId, str(current_user.id))
    return result


@router.post("/{kbId}/documents/{docId}/retry")
async def retry_document(
    kbId: str,
    docId: str,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Retry processing a failed document."""
    service = _create_document_service(db)
    return await service.retry_document(docId, kbId, str(current_user.id), background_tasks)


@router.delete("/{kbId}/documents/{docId}")
async def delete_document(
    kbId: str,
    docId: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Delete a document."""
    service = _create_document_service(db)
    await service.delete_document(docId, kbId, str(current_user.id))
    return {"success": True}


@router.post("/{kbId}/documents/{docId}/move")
async def move_document(
    kbId: str,
    docId: str,
    request: MoveDocumentRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Move document to another knowledge base.
    User must be owner of both source and target knowledge bases.
    
    Request body:
        {
            "targetKbId": "target-kb-uuid"
        }
    """
    service = _create_document_service(db)
    return await service.move_document(docId, kbId, request.targetKbId, str(current_user.id))


@router.post("/{kbId}/chat/messages")
async def chat_with_kb(
    kbId: str,
    request: KnowledgeChatSearchRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Search in knowledge base (retrieve relevant chunks).
    Note: LLM answer generation is not implemented yet.
    """
    service = _create_search_service(db)
    question = request.question
    top_n = request.top_n

    await record_user_prompt_event(
        event_type="knowledge_chat_question",
        user=current_user,
        prompt=question,
        metadata={
            "kb_id": kbId,
            "top_n": top_n,
        },
    )
    
    search_results = await service.search_in_kb(
        kbId,
        str(current_user.id),
        question,
        top_n=top_n
    )
    
    # Return search results (without LLM-generated answer)
    return {
        "messageId": "search_" + str(hash(question)),
        "references": search_results["references"],
        "answer": "检索完成，找到相关内容（LLM问答功能暂未实现）"
    }


# ============ Public Sharing & Subscription Features ============

@router.post("/{kbId}/toggle-public")
async def toggle_kb_public(
    kbId: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Toggle public status of a knowledge base."""
    service = _create_kb_service(db)
    return await service.toggle_public(kbId, str(current_user.id))


@router.post("/{kbId}/subscribe")
async def subscribe_to_kb(
    kbId: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Subscribe to a public knowledge base."""
    service = _create_kb_service(db)
    return await service.subscribe_kb(kbId, str(current_user.id))


@router.delete("/{kbId}/subscribe")
async def unsubscribe_from_kb(
    kbId: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Unsubscribe from a knowledge base."""
    service = _create_kb_service(db)
    return await service.unsubscribe_kb(kbId, str(current_user.id))


@router.get("/{kbId}/subscription-status")
async def check_kb_subscription(
    kbId: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Check if user is subscribed to a knowledge base."""
    service = _create_kb_service(db)
    return await service.check_subscription(kbId, str(current_user.id))


@router.get("/subscriptions/list")
async def list_my_subscriptions(
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """List all knowledge bases subscribed by current user."""
    service = _create_kb_service(db)
    items, total = await service.list_user_subscriptions(str(current_user.id), page, pageSize)
    return {
        "total": total,
        "page": page,
        "pageSize": pageSize,
        "items": items
    }


@router.get("/public/list")
async def list_public_knowledge_bases(
    category: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """List public knowledge bases (knowledge square)."""
    service = _create_kb_service(db)
    items, total = await service.list_public_kbs(category, q, page, pageSize)
    return {
        "total": total,
        "page": page,
        "pageSize": pageSize,
        "items": items
    }


@router.get("/featured/list")
async def list_featured_knowledge_bases(
    page: int = Query(1, ge=1),
    pageSize: int = Query(30, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    List featured knowledge bases (2025年度精选).
    Shows all visible KBs (public + organization-shared) for the current user.
    Sorted by subscribers_count DESC, then created_at DESC.
    """
    service = _create_kb_service(db)
    items, total = await service.list_featured_kbs(str(current_user.id), page, pageSize)
    return {
        "total": total,
        "page": page,
        "pageSize": pageSize,
        "items": items
    }


@router.get("/categories/stats")
async def get_kb_categories_stats(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get statistics for each knowledge base category."""
    service = _create_kb_service(db)
    return {"categories": await service.get_categories_stats()}


# ============ Organization & Visibility Features ============

@router.get("/plaza")
async def list_plaza_knowledge_bases(
    category: Optional[str] = Query(None, description="Filter by category"),
    q: Optional[str] = Query(None, description="Search query"),
    page: int = Query(1, ge=1, description="Page number"),
    pageSize: int = Query(20, ge=1, le=100, description="Page size"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    List knowledge bases in plaza based on user permissions.
    - Admin users see all public KBs
    - Regular users see: admin-shared public KBs + org-shared KBs from their organizations
    """
    service = _create_kb_service(db)
    items, total = await service.list_plaza_kbs(
        str(current_user.id),
        category,
        q,
        page,
        pageSize
    )
    return {
        "total": total,
        "page": page,
        "pageSize": pageSize,
        "items": items
    }


@router.patch("/{kbId}/visibility")
async def update_kb_visibility(
    kbId: str,
    request: UpdateKBVisibilityRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Update knowledge base visibility.
    - private: Only owner can access
    - organization: Shared to specified organizations (requires org_ids)
    - public: Globally visible (admin only)
    """
    service = _create_kb_service(db)
    return await service.update_visibility(
        kbId,
        str(current_user.id),
        request.visibility,
        request.shared_to_orgs
    )


@router.post("/{kbId}/share-to-orgs")
async def share_kb_to_organizations(
    kbId: str,
    request: ShareToOrgsRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Share knowledge base to specific organizations.
    Automatically sets visibility to 'organization'.
    User must be a member of all specified organizations.
    """
    service = _create_kb_service(db)
    return await service.share_to_organizations(
        kbId,
        str(current_user.id),
        request.org_ids
    )


@router.get("/{kbId}/shared-status")
async def get_kb_shared_status(
    kbId: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Get knowledge base visibility and sharing status.
    Includes visibility setting, shared organizations, and user permissions.
    """
    service = _create_kb_service(db)
    return await service.get_shared_status(kbId, str(current_user.id))
