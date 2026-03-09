"""Application settings and configuration."""
from functools import lru_cache
from typing import Dict, Any

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Application settings loaded from environment variables.
    
    All configurable variables are loaded from .env file.
    """
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        protected_namespaces=()  # Disable protected namespace warning
    )
    
    # ========== LLM 配置 ==========
    temperature: float = 0.3  # LLM 温度（用于推理和压缩摘要）
    api_format: str = "openai"  # API 格式: "openai" 或 "anthropic"
    
    # ========== 上下文和 Token 管理 ==========
    max_context_tokens: int = 128000  # 模型最大上下文窗口（默认值，实际由请求传入）
    direct_content_threshold: float = 0.7  # 直接内容模式阈值
    
    # ========== Agent 基础配置 ==========
    enable_web_search: bool = False  # Web 搜索默认开关
    execution_timeout: int = 300  # 执行超时时间（秒）
    max_concurrent_llm_calls: int = 10  # 最大并发 LLM 调用数
    
    # ========== 上下文压缩配置 ==========
    compression_threshold_ratio: float = 0.8  # 达到80%上下文时触发压缩
    compression_preserve_ratio: float = 0.3  # 保留最近30%的消息不压缩
    
    # ========== 时间窗口注入配置 ==========
    intent_recognition_turns: int = 2
    planning_turns: int = 2
    answer_generation_turns: int = 3
    execution_turns: int = 0
    
    # ========== ReAct Agent 配置 ==========
    react_max_iterations: int = 20  # 最大循环次数
    react_max_scratchpad_tokens: int = 25600  # Scratchpad 最大 token 数
    react_tool_timeout: float = 30.0  # 工具执行超时时间（秒）
    react_token_warning_threshold: float = 0.85  # Token 使用率警告阈值
    react_enable_hooks: bool = True  # 是否启用 Hook 机制
    react_enable_loop_detection: bool = True  # 是否启用循环检测
    react_max_same_tool_calls: int = 2  # 同一工具相同输入的最大调用次数
    react_enable_completion_detection: bool = True  # 是否启用智能完成检测
    react_min_successful_calls: int = 2  # 至少需要的成功工具调用数
    react_max_consecutive_errors: int = 5  # 最大连续错误数
    react_show_iteration_progress: bool = True  # 是否显示迭代进度
    react_show_scratchpad_stats: bool = True  # 是否显示 scratchpad 统计
    
    # ========== 缓存配置 ==========
    recall_tool_cache_size: int = 100  # RecallTool 缓存大小
    enable_cache: bool = True  # 是否启用缓存
    cache_read_timeout: int = 2  # 缓存读取超时（秒）
    batch_size: int = 50  # 批处理大小
    
    # ========== Web 搜索配置 ==========
    tavily_max_results: int = 5  # Tavily 搜索最大结果数
    search_max_results: int = 5  # 搜索结果数量
    
    # ========== Redis 配置 ==========
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    redis_username: str = ""
    redis_password: str = ""
    redis_socket_timeout: int = 5
    redis_socket_connect_timeout: int = 5
    redis_scan_count: int = 100  # Redis SCAN 每次返回数量
    session_cache_ttl: int = 3600
    message_cache_ttl: int = 1800
    
    # ========== PostgreSQL 配置 ==========
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "reader_qaq"
    postgres_user: str = "reader"
    postgres_password: str = "reader_dev_password"
    postgres_pool_size: int = 20
    postgres_max_overflow: int = 30
    
    # ========== 取消管理器配置 ==========
    cancellation_expiry_seconds: int = 300  # 取消记录过期时间（秒）
    
    # ========== Logging & API ==========
    log_level: str = "INFO"
    log_file: str = "./logs/agent.log"
    api_host: str = "0.0.0.0"
    api_port: int = 8009
    
    # ========== 计算属性 ==========
    
    @property
    def redis_url(self) -> str:
        """获取Redis连接URL"""
        if self.redis_password:
            return f"redis://:{self.redis_password}@{self.redis_host}:{self.redis_port}/{self.redis_db}"
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"
    
    @property
    def postgres_url(self) -> str:
        """获取PostgreSQL连接URL"""
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )
    
    @property
    def injection_strategy(self) -> Dict[str, Dict[str, Any]]:
        """获取注入策略配置"""
        return {
            "intent_recognition": {
                "turn_count": self.intent_recognition_turns,
                "include_compression": True
            },
            "planning": {
                "turn_count": self.planning_turns,
                "include_compression": True
            },
            "execution": {
                "turn_count": self.execution_turns,
                "include_compression": False
            },
            "answer_generation": {
                "turn_count": self.answer_generation_turns,
                "include_compression": True
            }
        }


@lru_cache()
def get_settings() -> Settings:
    """
    Get cached settings instance.
    
    All configuration is loaded from environment variables (.env file).
    """
    return Settings()
