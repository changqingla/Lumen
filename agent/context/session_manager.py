"""
会话管理器

实现会话管理的业务逻辑，包括会话生命周期、消息管理、压缩判断等
"""

from typing import Optional, List

from context.models import Session, Message
from context.session_storage import SessionStorage
from src.utils.logger import get_logger

logger = get_logger(__name__)


class SessionManager:
    """会话管理器 - 业务逻辑层"""
    
    def __init__(self, storage: Optional[SessionStorage] = None):
        """
        初始化会话管理器
        
        Args:
            storage: 会话存储实例，如果为None则创建新实例
        """
        self.storage = storage or SessionStorage()
    
    # ========================================================================
    # 会话生命周期管理
    # ========================================================================
    
    def create_session(self, user_id: str = "default_user", session_id: Optional[str] = None) -> Session:
        """
        创建新会话
        
        Args:
            user_id: 用户ID
            session_id: 可选的会话ID，如果不提供则自动生成
            
        Returns:
            创建的会话对象
        """
        session = Session.create_new(user_id=user_id, session_id=session_id)
        self.storage.create_session(session)
        
        logger.info(f"New session created: {session.session_id} for user: {user_id}")
        return session
    
    def load_session(self, session_id: str) -> Optional[Session]:
        """
        加载会话
        
        Args:
            session_id: 会话ID
            
        Returns:
            会话对象，如果不存在返回None
        """
        session = self.storage.get_session(session_id)
        
        if session:
            logger.debug(f"Session loaded: {session_id}")
        else:
            logger.warning(f"Session not found: {session_id}")
        
        return session
    
    def get_or_create_session(self, session_id: Optional[str], user_id: str = "default_user") -> Session:
        """
        获取或创建会话
        
        如果提供了session_id:
          - 如果session存在，返回该session
          - 如果session不存在，使用该ID创建新session
        
        如果未提供session_id:
          - 自动生成新ID并创建session
        
        Args:
            session_id: 会话ID，如果为None则自动生成
            user_id: 用户ID
            
        Returns:
            会话对象
        """
        if session_id:
            session = self.load_session(session_id)
            if session:
                return session
            # Session不存在，使用用户提供的ID创建新session
            logger.info(f"Session {session_id} not found, creating new session with provided ID")
            return self.create_session(user_id=user_id, session_id=session_id)
        
        # 未提供session_id，自动生成
        return self.create_session(user_id=user_id)
    
    def close_session(self, session_id: str) -> None:
        """关闭会话（当前仅记录日志）"""
        logger.info(f"会话关闭: {session_id}")
    
    # ========================================================================
    # 内部辅助方法
    # ========================================================================
    
    # ========================================================================
    # 消息管理
    # ========================================================================
    
    def add_user_message(self, session_id: str, content: str, token_count: Optional[int] = None) -> Message:
        """
        添加用户消息
        
        Args:
            session_id: 会话ID
            content: 消息内容
            token_count: API 返回的 token 数，如果为 None 则使用估算 (len/4)
            
        Returns:
            创建的消息对象
        """
        # 如果没有提供 token_count，使用估算并记录警告
        if token_count is None:
            token_count = len(content) // 4 if content else 0
            logger.warning(f"User message token_count not provided, using estimation: {token_count}")
        
        # 创建消息（sequence_number由add_message自动分配，保证并发安全）
        message = Message.create_user_message(
            session_id=session_id,
            content=content,
            token_count=token_count,
            sequence_number=None  # 自动分配
        )
        
        # 保存消息（在事务中自动分配sequence_number）
        self.storage.add_message(message)
        
        # 更新会话统计
        self._update_session_after_message(session_id, token_count)
        
        logger.info(
            f"User message added: session={session_id}, "
            f"tokens={token_count}"
        )
        
        return message
    
    def add_assistant_message(self, session_id: str, content: str, token_count: Optional[int] = None) -> Message:
        """
        添加助手消息
        
        Args:
            session_id: 会话ID
            content: 消息内容
            token_count: API 返回的 token 数，如果为 None 则使用估算 (len/4)
            
        Returns:
            创建的消息对象
        """
        # 如果没有提供 token_count，使用估算并记录警告
        if token_count is None:
            token_count = len(content) // 4 if content else 0
            logger.warning(f"Assistant message token_count not provided, using estimation: {token_count}")
        
        # 创建消息（sequence_number由add_message自动分配，保证并发安全）
        message = Message.create_assistant_message(
            session_id=session_id,
            content=content,
            token_count=token_count,
            sequence_number=None  # 自动分配
        )
        
        # 保存消息（在事务中自动分配sequence_number）
        self.storage.add_message(message)
        
        # 更新会话统计
        self._update_session_after_message(session_id, token_count)
        
        logger.info(
            f"Assistant message added: session={session_id}, "
            f"tokens={token_count}"
        )
        
        return message
    
    def get_conversation_history(
        self,
        session_id: str,
        window_size: Optional[int] = None
    ) -> List[Message]:
        """
        获取对话历史
        
        Args:
            session_id: 会话ID
            window_size: 窗口大小（消息数量），None表示获取全部
            
        Returns:
            消息列表
        """
        messages = self.storage.get_messages(
            session_id=session_id,
            limit=window_size,
            include_compressed=False
        )
        
        logger.debug(
            f"Conversation history retrieved: session={session_id}, "
            f"messages={len(messages)}"
        )
        
        return messages
    
    # ========================================================================
    # Token管理
    # ========================================================================
    
    
    # ========================================================================
    # 压缩管理
    # ========================================================================
    
    def should_compress(self, session_id: str, compression_threshold: int) -> bool:
        """
        检查是否需要压缩
        
        Args:
            session_id: 会话ID
            compression_threshold: 压缩阈值（tokens）
            
        Returns:
            是否需要压缩
        """
        # 直接从session统计获取token数，避免重复计算
        session = self.storage.get_session(session_id)
        
        if not session:
            logger.warning(f"Session not found: {session_id}")
            return False
        
        total_tokens = session.total_token_count
        should_compress = total_tokens > compression_threshold
        
        if should_compress:
            logger.warning(
                f"Compression threshold exceeded: session={session_id}, "
                f"tokens={total_tokens}, threshold={compression_threshold}"
            )
        
        return should_compress
    
    def trigger_compression(self, session_id: str, llm):
        """
        触发压缩
        
        实际的压缩逻辑由CompressionManager实现
        
        Args:
            session_id: 会话ID
            llm: LLM实例，用于生成压缩摘要
            
        Returns:
            压缩记录，如果无法压缩则返回 None
        """
        logger.info(f"Compression triggered for session: {session_id}")
        
        try:
            # 使用CompressionManager执行压缩
            from context.compression_manager import CompressionManager
            compression_manager = CompressionManager(llm=llm, storage=self.storage)
            compression_record = compression_manager.compress_session(session_id)
            
            logger.info(
                f"Compression completed for session: {session_id}, "
                f"saved {compression_record.saved_tokens} tokens"
            )
            
            return compression_record
        
        except ValueError as e:
            # 捕获"无法压缩"的情况（例如只有一条消息）
            if "cannot compress" in str(e).lower():
                logger.info(f"Compression skipped for session {session_id}: {str(e)}")
                return None
            else:
                # 其他 ValueError 继续抛出
                raise
    
    def recalculate_session_stats(self, session_id: str) -> bool:
        """
        重新计算并修复session统计
        
        从数据库实际消息重新计算token数和消息数，用于数据修复和验证。
        
        Args:
            session_id: 会话ID
            
        Returns:
            是否成功修复
        """
        try:
            # 获取所有活跃消息（包括压缩摘要）
            messages = self.storage.get_messages(
                session_id=session_id,
                include_compressed=False
            )
            
            # 重新计算token总数
            actual_token_count = sum(msg.token_count for msg in messages)
            
            # 重新计算用户交互消息数（不包括压缩摘要）
            from context.models import MessageType
            actual_message_count = sum(
                1 for msg in messages 
                if msg.message_type in [MessageType.USER, MessageType.ASSISTANT]
            )
            
            # 更新统计
            self.storage.update_session_stats(
                session_id=session_id,
                total_tokens=actual_token_count,
                message_count=actual_message_count
            )
            
            logger.info(
                f"Session stats recalculated: session={session_id}, "
                f"tokens={actual_token_count}, messages={actual_message_count}"
            )
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to recalculate session stats: {e}", exc_info=True)
            return False
    
    # ========================================================================
    # 私有方法
    # ========================================================================
    
    def _update_session_after_message(self, session_id: str, token_count: int) -> None:
        """
        添加消息后更新会话统计
        
        Args:
            session_id: 会话ID
            token_count: 新增的token数
        """
        # 获取当前会话信息
        session = self.storage.get_session(session_id)
        
        if session:
            # 更新统计
            new_total_tokens = session.total_token_count + token_count
            new_message_count = session.message_count + 1
            
            self.storage.update_session_stats(
                session_id=session_id,
                total_tokens=new_total_tokens,
                message_count=new_message_count
            )
            
            logger.debug(
                f"Session stats updated: session={session_id}, "
                f"total_tokens={new_total_tokens}, message_count={new_message_count}"
            )

