"""
RAG 服务配置
从主配置文件 config/settings.py 读取，保持向后兼容
"""
from typing import Tuple
from config.settings import settings


class RAGSettings:
    """RAG 服务配置 - 代理到主 settings"""
    
    # ============================================================================
    # Agent System 配置
    # ============================================================================
    @property
    def AGENT_SYSTEM_URL(self) -> str:
        return settings.AGENT_SYSTEM_URL
    
    # ============================================================================
    # LLM 配置 - 普通用户
    # ============================================================================
    @property
    def LLM_MODEL_NAME(self) -> str:
        return settings.LLM_MODEL_NAME
    
    @property
    def LLM_MODEL_URL(self) -> str:
        return settings.LLM_MODEL_URL
    
    @property
    def LLM_API_KEY(self) -> str:
        return settings.LLM_API_KEY
    
    @property
    def LLM_TEMPERATURE(self) -> float:
        return settings.LLM_TEMPERATURE
    
    @property
    def LLM_TOP_P(self) -> float:
        return settings.LLM_TOP_P
    
    @property
    def LLM_MAX_TOKENS(self) -> int:
        return settings.LLM_MAX_TOKENS
    
    @property
    def LLM_MAX_CONTEXT_TOKENS(self) -> int:
        return settings.LLM_MAX_CONTEXT_TOKENS
    
    # ============================================================================
    # LLM 配置 - 会员用户
    # ============================================================================
    @property
    def MEMBER_LLM_MODEL_NAME(self) -> str:
        return settings.MEMBER_LLM_MODEL_NAME
    
    @property
    def MEMBER_LLM_MODEL_URL(self) -> str:
        return settings.MEMBER_LLM_MODEL_URL
    
    @property
    def MEMBER_LLM_API_KEY(self) -> str:
        return settings.MEMBER_LLM_API_KEY
    
    @property
    def MEMBER_LLM_MAX_CONTEXT_TOKENS(self) -> int:
        return settings.MEMBER_LLM_MAX_CONTEXT_TOKENS
    
    def get_llm_config(self, is_member: bool = False) -> Tuple[str, str, str, int]:
        """
        根据用户等级获取 LLM 配置
        
        Args:
            is_member: 是否为会员用户（member 或 premium）
            
        Returns:
            Tuple[model_name, model_url, api_key, max_context_tokens]
        """
        if is_member:
            return (
                self.MEMBER_LLM_MODEL_NAME,
                self.MEMBER_LLM_MODEL_URL,
                self.MEMBER_LLM_API_KEY,
                self.MEMBER_LLM_MAX_CONTEXT_TOKENS
            )
        else:
            return (
                self.LLM_MODEL_NAME,
                self.LLM_MODEL_URL,
                self.LLM_API_KEY,
                self.LLM_MAX_CONTEXT_TOKENS
            )
    
    # ============================================================================
    # Recall 配置
    # ============================================================================
    @property
    def RECALL_TOP_N(self) -> int:
        return settings.RECALL_TOP_N
    
    @property
    def RECALL_SIMILARITY_THRESHOLD(self) -> float:
        return settings.RECALL_SIMILARITY_THRESHOLD
    
    @property
    def RECALL_VECTOR_SIMILARITY_WEIGHT(self) -> float:
        return settings.RECALL_VECTOR_SIMILARITY_WEIGHT
    
    @property
    def RECALL_USE_RERANK(self) -> bool:
        return settings.RECALL_USE_RERANK
    
    @property
    def RECALL_API_URL(self) -> str:
        return getattr(settings, 'RECALL_API_URL', f"{settings.DOC_PROCESS_BASE_URL}/recall")
    
    # ============================================================================
    # Embedding 模型配置
    # ============================================================================
    @property
    def EMBEDDING_MODEL_FACTORY(self) -> str:
        return settings.EMBEDDING_MODEL_FACTORY
    
    @property
    def EMBEDDING_MODEL_NAME(self) -> str:
        return settings.EMBEDDING_MODEL_NAME
    
    @property
    def EMBEDDING_BASE_URL(self) -> str:
        return settings.EMBEDDING_BASE_URL
    
    @property
    def EMBEDDING_API_KEY(self) -> str:
        return settings.EMBEDDING_API_KEY
    
    # ============================================================================
    # Rerank 模型配置
    # ============================================================================
    @property
    def RERANK_FACTORY(self) -> str:
        return settings.RERANK_FACTORY
    
    @property
    def RERANK_MODEL_NAME(self) -> str:
        return settings.RERANK_MODEL_NAME
    
    @property
    def RERANK_BASE_URL(self) -> str:
        return settings.RERANK_BASE_URL
    
    @property
    def RERANK_API_KEY(self) -> str:
        return settings.RERANK_API_KEY
    
    # ============================================================================
    # Elasticsearch 配置
    # ============================================================================
    @property
    def ES_HOST(self) -> str:
        return settings.ES_HOST
    
    # ============================================================================
    # 搜索引擎配置
    # ============================================================================
    @property
    def SEARCH_ENGINE(self) -> str:
        return settings.SEARCH_ENGINE
    
    @property
    def SEARCH_ENGINE_API_KEY(self) -> str:
        return settings.SEARCH_ENGINE_API_KEY


# 全局配置实例
rag_settings = RAGSettings()
