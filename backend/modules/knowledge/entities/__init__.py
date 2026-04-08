"""ORM entities for the knowledge domain."""

from .document import Document
from .knowledge_base import KNOWLEDGE_CATEGORIES, KnowledgeBase, KnowledgeBaseSubscription

__all__ = [
    "Document",
    "KNOWLEDGE_CATEGORIES",
    "KnowledgeBase",
    "KnowledgeBaseSubscription",
]
