"""ReAct Agent 配置 - 从统一配置文件加载"""
from dataclasses import dataclass, field
from typing import Tuple

from config import get_settings


@dataclass
class ReActConfig:
    """ReAct Agent 配置类 - 所有配置从 .env 文件加载"""
    
    # 最大循环次数，防止无限循环
    max_iterations: int = field(default_factory=lambda: get_settings().react_max_iterations)
    
    # Scratchpad 最大 token 数
    max_scratchpad_tokens: int = field(default_factory=lambda: get_settings().react_max_scratchpad_tokens)
    
    # 工具执行超时时间（秒）
    tool_timeout: float = field(default_factory=lambda: get_settings().react_tool_timeout)
    
    # 当 token 使用率超过此阈值时，警告 Agent 尽快完成
    token_warning_threshold: float = field(default_factory=lambda: get_settings().react_token_warning_threshold)
    
    # 可用工具列表（这个保持硬编码，因为是功能性配置而非参数）
    available_tools: Tuple[str, ...] = ("recall", "web_search", "finish")
    
    # Hook 相关配置
    enable_hooks: bool = field(default_factory=lambda: get_settings().react_enable_hooks)
    enable_loop_detection: bool = field(default_factory=lambda: get_settings().react_enable_loop_detection)
    max_same_tool_calls: int = field(default_factory=lambda: get_settings().react_max_same_tool_calls)
    
    # 完成检测配置
    enable_completion_detection: bool = field(default_factory=lambda: get_settings().react_enable_completion_detection)
    min_successful_calls: int = field(default_factory=lambda: get_settings().react_min_successful_calls)
    max_consecutive_errors: int = field(default_factory=lambda: get_settings().react_max_consecutive_errors)
    
    # 思考过程可视化配置
    show_iteration_progress: bool = field(default_factory=lambda: get_settings().react_show_iteration_progress)
    show_scratchpad_stats: bool = field(default_factory=lambda: get_settings().react_show_scratchpad_stats)
    
    def __post_init__(self):
        """验证配置"""
        if self.max_iterations < 1:
            raise ValueError("max_iterations must be at least 1")
        if self.max_scratchpad_tokens < 100:
            raise ValueError("max_scratchpad_tokens must be at least 100")
        if self.tool_timeout <= 0:
            raise ValueError("tool_timeout must be positive")


def get_react_config() -> ReActConfig:
    """获取 ReAct 配置实例（每次调用都会从 settings 读取最新值）"""
    return ReActConfig()


# 默认配置实例（为了向后兼容）
DEFAULT_REACT_CONFIG = ReActConfig()
