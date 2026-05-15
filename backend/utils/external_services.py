"""Compatibility exports for external service clients."""

from utils.document_process_service import DocumentProcessService
from utils.http_client import close_http_client, get_http_client, get_rag_internal_headers
from utils.mineru_service import MineruService

__all__ = [
    "DocumentProcessService",
    "MineruService",
    "close_http_client",
    "get_http_client",
    "get_rag_internal_headers",
]
