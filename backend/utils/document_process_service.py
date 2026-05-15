"""Internal RAG document processing service client."""

import logging
from typing import Any, Dict

from config.settings import settings
from utils.http_client import get_http_client, get_rag_internal_headers

logger = logging.getLogger(__name__)


class DocumentProcessService:
    """Client for document processing service (chunking, embedding, storage)."""

    @staticmethod
    async def parse_document(
        file_path: str,
        document_id: str,
        index_name: str,
        filename: str,
    ) -> Dict[str, Any]:
        """Parse document: chunk + embed + store to ES."""
        try:
            with open(file_path, "rb") as f:
                file_data = f.read()

            files = {"file": (filename, file_data)}
            data = {
                "model_factory": settings.EMBEDDING_MODEL_FACTORY,
                "model_name": settings.EMBEDDING_MODEL_NAME,
                "base_url": settings.EMBEDDING_BASE_URL,
                "index_name": index_name,
                "document_id": document_id,
                "parser_type": settings.DEFAULT_PARSER_TYPE,
                "chunk_token_num": str(settings.DEFAULT_CHUNK_TOKEN_NUM),
                "es_host": settings.ES_HOST,
            }

            if settings.EMBEDDING_API_KEY:
                data["api_key"] = settings.EMBEDDING_API_KEY

            response = await get_http_client().post(
                f"{settings.DOC_PROCESS_BASE_URL}/parse-document",
                files=files,
                data=data,
                headers=get_rag_internal_headers(),
            )
            response.raise_for_status()
            result = response.json()

            if not result.get("success"):
                raise Exception(f"Document parsing failed: {result.get('message')}")

            return result["data"]

        except Exception as e:
            logger.error(f"Document parsing error: {e}")
            raise

    @staticmethod
    async def get_task_status(task_id: str) -> Dict[str, Any]:
        """Get document processing task status."""
        try:
            response = await get_http_client().get(
                f"{settings.DOC_PROCESS_BASE_URL}/task-status/{task_id}",
                headers=get_rag_internal_headers(),
            )
            response.raise_for_status()
            result = response.json()

            if not result.get("success"):
                raise Exception(f"Failed to get task status: {result.get('message')}")

            return result

        except Exception as e:
            logger.error(f"Get task status error: {e}")
            raise

    @staticmethod
    async def delete_document_from_es(document_id: str, index_name: str) -> Dict[str, Any]:
        """Delete document chunks from Elasticsearch."""
        try:
            payload = {
                "document_id": document_id,
                "index_name": index_name,
                "es_host": settings.ES_HOST,
            }

            response = await get_http_client().post(
                f"{settings.DOC_PROCESS_BASE_URL}/delete-document",
                json=payload,
                headers=get_rag_internal_headers(),
            )
            response.raise_for_status()
            result = response.json()

            if not result.get("success"):
                raise Exception(f"ES deletion failed: {result.get('message')}")

            return result["data"]

        except Exception as e:
            logger.error(f"Delete from ES error: {e}")
            raise
