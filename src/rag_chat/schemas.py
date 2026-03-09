"""
RAG 数据模型
"""
from typing import List, Optional
from pydantic import BaseModel


class ChatMessage(BaseModel):
    """聊天消息"""
    role: str  # "user" | "assistant"
    content: str


class LLMConfig(BaseModel):
    """LLM 配置"""
    model_name: str
    model_url: str
    api_key: str
    temperature: float = 0.7
    top_p: float = 0.3
    max_tokens: int = 1000


class RecallConfig(BaseModel):
    """召回配置"""
    index_names: List[str]  # ES 索引名称列表
    es_host: str
    top_n: int = 8
    similarity_threshold: float = 0.2
    vector_similarity_weight: float = 0.3
    top_k: int = 1024
    doc_ids: Optional[List[str]] = None  # 文档ID限制
    
    # Embedding 模型配置
    model_factory: str
    model_name: str
    model_base_url: str
    model_api_key: str
    
    # Rerank 模型配置
    rerank_factory: str
    rerank_model_name: str
    rerank_base_url: str
    rerank_api_key: str


class ThinkingConfig(BaseModel):
    """思考配置"""
    max_sub_questions: int = 4
    max_iterations: int = 2
    enable_question_refinement: bool = True


class RAGChatRequest(BaseModel):
    """RAG 服务请求"""
    mode: str  # "chat" | "think" (RAG服务要求)
    session_id: str
    messages: List[ChatMessage]
    llm: LLMConfig
    recall: RecallConfig
    thinking: ThinkingConfig
    knowledge_base: bool = True  # 是否启用知识库检索
    tavily: bool = False  # 是否启用联网搜索
    tavily_api_key: Optional[str] = None
    show_quote: bool = False
    stream: bool = True


class ChatRequest(BaseModel):
    """前端聊天请求"""
    kb_id: Optional[str] = None  # 知识库ID（可选）
    doc_ids: Optional[List[str]] = None  # 文档ID列表（可选）
    message: str  # 用户消息
    session_id: str  # 会话ID
    mode: str = "deep"  # "deep" | "search" (保留兼容性)
    enable_web_search: bool = False  # 是否启用联网搜索
    show_thinking: bool = True  # 是否显示思考过程（深度思考模式）


class StreamChunk(BaseModel):
    """流式响应块"""
    type: str  # "token" | "error" | "thinking" | "final_answer" | "token_usage"
    content: str


class TokenUsageData(BaseModel):
    """Token usage data from Agent System"""
    input_tokens: int
    output_tokens: int
    total_tokens: int
    model_name: str
    session_id: Optional[str] = None
    user_id: Optional[str] = None
    request_type: Optional[str] = None


class ChatResponse(BaseModel):
    """完整响应"""
    content: str

