"""Agent 系统常量定义 - 从统一配置文件加载"""
from config import get_settings

# 获取 settings 实例
_settings = get_settings()

# 缓存相关
RECALL_TOOL_CACHE_SIZE = _settings.recall_tool_cache_size    # RecallTool 缓存大小
REDIS_SCAN_COUNT = _settings.redis_scan_count                # Redis SCAN 每次返回数量

# 并发控制
MAX_CONCURRENT_LLM_CALLS = _settings.max_concurrent_llm_calls  # 最大并发 LLM 调用数
