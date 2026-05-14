"""Application configuration settings."""
import base64
import hashlib
from pathlib import Path
from typing import List

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=str(Path(__file__).resolve().parent.parent / ".env"),
        case_sensitive=True,
        extra="ignore",
    )
    
    # ============================================================================
    # 应用基础配置
    # ============================================================================
    APP_NAME: str = "Lumen API"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False
    API_PREFIX: str = "/api"
    CORS_ORIGINS: List[str] = [
        "http://localhost:3003",
        "http://127.0.0.1:3003",
    ]
    
    # ============================================================================
    # 数据库配置
    # ============================================================================
    DATABASE_URL: str
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 10
    
    # ============================================================================
    # Redis 配置
    # ============================================================================
    REDIS_URL: str
    
    # ============================================================================
    # MinIO/S3 对象存储配置
    # ============================================================================
    MINIO_ENDPOINT: str = "minio:9000"
    MINIO_PUBLIC_ENDPOINT: str = "nginx"
    MINIO_ACCESS_KEY: str
    MINIO_SECRET_KEY: str
    MINIO_BUCKET: str = "reader-uploads"
    MINIO_SECURE: bool = False
    
    # ============================================================================
    # JWT 认证配置
    # ============================================================================
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days
    
    # ============================================================================
    # 上传限制配置
    # ============================================================================
    MAX_UPLOAD_SIZE: int = 100 * 1024 * 1024  # 100MB
    MAX_AVATAR_SIZE: int = 10 * 1024 * 1024  # 10MB
    
    # MinerU 文档解析服务配置
    # ============================================================================
    MINERU_API_BASE_URL: str = "https://mineru.net/api/v4"
    MINERU_API_TOKEN: str = ""
    MINERU_MODEL_VERSION: str = "vlm"

    # ============================================================================
    # RAG Agent 文档处理服务配置
    # ============================================================================
    DOC_PROCESS_BASE_URL: str = "http://rag:7791/api"
    RAG_INTERNAL_API_TOKEN: str = ""

    # ============================================================================
    # lumen 运行时配置
    # ============================================================================
    INSIGHT_GATEWAY_URL: str = "http://lumen_gateway:8001"
    INSIGHT_LANGGRAPH_URL: str = "http://lumen_langgraph:2024"
    INSIGHT_GATEWAY_PUBLIC_BASE_URL: str = ""
    INSIGHT_LANGGRAPH_PUBLIC_BASE_URL: str = ""
    INSIGHT_ASSISTANT_ID: str = "lead_agent"
    INSIGHT_ON_DISCONNECT: str = "continue"
    INSIGHT_RECURSION_LIMIT: int = 300
    INSIGHT_REQUEST_TIMEOUT_SECONDS: float = 120.0
    
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
    SIMILARITY_THRESHOLD: float = 0.01
    VECTOR_SIMILARITY_WEIGHT: float = 0.3

    # ============================================================================
    # 邮件服务配置
    # ============================================================================
    SMTP_HOST: str = "smtpdm.aliyun.com"
    SMTP_PORT: int = 465
    SMTP_USE_SSL: bool = True
    SMTP_USERNAME: str = "no-reply@ireader.online"
    SMTP_PASSWORD: str = ""
    SMTP_FROM_NAME: str = "Lumen"
    SMTP_TIMEOUT: int = 10

    # ============================================================================
    # 模型配置安全
    # ============================================================================
    MODEL_CONFIG_ENCRYPTION_KEY: str = ""
    MODEL_CONFIG_TOKEN_EXPIRE_SECONDS: int = 60 * 60 * 6
    
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
    AUTH_RATE_LIMIT_WINDOW_SECONDS: int = 300
    AUTH_RATE_LIMIT_LOGIN_MAX: int = 10
    AUTH_RATE_LIMIT_SEND_CODE_MAX: int = 5
    AUTH_RATE_LIMIT_REGISTER_MAX: int = 5
    AUTH_RATE_LIMIT_RESET_PASSWORD_MAX: int = 5
    AUTH_RATE_LIMIT_LOGIN_IP_MAX: int = 50
    AUTH_RATE_LIMIT_SEND_CODE_IP_MAX: int = 20
    AUTH_RATE_LIMIT_REGISTER_IP_MAX: int = 20
    AUTH_RATE_LIMIT_RESET_PASSWORD_IP_MAX: int = 20
    
    # ============================================================================
    # 用户默认配置
    # ============================================================================
    DEFAULT_USER_FOLDERS: List[str] = ["学习", "工作", "生活"]
    DEFAULT_KB_NAME: str = "我的知识库"
    DEFAULT_KB_DESCRIPTION: str = "这是您的第一个知识库，您可以在这里上传和管理文档"
    DEFAULT_KB_CATEGORY: str = "其它"

    @field_validator(
        "DATABASE_URL",
        "REDIS_URL",
        "MINIO_ACCESS_KEY",
        "MINIO_SECRET_KEY",
        "SECRET_KEY",
    )
    @classmethod
    def validate_required_secret_like_value(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("配置项不能为空")
        insecure_placeholders = {
            "change-me",
            "reader_dev_password",
            "agent123",
            "your-secret-key-change-in-production-please-use-random-string",
            "change-me-use-a-long-random-string",
        }
        if normalized in insecure_placeholders:
            raise ValueError("检测到不安全的默认凭据，请在环境变量中配置真实值")
        return normalized

    @field_validator("SECRET_KEY")
    @classmethod
    def validate_secret_key_strength(cls, value: str) -> str:
        if len(value) < 32:
            raise ValueError("SECRET_KEY 长度至少需要 32 个字符")
        return value

    @model_validator(mode="after")
    def validate_cors_origins(self) -> "Settings":
        if "*" in self.CORS_ORIGINS:
            raise ValueError("CORS_ORIGINS 不允许包含 '*'，请显式列出允许的域名")
        return self

    @property
    def model_config_encryption_secret(self) -> str:
        normalized = str(self.MODEL_CONFIG_ENCRYPTION_KEY or "").strip()
        return normalized or self.SECRET_KEY

    @property
    def model_config_fernet_key(self) -> str:
        digest = hashlib.sha256(self.model_config_encryption_secret.encode("utf-8")).digest()
        return base64.urlsafe_b64encode(digest).decode("utf-8")


settings = Settings()
