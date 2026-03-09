"""Application configuration settings."""
from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""
    
    # ============================================================================
    # 应用基础配置
    # ============================================================================
    APP_NAME: str = "Reader API"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False
    API_PREFIX: str = "/api"
    CORS_ORIGINS: List[str] = [
        "http://localhost:3003",
        "http://101.126.153.146:30003",
        "*"
    ]
    
    # ============================================================================
    # 数据库配置
    # ============================================================================
    DATABASE_URL: str = "postgresql+asyncpg://reader:reader_dev_password@reader_postgres:5432/reader_qaq"
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 10
    
    # ============================================================================
    # Redis 配置
    # ============================================================================
    REDIS_URL: str = "redis://reader:agent123@redis:6378/2"
    
    # ============================================================================
    # MinIO/S3 对象存储配置
    # ============================================================================
    MINIO_ENDPOINT: str = "reader_minio:9000"
    MINIO_PUBLIC_ENDPOINT: str = "nginx"
    MINIO_ACCESS_KEY: str = "reader"
    MINIO_SECRET_KEY: str = "reader_dev_password"
    MINIO_BUCKET: str = "reader-uploads"
    MINIO_SECURE: bool = False
    
    # ============================================================================
    # JWT 认证配置
    # ============================================================================
    SECRET_KEY: str = "your-secret-key-change-in-production-please-use-random-string"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30
    
    # ============================================================================
    # 上传限制配置
    # ============================================================================
    MAX_UPLOAD_SIZE: int = 100 * 1024 * 1024  # 100MB
    MAX_AVATAR_SIZE: int = 10 * 1024 * 1024  # 10MB
    
    # ============================================================================
    # 速率限制配置
    # ============================================================================
    RATE_LIMIT_PER_MINUTE: int = 60
    
    # ============================================================================
    # MinerU 文档解析服务配置
    # ============================================================================
    MINERU_API_BASE_URL: str = "https://mineru.net/api/v4"
    MINERU_API_TOKEN: str = ""
    MINERU_MODEL_VERSION: str = "vlm"

    # ============================================================================
    # RAG Agent 文档处理服务配置
    # ============================================================================
    DOC_PROCESS_BASE_URL: str = "http://rag:7791/api"
    AGENT_SYSTEM_URL: str = "http://reader_agent:8009"
    
    # ============================================================================
    # Elasticsearch 配置
    # ============================================================================
    ES_HOST: str = "http://elasticsearch:9200"
    
    # ============================================================================
    # Embedding 模型配置
    # ============================================================================
    EMBEDDING_MODEL_FACTORY: str = "Tongyi-Qianwen"
    EMBEDDING_MODEL_NAME: str = "text-embedding-v4"
    EMBEDDING_BASE_URL: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    EMBEDDING_API_KEY: str = ""

    # ============================================================================
    # Rerank 模型配置
    # ============================================================================
    RERANK_FACTORY: str = "Tongyi-Qianwen"
    RERANK_MODEL_NAME: str = "qwen3-rerank"
    RERANK_BASE_URL: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    RERANK_API_KEY: str = ""
    
    # ============================================================================
    # 文档处理配置
    # ============================================================================
    DEFAULT_CHUNK_TOKEN_NUM: int = 512
    DEFAULT_PARSER_TYPE: str = "general"
    
    # ============================================================================
    # 搜索配置
    # ============================================================================
    DEFAULT_TOP_N: int = 8
    SIMILARITY_THRESHOLD: float = 0.01
    VECTOR_SIMILARITY_WEIGHT: float = 0.3

    # ============================================================================
    # LLM 配置 - 普通用户
    # ============================================================================
    LLM_MODEL_NAME: str = "qwen-plus"
    LLM_MODEL_URL: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    LLM_API_KEY: str = ""
    LLM_TEMPERATURE: float = 0.7
    LLM_TOP_P: float = 0.3
    LLM_MAX_TOKENS: int = 100000
    LLM_MAX_CONTEXT_TOKENS: int = 128000
    
    # ============================================================================
    # LLM 配置 - 会员用户
    # ============================================================================
    MEMBER_LLM_MODEL_NAME: str = "qwen3-max"
    MEMBER_LLM_MODEL_URL: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    MEMBER_LLM_API_KEY: str = ""
    MEMBER_LLM_MAX_CONTEXT_TOKENS: int = 128000
    
    # ============================================================================
    # Recall 配置
    # ============================================================================
    RECALL_TOP_N: int = 10
    RECALL_SIMILARITY_THRESHOLD: float = 0.01
    RECALL_VECTOR_SIMILARITY_WEIGHT: float = 0.3
    RECALL_USE_RERANK: bool = False
    
    # ============================================================================
    # 搜索引擎配置
    # ============================================================================
    SEARCH_ENGINE: str = "bocha"
    SEARCH_ENGINE_API_KEY: str = ""

    # ============================================================================
    # 邮件服务配置
    # ============================================================================
    SMTP_HOST: str = "smtpdm.aliyun.com"
    SMTP_PORT: int = 465
    SMTP_USE_SSL: bool = True
    SMTP_USERNAME: str = "no-reply@ireader.online"
    SMTP_PASSWORD: str = ""
    SMTP_FROM_NAME: str = "Reader AI"
    SMTP_TIMEOUT: int = 10
    
    # ============================================================================
    # HTTP 客户端超时配置（秒）
    # ============================================================================
    HTTP_DEFAULT_TIMEOUT: float = 60.0
    HTTP_UPLOAD_TIMEOUT: float = 300.0
    HTTP_DOWNLOAD_TIMEOUT: float = 120.0
    
    # ============================================================================
    # 安全配置
    # ============================================================================
    MAX_PASSWORD_LENGTH: int = 72
    
    # ============================================================================
    # 用户默认配置
    # ============================================================================
    DEFAULT_USER_FOLDERS: List[str] = ["学习", "工作", "生活"]
    DEFAULT_KB_NAME: str = "我的知识库"
    DEFAULT_KB_DESCRIPTION: str = "这是您的第一个知识库，您可以在这里上传和管理文档"
    DEFAULT_KB_CATEGORY: str = "其它"
    
    class Config:
        env_file = ".env"
        case_sensitive = True
        extra = "ignore"


settings = Settings()
