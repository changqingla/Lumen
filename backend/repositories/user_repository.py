"""User repository for database operations."""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from typing import Optional, List
from datetime import datetime
from models.user import User
import uuid


class UserRepository:
    """Repository for User model with membership support."""
    
    def __init__(self, db: AsyncSession):
        self.db = db
    
    async def get_by_id(self, user_id: uuid.UUID | str) -> Optional[User]:
        """Get user by ID."""
        if isinstance(user_id, str):
            user_id = uuid.UUID(user_id)
        result = await self.db.execute(
            select(User).where(User.id == user_id)
        )
        return result.scalar_one_or_none()
    
    async def get_by_email(self, email: str) -> Optional[User]:
        """Get user by email."""
        result = await self.db.execute(
            select(User).where(User.email == email)
        )
        return result.scalar_one_or_none()
    
    async def get_by_name(self, name: str) -> Optional[User]:
        """Get user by name."""
        result = await self.db.execute(
            select(User).where(User.name == name)
        )
        return result.scalar_one_or_none()
    
    async def check_username_available(self, username: str, exclude_user_id: Optional[uuid.UUID] = None) -> bool:
        """
        Check if username is available.
        
        Args:
            username: Username to check
            exclude_user_id: User ID to exclude from check (for profile update)
            
        Returns:
            True if available, False otherwise
        """
        query = select(User).where(func.lower(User.name) == func.lower(username))
        if exclude_user_id:
            query = query.where(User.id != exclude_user_id)
        
        result = await self.db.execute(query)
        existing_user = result.scalar_one_or_none()
        return existing_user is None
    
    async def create(self, email: str, password_hash: str, name: str) -> User:
        """Create a new user."""
        user = User(
            email=email,
            password_hash=password_hash,
            name=name,
            user_level='basic',
            is_admin=False,
        )
        self.db.add(user)
        await self.db.commit()
        await self.db.refresh(user)
        return user
    
    async def update(self, user: User, **kwargs) -> User:
        """Update user fields."""
        for key, value in kwargs.items():
            if hasattr(user, key):
                setattr(user, key, value)
        await self.db.commit()
        await self.db.refresh(user)
        return user
    
    async def update_password(self, user_id: uuid.UUID, password_hash: str) -> User:
        """
        Update user password.
        
        Args:
            user_id: User ID
            password_hash: New password hash
            
        Returns:
            Updated user
        """
        user = await self.get_by_id(user_id)
        if not user:
            raise ValueError("User not found")
        
        user.password_hash = password_hash
        await self.db.commit()
        await self.db.refresh(user)
        return user
    
    async def update_profile(self, user_id: uuid.UUID, name: Optional[str] = None, avatar: Optional[str] = None) -> User:
        """
        Update user profile (name and/or avatar).
        
        Args:
            user_id: User ID
            name: New name (optional)
            avatar: New avatar URL (optional)
            
        Returns:
            Updated user
        """
        user = await self.get_by_id(user_id)
        if not user:
            raise ValueError("User not found")
        
        if name is not None:
            user.name = name
        if avatar is not None:
            user.avatar = avatar
        
        await self.db.commit()
        await self.db.refresh(user)
        return user
    
    async def update_user_level(
        self,
        user_id: uuid.UUID,
        level: str,
        expires_at: Optional[datetime] = None,
        activated_code: Optional[str] = None
    ) -> User:
        """
        Update user membership level.
        
        Args:
            user_id: User ID
            level: New level (basic/member/premium)
            expires_at: Membership expiry date (None for permanent)
            activated_code: Activation code used
            
        Returns:
            Updated user
        """
        user = await self.get_by_id(user_id)
        if not user:
            raise ValueError("User not found")
        
        user.user_level = level
        user.membership_expires_at = expires_at
        
        # 记录激活码
        if activated_code:
            if user.activated_codes is None:
                user.activated_codes = []
            if activated_code not in user.activated_codes:
                user.activated_codes = user.activated_codes + [activated_code]
        
        await self.db.commit()
        await self.db.refresh(user)
        return user
    
    async def set_admin(self, user_id: uuid.UUID, is_admin: bool) -> User:
        """
        Set user admin status.
        
        Args:
            user_id: User ID
            is_admin: Admin status
            
        Returns:
            Updated user
        """
        user = await self.get_by_id(user_id)
        if not user:
            raise ValueError("User not found")
        
        user.is_admin = is_admin
        await self.db.commit()
        await self.db.refresh(user)
        return user
    
    async def get_users_by_level(self, level: str, limit: int = 100) -> List[User]:
        """
        Get users by membership level.
        
        Args:
            level: User level (basic/member/premium)
            limit: Maximum number of users to return
            
        Returns:
            List of users
        """
        result = await self.db.execute(
            select(User)
            .where(User.user_level == level)
            .limit(limit)
        )
        return list(result.scalars().all())
    
    async def get_all_admins(self) -> List[User]:
        """Get all admin users."""
        result = await self.db.execute(
            select(User).where(User.is_admin == True)
        )
        return list(result.scalars().all())
