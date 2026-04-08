"""Database models.

SQLAlchemy relationships in this project use string targets such as
``relationship("ChatSession")``. That requires all ORM model classes to be
imported into the registry before mapper configuration runs, so this package
must keep eager model imports instead of lazy-loading them.
"""

from .user import User
from modules.notes.entities.note import Note, NoteFolder
from modules.favorites.entities.favorite import Favorite
from modules.knowledge.entities.knowledge_base import KnowledgeBase, KnowledgeBaseSubscription
from modules.knowledge.entities.document import Document
from modules.chat.entities.chat_session import ChatSession, ChatMessage
from modules.admin.entities.activation_code import ActivationCode
from modules.organization.entities.organization import Organization
from modules.organization.entities.organization_member import OrganizationMember
from modules.model_config.entities.user_model_config import UserModelBinding, UserModelProviderCredential

__all__ = [
    "User",
    "Note",
    "NoteFolder",
    "Favorite",
    "KnowledgeBase",
    "KnowledgeBaseSubscription",
    "Document",
    "ChatSession",
    "ChatMessage",
    "ActivationCode",
    "Organization",
    "OrganizationMember",
    "UserModelProviderCredential",
    "UserModelBinding",
]
