"""Internal RAG document processing service client."""

import asyncio
import hashlib
import logging
import re
from typing import Any, Dict
from urllib.parse import quote
from uuid import UUID

from config.settings import settings
from utils.http_client import get_internal_http_client, get_rag_internal_headers

logger = logging.getLogger(__name__)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_OPAQUE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_IDEMPOTENCY_DOMAIN = "lumen-rag-parse-v1"

DOCUMENT_ERROR_MESSAGES = {
    "knowledge_base": "The knowledge base is no longer available",
    "source": "The original document is unavailable",
    "empty": "The uploaded document is empty",
    "conversion": "Document conversion failed",
    "extraction": "Document text extraction failed",
    "storage": "Document content storage failed",
    "index_submit": "Document indexing could not be started",
    "index_status": "Document indexing status could not be confirmed",
    "index_failed": "Document indexing failed",
    "index_timeout": "Document indexing timed out",
    "cancellation": "Document processing cancellation could not be confirmed",
    "cleanup": "Document cleanup failed",
    "processing": "Document processing failed",
}
_LEGACY_ERROR_ALIASES = {
    "Knowledge base no longer exists": DOCUMENT_ERROR_MESSAGES["knowledge_base"],
    "Original document object is missing": DOCUMENT_ERROR_MESSAGES["source"],
    "Processing timeout": DOCUMENT_ERROR_MESSAGES["index_timeout"],
}
_RAG_TASK_STATUSES = frozenset(
    {
        "queued",
        "pending",
        "processing",
        "chunking",
        "embedding",
        "storing",
        "completed",
        "failed",
        "cancelled",
    }
)
_RAG_CANCELLATION_STATES = frozenset(
    {"cancelled", "cancellation_requested", "not_found"}
)


class DocumentProcessingError(RuntimeError):
    """Stable stage failure that never contains provider-derived text."""

    def __init__(self, stage: str, *, error_type: str) -> None:
        self.stage = stage if stage in DOCUMENT_ERROR_MESSAGES else "processing"
        self.error_type = _safe_error_type(error_type)
        self.public_message = DOCUMENT_ERROR_MESSAGES[self.stage]
        super().__init__(self.public_message)


def _safe_error_type(value: object) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "Error"))[:80]
    return normalized or "Error"


def _safe_opaque_id(value: object) -> str:
    normalized = str(value or "").strip()
    return normalized if _OPAQUE_ID_RE.fullmatch(normalized) else "invalid"


def _validate_task_id(value: object) -> str:
    normalized = str(value or "").strip()
    if _OPAQUE_ID_RE.fullmatch(normalized) is None:
        raise ValueError("invalid opaque task identifier")
    return normalized


def public_document_error(
    exc: BaseException | None, *, default_stage: str = "processing"
) -> str:
    stage = exc.stage if isinstance(exc, DocumentProcessingError) else default_stage
    return DOCUMENT_ERROR_MESSAGES.get(stage, DOCUMENT_ERROR_MESSAGES["processing"])


def sanitize_persisted_document_error(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value)
    if normalized in DOCUMENT_ERROR_MESSAGES.values():
        return normalized
    return _LEGACY_ERROR_ALIASES.get(normalized, DOCUMENT_ERROR_MESSAGES["processing"])


def _log_client_error(
    *,
    stage: str,
    exc: BaseException,
    document_id: object | None = None,
    task_id: object | None = None,
) -> None:
    logger.error(
        "document_process_client stage=%s document_id=%s task_id=%s error_type=%s",
        stage,
        _safe_opaque_id(document_id),
        _safe_opaque_id(task_id),
        _safe_error_type(type(exc).__name__),
    )


def _successful_response_payload(response: Any) -> dict[str, Any]:
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or payload.get("success") is not True:
        raise ValueError("invalid internal service response")
    return payload


def _sha256_file(file_path: str, *, chunk_size: int = 64 * 1024) -> str:
    digest = hashlib.sha256()
    with open(file_path, "rb") as source:
        while chunk := source.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _build_parse_idempotency_key(document_id: str, content_sha256: str) -> str:
    normalized_document_id = str(UUID(str(document_id).strip()))
    normalized_digest = str(content_sha256).strip().lower()
    if _SHA256_RE.fullmatch(normalized_digest) is None:
        raise ValueError("content_sha256 must be a lowercase SHA-256 digest")
    identity = f"{_IDEMPOTENCY_DOMAIN}\0{normalized_document_id}\0{normalized_digest}"
    return hashlib.sha256(identity.encode("ascii")).hexdigest()


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
            content_sha256 = await asyncio.to_thread(_sha256_file, file_path)
            idempotency_key = _build_parse_idempotency_key(
                document_id,
                content_sha256,
            )
            with open(file_path, "rb") as f:
                files = {"file": (filename, f, "text/markdown")}
                data = {
                    "model_factory": settings.EMBEDDING_MODEL_FACTORY,
                    "model_name": settings.EMBEDDING_MODEL_NAME,
                    "base_url": settings.EMBEDDING_BASE_URL,
                    "index_name": index_name,
                    "document_id": document_id,
                    "idempotency_key": idempotency_key,
                    "parser_type": settings.DEFAULT_PARSER_TYPE,
                    "chunk_token_num": str(settings.DEFAULT_CHUNK_TOKEN_NUM),
                    "es_host": settings.ES_HOST,
                }

                if settings.EMBEDDING_API_KEY:
                    data["api_key"] = settings.EMBEDDING_API_KEY

                response = await get_internal_http_client().post(
                    f"{settings.DOC_PROCESS_BASE_URL}/parse-document",
                    files=files,
                    data=data,
                    headers=get_rag_internal_headers(),
                )
            result = _successful_response_payload(response)
            result_data = result.get("data")
            if not isinstance(result_data, dict):
                raise ValueError("missing parse task data")
            task_id = _validate_task_id(result_data.get("task_id"))
            return {"task_id": task_id}

        except DocumentProcessingError:
            raise
        except Exception as exc:
            _log_client_error(
                stage="index_submit",
                document_id=document_id,
                exc=exc,
            )
            raise DocumentProcessingError(
                "index_submit",
                error_type=type(exc).__name__,
            ) from None

    @staticmethod
    async def get_task_status(task_id: str) -> Dict[str, Any]:
        """Get document processing task status."""
        try:
            normalized_task_id = _validate_task_id(task_id)
            response = await get_internal_http_client().get(
                f"{settings.DOC_PROCESS_BASE_URL}/task-status/{quote(normalized_task_id, safe='')}",
                headers=get_rag_internal_headers(),
            )
            result = _successful_response_payload(response)
            normalized_status = str(result.get("status") or "").strip().lower()
            if normalized_status not in _RAG_TASK_STATUSES:
                raise ValueError("invalid task status")
            raw_data = result.get("data")
            task_data = raw_data if isinstance(raw_data, dict) else {}
            try:
                total_chunks = max(
                    min(int(task_data.get("total_chunks") or 0), 1_000_000_000), 0
                )
            except (TypeError, ValueError):
                total_chunks = 0
            return {
                "status": normalized_status,
                "data": {"total_chunks": total_chunks},
            }

        except DocumentProcessingError:
            raise
        except Exception as exc:
            _log_client_error(
                stage="index_status",
                task_id=task_id,
                exc=exc,
            )
            raise DocumentProcessingError(
                "index_status",
                error_type=type(exc).__name__,
            ) from None

    @staticmethod
    async def cancel_task(task_id: str) -> Dict[str, Any]:
        """Request cancellation of a durable RAG processing task."""
        try:
            normalized_task_id = _validate_task_id(task_id)
            response = await get_internal_http_client().delete(
                f"{settings.DOC_PROCESS_BASE_URL}/task/{quote(normalized_task_id, safe='')}",
                headers=get_rag_internal_headers(),
            )
            result = _successful_response_payload(response)
            raw_data = result.get("data")
            data = raw_data if isinstance(raw_data, dict) else {}
            state = str(data.get("state") or "").strip().lower()
            task_payload = data.get("task")
            task_status = (
                str(task_payload.get("status") or "").strip().lower()
                if isinstance(task_payload, dict)
                else ""
            )
            if state not in _RAG_CANCELLATION_STATES:
                raise ValueError("invalid cancellation state")
            if task_status and task_status not in _RAG_TASK_STATUSES:
                raise ValueError("invalid cancelled task status")
            return {
                "state": state,
                "task": {"status": task_status} if task_status else {},
            }
        except DocumentProcessingError:
            raise
        except Exception as exc:
            _log_client_error(
                stage="cancellation",
                task_id=task_id,
                exc=exc,
            )
            raise DocumentProcessingError(
                "cancellation",
                error_type=type(exc).__name__,
            ) from None

    @staticmethod
    async def delete_document_from_es(
        document_id: str, index_name: str
    ) -> Dict[str, Any]:
        """Delete document chunks from Elasticsearch."""
        try:
            payload = {
                "document_id": document_id,
                "index_name": index_name,
                "es_host": settings.ES_HOST,
            }

            response = await get_internal_http_client().post(
                f"{settings.DOC_PROCESS_BASE_URL}/delete-document",
                json=payload,
                headers=get_rag_internal_headers(),
            )
            _successful_response_payload(response)
            return {}

        except DocumentProcessingError:
            raise
        except Exception as exc:
            _log_client_error(
                stage="cleanup",
                document_id=document_id,
                exc=exc,
            )
            raise DocumentProcessingError(
                "cleanup",
                error_type=type(exc).__name__,
            ) from None
