"""Admin API endpoints."""
from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from config.database import get_db
from schemas.schemas import (
    GenerateActivationCodeRequest,
    ActivationCodeResponse,
    ValidateCodeResponse,
)
from middlewares.auth import get_current_user
from models.user import User
from typing import Optional
import uuid

router = APIRouter(prefix="/admin", tags=["Admin"])


def _create_activation_code_service(db: AsyncSession):
    from modules.admin.services.activation_code_service import ActivationCodeService

    return ActivationCodeService(db)


def _create_user_repository(db: AsyncSession):
    from repositories.user_repository import UserRepository

    return UserRepository(db)


def _ensure_admin(current_user: User, message: str) -> None:
    """Raise a consistent 403 when a non-admin accesses an admin endpoint."""
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": {"code": "FORBIDDEN", "message": message}},
        )


# === Activation Codes ===

@router.post("/codes", response_model=ActivationCodeResponse)
async def generate_activation_code(
    request: GenerateActivationCodeRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Generate a new activation code (Admin only)."""
    _ensure_admin(current_user, "仅管理员可以生成激活码")
    service = _create_activation_code_service(db)
    code_data = await service.generate_code(
        admin_id=current_user.id,
        type=request.type,
        duration_days=request.duration_days,
        max_usage=request.max_usage,
        code_expires_in_days=request.code_expires_in_days
    )
    return code_data


@router.get("/codes", response_model=dict)
async def list_activation_codes(
    type: Optional[str] = Query(None, description="Filter by type (member/premium)"),
    is_active: Optional[bool] = Query(None, description="Filter by active status"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Page size"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """List activation codes (Admin only)."""
    _ensure_admin(current_user, "仅管理员可以查看激活码列表")
    service = _create_activation_code_service(db)
    result = await service.list_codes(
        admin_id=current_user.id,
        type=type,
        is_active=is_active,
        page=page,
        page_size=page_size
    )
    return result


@router.delete("/codes/{code}")
async def deactivate_activation_code(
    code: str = Path(..., description="Activation code to deactivate"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Deactivate an activation code (Admin only)."""
    _ensure_admin(current_user, "仅管理员可以作废激活码")
    service = _create_activation_code_service(db)
    result = await service.deactivate_code(current_user.id, code)
    return result


@router.get("/codes/validate/{code}", response_model=ValidateCodeResponse)
async def validate_activation_code(
    code: str = Path(..., description="Activation code to validate"),
    db: AsyncSession = Depends(get_db)
):
    """Validate an activation code (Public endpoint)."""
    service = _create_activation_code_service(db)
    result = await service.validate_code(code)
    return result


@router.post("/codes/batch")
async def batch_generate_activation_codes(
    request: GenerateActivationCodeRequest,
    count: int = Query(..., ge=1, le=100, description="Number of codes to generate (1-100)"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Batch generate activation codes (Admin only)."""
    _ensure_admin(current_user, "仅管理员可以生成激活码")
    service = _create_activation_code_service(db)
    result = await service.batch_generate_codes(
        admin_id=current_user.id,
        count=count,
        type=request.type,
        duration_days=request.duration_days,
        max_usage=request.max_usage,
        code_expires_in_days=request.code_expires_in_days
    )
    return result


# === User Management ===

@router.post("/users/{user_id}/set-admin")
async def set_user_admin(
    user_id: str = Path(..., description="User ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Set user as admin (Admin only)."""
    _ensure_admin(current_user, "仅管理员可以设置管理员权限")
    
    user_repo = _create_user_repository(db)
    updated_user = await user_repo.set_admin(uuid.UUID(user_id), True)
    
    return {"message": "用户已设置为管理员", "user": updated_user.to_dict()}


@router.delete("/users/{user_id}/remove-admin")
async def remove_user_admin(
    user_id: str = Path(..., description="User ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Remove admin status from user (Admin only)."""
    _ensure_admin(current_user, "仅管理员可以取消管理员权限")
    
    # Prevent removing own admin status
    if str(current_user.id) == user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": "INVALID_OPERATION", "message": "不能取消自己的管理员权限"}}
        )
    
    user_repo = _create_user_repository(db)
    updated_user = await user_repo.set_admin(uuid.UUID(user_id), False)
    
    return {"message": "已取消管理员权限", "user": updated_user.to_dict()}


@router.get("/statistics")
async def get_statistics(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get system statistics (Admin only)."""
    _ensure_admin(current_user, "仅管理员可以查看统计数据")
    
    from modules.organization.repositories.organization_repository import OrganizationRepository
    from sqlalchemy import select, func
    from modules.knowledge.entities.knowledge_base import KnowledgeBase
    from modules.organization.entities.organization import Organization
    from modules.organization.entities.organization_member import OrganizationMember
    
    org_repo = OrganizationRepository(db)
    
    # User statistics - 使用SQL直接查询，避免重复计数
    from models.user import User as UserModel
    
    # 总用户数
    result = await db.execute(select(func.count(UserModel.id)))
    total_users = result.scalar() or 0
    
    # 按等级统计（不包括管理员）
    result = await db.execute(
        select(func.count(UserModel.id))
        .where(UserModel.user_level == 'basic')
        .where(UserModel.is_admin.is_(False))
    )
    explorers = result.scalar() or 0
    
    result = await db.execute(
        select(func.count(UserModel.id))
        .where(UserModel.user_level == 'member')
        .where(UserModel.is_admin.is_(False))
    )
    members = result.scalar() or 0
    
    result = await db.execute(
        select(func.count(UserModel.id))
        .where(UserModel.user_level == 'premium')
        .where(UserModel.is_admin.is_(False))
    )
    advanced_members = result.scalar() or 0
    
    # 管理员数量
    result = await db.execute(
        select(func.count(UserModel.id))
        .where(UserModel.is_admin.is_(True))
    )
    admins = result.scalar() or 0
    
    # Organization statistics
    total_orgs = await org_repo.count_all()
    result = await db.execute(
        select(func.count(OrganizationMember.id))
        .select_from(Organization)
        .outerjoin(OrganizationMember, OrganizationMember.org_id == Organization.id)
        .where(Organization.is_deleted.is_(False))
    )
    total_org_members = result.scalar() or 0
    average_members = round(total_org_members / total_orgs, 1) if total_orgs else 0
    
    # Knowledge base statistics
    result = await db.execute(select(func.count(KnowledgeBase.id)))
    total_kbs = result.scalar() or 0
    
    result = await db.execute(
        select(func.count(KnowledgeBase.id))
        .where(KnowledgeBase.visibility == 'public')
    )
    public_kbs = result.scalar() or 0
    
    result = await db.execute(
        select(func.count(KnowledgeBase.id))
        .where(KnowledgeBase.visibility == 'organization')
    )
    org_shared_kbs = result.scalar() or 0
    
    return {
        "users": {
            "total": total_users,
            "explorers": explorers,
            "members": members,
            "advanced_members": advanced_members,
            "admins": admins,
        },
        "organizations": {
            "total": total_orgs,
            "average_members": average_members,
        },
        "knowledge_bases": {
            "total": total_kbs,
            "public": public_kbs,
            "shared": org_shared_kbs,
        }
    }


@router.get("/users")
async def list_users(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Page size"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """List all users (Admin only)."""
    _ensure_admin(current_user, "仅管理员可以查看用户列表")
    
    from sqlalchemy import select, func
    from config.quota_config import get_billing_window, get_user_model_quota_limit
    from models.user import User as UserModel
    from modules.chat.entities.chat_session import ChatSession, ChatMessage
    from models.token_usage import TokenUsageRecord
    
    # Count total users
    result = await db.execute(select(func.count(UserModel.id)))
    total = result.scalar() or 0
    
    last_active_subquery = (
        select(
            ChatSession.user_id.label("user_id"),
            func.max(ChatMessage.created_at).label("last_active_at"),
        )
        .join(ChatMessage, ChatMessage.session_id == ChatSession.id)
        .where(ChatMessage.role == "user")
        .group_by(ChatSession.user_id)
        .subquery()
    )

    billing_window = get_billing_window()
    billing_token_subquery = (
        select(
            TokenUsageRecord.user_id.label("user_id"),
            func.sum(TokenUsageRecord.total_tokens).label("billing_cycle_token_total"),
        )
        .where(TokenUsageRecord.billing_window_start == billing_window.start)
        .group_by(TokenUsageRecord.user_id)
        .subquery()
    )

    # Get paginated users
    offset = (page - 1) * page_size
    result = await db.execute(
        select(
            UserModel,
            last_active_subquery.c.last_active_at,
            func.coalesce(billing_token_subquery.c.billing_cycle_token_total, 0).label("billing_cycle_token_total"),
        )
        .outerjoin(last_active_subquery, last_active_subquery.c.user_id == UserModel.id)
        .outerjoin(billing_token_subquery, billing_token_subquery.c.user_id == UserModel.id)
        .order_by(UserModel.created_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    user_rows = result.all()
    
    # Format user data
    items = []
    for user, last_active_at, billing_cycle_token_total in user_rows:
        token_total = int(billing_cycle_token_total or 0)
        items.append({
            "id": str(user.id),
            "name": user.name,
            "email": user.email,
            "avatar": user.avatar,
            "user_level": user.user_level,
            "is_admin": user.is_admin,
            "created_at": user.created_at.isoformat() if user.created_at else None,
            "last_active_at": last_active_at.isoformat() if last_active_at else None,
            "billing_cycle_token_total": token_total,
            "model_quota_limit": get_user_model_quota_limit(user),
            "quota_reset_date": billing_window.end.isoformat(),
        })
    
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }
