"""recall_lib — 共享检索包

从 rag/ 中抽取的文档检索功能，供 src 和 agent 直接 import 使用。
"""

from .retriever import DeepRagPureRetriever, DeepRagRetrievalConfig
from .es_adapter import ESAdapter
from .es_connection import SimpleESConnection
from .model_factory import create_embedding_model, create_rerank_model
from .config import RecallConfig

__all__ = [
    "DeepRagPureRetriever",
    "DeepRagRetrievalConfig",
    "ESAdapter",
    "SimpleESConnection",
    "RecallConfig",
    "create_embedding_model",
    "create_rerank_model",
]
