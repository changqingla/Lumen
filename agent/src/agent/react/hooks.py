"""ReAct 工具执行 Hook 机制"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Tuple, Optional, List
from enum import Enum

from ..state import AgentState
from ...utils.logger import get_logger

logger = get_logger(__name__)


class HookAction(Enum):
    """Hook 处理结果"""
    CONTINUE = "continue"  # 继续执行
    SKIP = "skip"  # 跳过此次工具调用
    MODIFY = "modify"  # 修改输入/输出


@dataclass
class HookResult:
    """Hook 执行结果"""
    action: HookAction
    modified_value: Optional[str] = None
    message: Optional[str] = None


class ToolHook(ABC):
    """工具执行钩子基类"""
    
    @abstractmethod
    async def pre_execute(
        self, 
        action: str, 
        action_input: str, 
        state: AgentState
    ) -> HookResult:
        """
        执行前拦截
        
        Args:
            action: 工具名称
            action_input: 工具输入
            state: Agent 状态
            
        Returns:
            HookResult 指示如何处理
        """
        pass
    
    @abstractmethod
    async def post_execute(
        self, 
        action: str, 
        action_input: str,
        observation: str, 
        state: AgentState
    ) -> HookResult:
        """
        执行后处理
        
        Args:
            action: 工具名称
            action_input: 工具输入
            observation: 工具执行结果
            state: Agent 状态
            
        Returns:
            HookResult 指示如何处理
        """
        pass


class QuerySanitizationHook(ToolHook):
    """查询清理钩子 - 清理和优化查询输入"""
    
    # 需要移除的无意义词
    STOP_WORDS = {"请", "帮我", "能否", "可以", "告诉我", "查找", "搜索"}
    
    async def pre_execute(
        self, 
        action: str, 
        action_input: str, 
        state: AgentState
    ) -> HookResult:
        """清理查询输入"""
        if action not in ("recall", "web_search"):
            return HookResult(action=HookAction.CONTINUE)
        
        # 清理查询
        cleaned = action_input.strip()
        
        # 移除常见的无意义前缀
        for word in self.STOP_WORDS:
            if cleaned.startswith(word):
                cleaned = cleaned[len(word):].strip()
        
        # 如果清理后为空，跳过
        if not cleaned:
            return HookResult(
                action=HookAction.SKIP,
                message="查询内容为空，跳过此次调用"
            )
        
        # 如果有修改，返回修改后的值
        if cleaned != action_input:
            logger.debug(f"Query sanitized: '{action_input}' -> '{cleaned}'")
            return HookResult(
                action=HookAction.MODIFY,
                modified_value=cleaned
            )
        
        return HookResult(action=HookAction.CONTINUE)
    
    async def post_execute(
        self, 
        action: str, 
        action_input: str,
        observation: str, 
        state: AgentState
    ) -> HookResult:
        """不做后处理"""
        return HookResult(action=HookAction.CONTINUE)


class ResultValidationHook(ToolHook):
    """结果验证钩子 - 验证工具返回结果的质量"""
    
    # 低质量结果的标识
    LOW_QUALITY_INDICATORS = [
        "未找到",
        "没有找到", 
        "无相关",
        "无法找到",
        "[ERROR]",
    ]
    
    # 最小有效结果长度
    MIN_RESULT_LENGTH = 50
    
    async def pre_execute(
        self, 
        action: str, 
        action_input: str, 
        state: AgentState
    ) -> HookResult:
        """不做前处理"""
        return HookResult(action=HookAction.CONTINUE)
    
    async def post_execute(
        self, 
        action: str, 
        action_input: str,
        observation: str, 
        state: AgentState
    ) -> HookResult:
        """验证结果质量"""
        if action == "finish":
            return HookResult(action=HookAction.CONTINUE)
        
        # 检查是否为空或过短
        if not observation or len(observation.strip()) < self.MIN_RESULT_LENGTH:
            return HookResult(
                action=HookAction.MODIFY,
                modified_value=f"[LOW_QUALITY] 结果过短或为空。原始结果: {observation[:100] if observation else '无'}",
                message="结果质量较低"
            )
        
        # 检查是否包含低质量标识
        for indicator in self.LOW_QUALITY_INDICATORS:
            if indicator in observation:
                # 不修改，但记录日志
                logger.debug(f"Low quality indicator found: {indicator}")
                break
        
        return HookResult(action=HookAction.CONTINUE)


class LoopDetectionHook(ToolHook):
    """循环检测钩子 - 检测重复的工具调用"""
    
    def __init__(self, max_same_calls: int = 2):
        self.max_same_calls = max_same_calls
        self.call_history: List[Tuple[str, str]] = []
    
    async def pre_execute(
        self, 
        action: str, 
        action_input: str, 
        state: AgentState
    ) -> HookResult:
        """检测重复调用"""
        call_key = (action, action_input.strip().lower())
        
        # 统计相同调用次数
        same_call_count = sum(1 for c in self.call_history if c == call_key)
        
        if same_call_count >= self.max_same_calls:
            logger.warning(f"Detected repeated call: {action}({action_input[:50]}...)")
            return HookResult(
                action=HookAction.SKIP,
                message=f"检测到重复调用 {action}，已跳过。请尝试不同的查询方式。"
            )
        
        # 记录此次调用
        self.call_history.append(call_key)
        
        return HookResult(action=HookAction.CONTINUE)
    
    async def post_execute(
        self, 
        action: str, 
        action_input: str,
        observation: str, 
        state: AgentState
    ) -> HookResult:
        """不做后处理"""
        return HookResult(action=HookAction.CONTINUE)
    
    def reset(self):
        """重置调用历史"""
        self.call_history.clear()


class HookManager:
    """Hook 管理器"""
    
    def __init__(self):
        self.hooks: List[ToolHook] = []
    
    def register(self, hook: ToolHook) -> None:
        """注册 Hook"""
        self.hooks.append(hook)
    
    def clear(self) -> None:
        """清空所有 Hook"""
        self.hooks.clear()
    
    async def run_pre_hooks(
        self, 
        action: str, 
        action_input: str, 
        state: AgentState
    ) -> Tuple[str, str, Optional[str]]:
        """
        运行所有 pre-execute hooks
        
        Returns:
            (action, action_input, skip_message) - 如果 skip_message 不为 None，则跳过执行
        """
        current_input = action_input
        
        for hook in self.hooks:
            try:
                result = await hook.pre_execute(action, current_input, state)
                
                if result.action == HookAction.SKIP:
                    return action, current_input, result.message
                elif result.action == HookAction.MODIFY and result.modified_value:
                    current_input = result.modified_value
                    
            except Exception as e:
                logger.error(f"Hook pre_execute error: {e}")
                continue
        
        return action, current_input, None
    
    async def run_post_hooks(
        self, 
        action: str, 
        action_input: str,
        observation: str, 
        state: AgentState
    ) -> str:
        """
        运行所有 post-execute hooks
        
        Returns:
            处理后的 observation
        """
        current_observation = observation
        
        for hook in self.hooks:
            try:
                result = await hook.post_execute(action, action_input, current_observation, state)
                
                if result.action == HookAction.MODIFY and result.modified_value:
                    current_observation = result.modified_value
                    
            except Exception as e:
                logger.error(f"Hook post_execute error: {e}")
                continue
        
        return current_observation


def create_default_hook_manager() -> HookManager:
    """创建默认的 Hook 管理器"""
    manager = HookManager()
    manager.register(QuerySanitizationHook())
    manager.register(ResultValidationHook())
    manager.register(LoopDetectionHook())
    return manager
