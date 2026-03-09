from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class RecallConfig:
    """recall 调用所需的全部配置"""
    es_host: str
    index_names: List[str]
    doc_ids: Optional[List[str]] = None
    top_n: int = 10
    similarity_threshold: float = 0.1
    vector_similarity_weight: float = 0.3
    top_k: int = 1024

    # Embedding 模型
    embedding_model_factory: str = "Tongyi-Qianwen"
    embedding_model_name: str = "text-embedding-v4"
    embedding_base_url: str = ""
    embedding_api_key: str = ""

    # Rerank 模型（可选）
    use_rerank: bool = False
    rerank_factory: Optional[str] = None
    rerank_model_name: Optional[str] = None
    rerank_base_url: Optional[str] = None
    rerank_api_key: Optional[str] = None
