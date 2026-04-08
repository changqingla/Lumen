"""Organization API endpoints."""
from fastapi import APIRouter, Depends, Path
from sqlalchemy.ext.asyncio import AsyncSession
from config.database import get_db
from modules.organization.schemas import (
    CreateOrganizationRequest,
    UpdateOrganizationRequest,
    JoinOrganizationRequest,
    OrganizationResponse,
    OrganizationListResponse,
    OrganizationMemberResponse,
    OrganizationDetailResponse,
    RegenerateCodeResponse,
    SetCodeExpiryRequest,
)
from middlewares.auth import get_current_user
from models.user import User
from typing import List
import uuid

router = APIRouter(prefix="/organizations", tags=["Organizations"])


def _create_organization_service(db: AsyncSession):
    from modules.organization.services.organization_service import OrganizationService

    return OrganizationService(db)


@router.post("", response_model=OrganizationResponse)
async def create_organization(
    request: CreateOrganizationRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Create a new organization."""
    service = _create_organization_service(db)
    org_data = await service.create_organization(
        user_id=current_user.id,
        name=request.name,
        description=request.description,
        avatar=request.avatar
    )
    return org_data


@router.get("", response_model=OrganizationListResponse)
async def list_my_organizations(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """List all organizations the user is in (owned or joined)."""
    service = _create_organization_service(db)
    orgs = await service.list_my_organizations(current_user.id)
    return orgs


@router.get("/{org_id}", response_model=OrganizationDetailResponse)
async def get_organization(
    org_id: str = Path(..., description="Organization ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get organization details."""
    service = _create_organization_service(db)
    org_data = await service.get_organization(uuid.UUID(org_id), current_user.id)
    return org_data


@router.patch("/{org_id}", response_model=OrganizationResponse)
async def update_organization(
    request: UpdateOrganizationRequest,
    org_id: str = Path(..., description="Organization ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Update organization information."""
    service = _create_organization_service(db)
    org_data = await service.update_organization(
        org_id=uuid.UUID(org_id),
        user_id=current_user.id,
        name=request.name,
        description=request.description,
        avatar=request.avatar
    )
    return org_data


@router.delete("/{org_id}")
async def delete_organization(
    org_id: str = Path(..., description="Organization ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Delete (dissolve) an organization."""
    service = _create_organization_service(db)
    result = await service.delete_organization(uuid.UUID(org_id), current_user.id)
    return result


@router.post("/join", response_model=OrganizationResponse)
async def join_organization(
    request: JoinOrganizationRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Join an organization using organization code."""
    service = _create_organization_service(db)
    org_data = await service.join_organization(current_user.id, request.org_code)
    return org_data


@router.delete("/{org_id}/leave")
async def leave_organization(
    org_id: str = Path(..., description="Organization ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Leave an organization."""
    service = _create_organization_service(db)
    result = await service.leave_organization(uuid.UUID(org_id), current_user.id)
    return result


@router.get("/{org_id}/members", response_model=List[OrganizationMemberResponse])
async def get_organization_members(
    org_id: str = Path(..., description="Organization ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get organization members."""
    service = _create_organization_service(db)
    members = await service.get_members(uuid.UUID(org_id), current_user.id)
    return members


@router.delete("/{org_id}/members/{user_id}")
async def remove_organization_member(
    org_id: str = Path(..., description="Organization ID"),
    user_id: str = Path(..., description="User ID to remove"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Remove a member from organization."""
    service = _create_organization_service(db)
    result = await service.remove_member(
        org_id=uuid.UUID(org_id),
        user_id=current_user.id,
        target_user_id=uuid.UUID(user_id)
    )
    return result


@router.post("/{org_id}/regenerate-code", response_model=RegenerateCodeResponse)
async def regenerate_organization_code(
    org_id: str = Path(..., description="Organization ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Regenerate organization code."""
    service = _create_organization_service(db)
    result = await service.regenerate_code(uuid.UUID(org_id), current_user.id)
    return result


@router.patch("/{org_id}/code-expiry")
async def set_code_expiry(
    request: SetCodeExpiryRequest,
    org_id: str = Path(..., description="Organization ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Set organization code expiry date."""
    service = _create_organization_service(db)
    result = await service.set_code_expiry(
        org_id=uuid.UUID(org_id),
        user_id=current_user.id,
        expires_at=request.expires_at
    )
    return result
