"""Shared retrieval package used by the backend and RAG service.

Public attributes are loaded lazily so an Elasticsearch-only consumer does not
also import the NLP stack and every optional model provider.
"""

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .config import RecallConfig
    from .es_adapter import ESAdapter
    from .es_connection import SimpleESConnection
    from .model_factory import create_embedding_model, create_rerank_model
    from .retriever import DeepRagPureRetriever, DeepRagRetrievalConfig

__all__ = [
    "DeepRagPureRetriever",
    "DeepRagRetrievalConfig",
    "ESAdapter",
    "SimpleESConnection",
    "RecallConfig",
    "create_embedding_model",
    "create_rerank_model",
]

_EXPORTS = {
    "DeepRagPureRetriever": (".retriever", "DeepRagPureRetriever"),
    "DeepRagRetrievalConfig": (".retriever", "DeepRagRetrievalConfig"),
    "ESAdapter": (".es_adapter", "ESAdapter"),
    "SimpleESConnection": (".es_connection", "SimpleESConnection"),
    "RecallConfig": (".config", "RecallConfig"),
    "create_embedding_model": (".model_factory", "create_embedding_model"),
    "create_rerank_model": (".model_factory", "create_rerank_model"),
}


def __getattr__(name: str) -> Any:
    try:
        module_name, attribute_name = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc

    value = getattr(import_module(module_name, __name__), attribute_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
