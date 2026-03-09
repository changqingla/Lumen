"""
上下文注入器

实现时间窗口注入策略，根据不同的处理阶段注入相应的历史对话
"""

from typing import List, Optional
from context.models import Message, MessageType
from context.session_storage import SessionStorage
from config import get_settings
from src.utils.logger import get_logger

logger = get_logger(__name__)


class ContextInjector:
    """上下文注入器 - 实现时间窗口注入策略"""
    
    def __init__(self, storage: Optional[SessionStorage] = None):
        """
        初始化上下文注入器
        
        Args:
            storage: 会话存储实例，如果为None则创建新实例
        """
        self.storage = storage or SessionStorage()
        self.injection_strategy = get_settings().injection_strategy
    
    def inject_for_intent_recognition(self, session_id: str) -> List[Message]:
        """
        为意图识别阶段注入上下文
        
        注入策略: 最近2轮对话（4条消息）
        
        Args:
            session_id: 会话ID
            
        Returns:
            注入的消息列表
        """
        turn_count = self.injection_strategy["intent_recognition"]["turn_count"]
        include_compression = self.injection_strategy["intent_recognition"]["include_compression"]
        
        messages = self._get_recent_turns(session_id, turn_count, include_compression)
        
        logger.debug(
            f"Injected for intent_recognition: session={session_id}, "
            f"turns={turn_count}, messages={len(messages)}"
        )
        
        return messages
    
    def inject_for_planning(self, session_id: str) -> List[Message]:
        """
        为执行规划阶段注入上下文
        
        注入策略: 最近2轮对话（4条消息）
        
        Args:
            session_id: 会话ID
            
        Returns:
            注入的消息列表
        """
        turn_count = self.injection_strategy["planning"]["turn_count"]
        include_compression = self.injection_strategy["planning"]["include_compression"]
        
        messages = self._get_recent_turns(session_id, turn_count, include_compression)
        
        logger.debug(
            f"Injected for planning: session={session_id}, "
            f"turns={turn_count}, messages={len(messages)}"
        )
        
        return messages
    
    def inject_for_answer_generation(self, session_id: str) -> List[Message]:
        """
        为答案生成阶段注入上下文
        
        注入策略: 最近3轮对话（6条消息）
        
        Args:
            session_id: 会话ID
            
        Returns:
            注入的消息列表
        """
        turn_count = self.injection_strategy["answer_generation"]["turn_count"]
        include_compression = self.injection_strategy["answer_generation"]["include_compression"]
        
        messages = self._get_recent_turns(session_id, turn_count, include_compression)
        
        logger.debug(
            f"Injected for answer_generation: session={session_id}, "
            f"turns={turn_count}, messages={len(messages)}"
        )
        
        return messages
    
    def inject_for_execution(self, session_id: str) -> List[Message]:
        """
        为执行阶段注入上下文
        
        注入策略: 不注入历史对话（返回空列表）
        
        Args:
            session_id: 会话ID
            
        Returns:
            空列表
        """
        logger.debug(f"No injection for execution: session={session_id}")
        return []
    
    def inject_for_simple_interaction(self, session_id: str) -> List[Message]:
        """
        为简单对话交互注入上下文
        
        注入策略: 注入所有活跃消息（包括压缩摘要）
        
        理由：简单对话可能涉及"你刚才说了什么"、"总结一下我们的讨论"等问题，
              需要访问完整的对话历史。
        
        安全性：
        - 压缩算法确保活跃消息始终在安全范围内（压缩摘要 + 30%最近内容 ≈ 34K tokens）
        - 正常情况下远小于模型上下文限制（128K）
        - 不需要二次截断，如果发现异常大小会记录警告
        
        Args:
            session_id: 会话ID
            
        Returns:
            所有活跃消息列表
        """
        messages = self._get_all_active_messages(session_id)
        
        # 计算总token数（用于监控和日志）
        total_tokens = self.calculate_injection_tokens(messages)
        
        logger.debug(
            f"Injected for simple_interaction: session={session_id}, "
            f"messages={len(messages)}, tokens={total_tokens}"
        )
        
        return messages
    
    def _get_all_active_messages(self, session_id: str) -> List[Message]:
        """
        获取所有活跃消息（包括压缩摘要，但不包括已被压缩的原始消息）
        
        Args:
            session_id: 会话ID
            
        Returns:
            所有活跃消息列表
        """
        all_messages = self.storage.get_messages(
            session_id,
            include_compressed=False  # 不包含已被压缩的原始消息
        )
        return all_messages if all_messages else []
    
    def _get_recent_turns(
        self,
        session_id: str,
        turn_count: int,
        include_compression: bool = True
    ) -> List[Message]:
        """
        获取最近N轮对话
        
        1轮对话 = User消息 + Assistant消息（2条消息）
        
        策略：
        - 如果 include_compression=True，始终保留压缩摘要（如果有）
        - 然后从非压缩消息中取最近N轮
        
        Args:
            session_id: 会话ID
            turn_count: 轮数
            include_compression: 是否包含压缩摘要
            
        Returns:
            消息列表（压缩摘要 + 最近N轮）
        """
        if turn_count == 0:
            return []
        
        # 获取所有活跃消息（包括压缩摘要）
        all_messages = self.storage.get_messages(
            session_id,
            include_compressed=False  # 不包含已被压缩的原始消息
        )
        
        if not all_messages:
            return []
        
        # 分离压缩摘要和普通消息
        compression_summary = None
        regular_messages = []
        
        for msg in all_messages:
            if msg.message_type == MessageType.COMPRESSION:
                compression_summary = msg
            else:
                regular_messages.append(msg)
        
        # 计算需要获取的普通消息数量：turn_count * 2
        message_count = turn_count * 2
        
        # 获取最后N条普通消息
        recent_regular = regular_messages[-message_count:] if len(regular_messages) > message_count else regular_messages
        
        # 组装结果：压缩摘要（如果有且需要） + 最近的普通消息
        result = []
        if include_compression and compression_summary:
            result.append(compression_summary)
        result.extend(recent_regular)
        
        return result
    
    def calculate_injection_tokens(self, messages: List[Message]) -> int:
        """
        计算注入消息的总token数
        
        Args:
            messages: 消息列表
            
        Returns:
            总token数
        """
        return sum(msg.token_count for msg in messages)
    
    def format_messages_for_prompt(self, messages: List[Message]) -> str:
        """
        将消息格式化为Prompt字符串
        
        Args:
            messages: 消息列表
            
        Returns:
            格式化后的字符串
        """
        if not messages:
            return ""
        
        formatted = "## 对话历史\n\n"
        
        for msg in messages:
            if msg.message_type == MessageType.COMPRESSION:
                formatted += f"[历史摘要]\n{msg.content}\n\n"
            elif msg.message_type == MessageType.USER:
                formatted += f"用户: {msg.content}\n\n"
            elif msg.message_type == MessageType.ASSISTANT:
                formatted += f"助手: {msg.content}\n\n"
        
        return formatted.strip()

