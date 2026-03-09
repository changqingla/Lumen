"""
会话存储层

封装 Redis 和 PostgreSQL 的数据访问操作
实现缓存策略：Redis作为一级缓存，PostgreSQL作为持久化存储
"""

import json
import redis
from psycopg2 import pool
from typing import List, Optional
from datetime import datetime

from context.models import Session, Message, CompressionRecord, MessageType, SessionStatus
from config import get_settings
from src.utils.logger import get_logger

logger = get_logger(__name__)


class SessionStorage:
    """会话存储层 - 数据访问封装"""
    
    def __init__(self):
        """初始化存储连接"""
        settings = get_settings()
        
        # Redis连接（支持ACL用户认证）
        redis_kwargs = {
            'host': settings.redis_host,
            'port': settings.redis_port,
            'db': settings.redis_db,
            'socket_timeout': settings.redis_socket_timeout,
            'socket_connect_timeout': settings.redis_socket_connect_timeout,
            'decode_responses': True
        }
        
        # 如果有密码，添加认证参数
        if settings.redis_password:
            redis_kwargs['password'] = settings.redis_password
            # 如果配置了用户名，使用ACL认证
            if settings.redis_username:
                redis_kwargs['username'] = settings.redis_username
        
        self.redis_client = redis.Redis(**redis_kwargs)
        
        # PostgreSQL连接池（使用线程安全版本）
        self.pg_pool = pool.ThreadedConnectionPool(
            1,  # minconn
            settings.postgres_pool_size,  # maxconn
            host=settings.postgres_host,
            port=settings.postgres_port,
            database=settings.postgres_db,
            user=settings.postgres_user,
            password=settings.postgres_password
        )
        
        # 保存settings引用（用于其他方法）
        self.settings = settings
        
        logger.info("SessionStorage initialized successfully")
    
    def __del__(self):
        """清理资源"""
        if hasattr(self, 'pg_pool') and self.pg_pool:
            self.pg_pool.closeall()
            logger.info("PostgreSQL connection pool closed")
    
    # ========================================================================
    # Session 操作
    # ========================================================================
    
    def create_session(self, session: Session) -> None:
        """创建会话"""
        # 写入PostgreSQL
        conn = self.pg_pool.getconn()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO agent_sessions
                    (session_id, user_id, created_at, updated_at, total_token_count,
                     message_count, compression_count, status, metadata)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        session.session_id,
                        session.user_id,
                        session.created_at,
                        session.updated_at,
                        session.total_token_count,
                        session.message_count,
                        session.compression_count,
                        session.status.value,
                        json.dumps(session.metadata)
                    )
                )
            conn.commit()
            logger.info(f"Session created in PostgreSQL: {session.session_id}")
        finally:
            self.pg_pool.putconn(conn)
        
        # 写入Redis缓存
        if self.settings.enable_cache:
            self._cache_session(session)
    
    def get_session(self, session_id: str) -> Optional[Session]:
        """获取会话"""
        # 先尝试从Redis读取
        if self.settings.enable_cache:
            cached = self._get_cached_session(session_id)
            if cached:
                logger.debug(f"Session cache hit: {session_id}")
                return cached
        
        # Redis未命中，从PostgreSQL读取
        conn = self.pg_pool.getconn()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT session_id, user_id, created_at, updated_at, total_token_count,
                           message_count, compression_count, status, metadata
                    FROM agent_sessions
                    WHERE session_id = %s
                    """,
                    (session_id,)
                )
                row = cursor.fetchone()
                
                if row:
                    session = Session(
                        session_id=row[0],
                        user_id=row[1],
                        created_at=row[2],
                        updated_at=row[3],
                        total_token_count=row[4],
                        message_count=row[5],
                        compression_count=row[6],
                        status=SessionStatus(row[7]),
                        metadata=row[8] if row[8] else {}
                    )
                    
                    # 写入缓存
                    if self.settings.enable_cache:
                        self._cache_session(session)
                    
                    logger.debug(f"Session loaded from PostgreSQL: {session_id}")
                    return session
                
                return None
        finally:
            self.pg_pool.putconn(conn)
    
    def update_session_stats(
        self,
        session_id: str,
        total_tokens: int,
        message_count: int
    ) -> None:
        """更新会话统计信息"""
        conn = self.pg_pool.getconn()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE agent_sessions
                    SET total_token_count = %s,
                        message_count = %s,
                        updated_at = %s
                    WHERE session_id = %s
                    """,
                    (total_tokens, message_count, datetime.now(), session_id)
                )
            conn.commit()
            logger.debug(f"Session stats updated: {session_id}, tokens={total_tokens}, messages={message_count}")
        finally:
            self.pg_pool.putconn(conn)
        
        # 使缓存失效
        if self.settings.enable_cache:
            self._invalidate_cache(session_id)
    
    def increment_compression_count(self, session_id: str) -> None:
        """增加压缩次数"""
        conn = self.pg_pool.getconn()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE agent_sessions
                    SET compression_count = compression_count + 1,
                        updated_at = %s
                    WHERE session_id = %s
                    """,
                    (datetime.now(), session_id)
                )
            conn.commit()
            logger.debug(f"Compression count incremented for session: {session_id}")
        finally:
            self.pg_pool.putconn(conn)
        
        # 使缓存失效
        if self.settings.enable_cache:
            self._invalidate_cache(session_id)
    
    # ========================================================================
    # Message 操作
    # ========================================================================
    
    def get_next_sequence_number(self, session_id: str) -> int:
        """
        获取会话的下一个消息序号
        
        Args:
            session_id: 会话ID
            
        Returns:
            下一个序号
        """
        conn = self.pg_pool.getconn()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT COALESCE(MAX(sequence_number), -1) + 1 FROM agent_messages WHERE session_id = %s",
                    (session_id,)
                )
                result = cursor.fetchone()
                return result[0] if result else 0
        finally:
            self.pg_pool.putconn(conn)
    
    def add_message(self, message: Message) -> None:
        """
        添加消息
        
        注意：如果message.sequence_number为None或负数，会在事务中自动分配下一个序号。
        这样可以保证并发安全（在同一事务中获取和插入）。
        """
        conn = self.pg_pool.getconn()
        try:
            with conn.cursor() as cursor:
                # 如果sequence_number未设置，在事务中自动分配
                seq_num = message.sequence_number
                if seq_num is None or seq_num < 0:
                    cursor.execute(
                        "SELECT COALESCE(MAX(sequence_number), -1) + 1 FROM agent_messages WHERE session_id = %s FOR UPDATE",
                        (message.session_id,)
                    )
                    result = cursor.fetchone()
                    seq_num = result[0] if result else 0
                    logger.debug(f"Auto-assigned sequence_number={seq_num} for message {message.message_id}")
                
                cursor.execute(
                    """
                    INSERT INTO agent_messages
                    (message_id, session_id, role, content, message_type, token_count,
                     created_at, is_compressed, compression_id, sequence_number, metadata)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        message.message_id,
                        message.session_id,
                        message.role,
                        message.content,
                        message.message_type.value,
                        message.token_count,
                        message.created_at,
                        message.is_compressed,
                        message.compression_id,
                        seq_num,  # 使用计算出的序号
                        json.dumps(message.metadata)
                    )
                )
            conn.commit()
            logger.debug(f"Message added: {message.message_id}, type={message.message_type.value}, seq={seq_num}")
        finally:
            self.pg_pool.putconn(conn)
        
        # 使消息缓存失效
        if self.settings.enable_cache:
            self._invalidate_message_cache(message.session_id)
    
    def get_messages(
        self,
        session_id: str,
        limit: Optional[int] = None,
        include_compressed: bool = False
    ) -> List[Message]:
        """获取消息列表"""
        # 尝试从Redis读取
        cache_key = f"messages:{session_id}:all" if include_compressed else f"messages:{session_id}:active"
        
        if self.settings.enable_cache:
            cached = self._get_cached_messages(cache_key)
            if cached:
                logger.debug(f"Messages cache hit: {session_id}")
                return cached[:limit] if limit else cached
        
        # 从PostgreSQL读取
        conn = self.pg_pool.getconn()
        try:
            with conn.cursor() as cursor:
                if include_compressed:
                    query = """
                        SELECT message_id, session_id, role, content, message_type, token_count,
                               created_at, is_compressed, compression_id, sequence_number, metadata
                        FROM agent_messages
                        WHERE session_id = %s
                        ORDER BY sequence_number ASC
                    """
                else:
                    query = """
                        SELECT message_id, session_id, role, content, message_type, token_count,
                               created_at, is_compressed, compression_id, sequence_number, metadata
                        FROM agent_messages
                        WHERE session_id = %s AND (is_compressed = FALSE OR message_type = 'compression')
                        ORDER BY sequence_number ASC
                    """
                
                if limit:
                    query += f" LIMIT {limit}"
                
                cursor.execute(query, (session_id,))
                rows = cursor.fetchall()
                
                messages = []
                for row in rows:
                    message = Message(
                        message_id=row[0],
                        session_id=row[1],
                        role=row[2],
                        content=row[3],
                        message_type=MessageType(row[4]),
                        token_count=row[5],
                        created_at=row[6],
                        is_compressed=row[7],
                        compression_id=row[8],
                        sequence_number=row[9],
                        metadata=row[10] if row[10] else {}
                    )
                    messages.append(message)
                
                # 写入缓存
                if self.settings.enable_cache and not limit:
                    self._cache_messages(cache_key, messages)
                
                logger.debug(f"Loaded {len(messages)} messages from PostgreSQL: {session_id}")
                return messages
        finally:
            self.pg_pool.putconn(conn)
    
    def get_recent_messages(self, session_id: str, count: int) -> List[Message]:
        """获取最近的N条消息"""
        conn = self.pg_pool.getconn()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT message_id, session_id, role, content, message_type, token_count,
                           created_at, is_compressed, compression_id, sequence_number, metadata
                    FROM agent_messages
                    WHERE session_id = %s AND (is_compressed = FALSE OR message_type = 'compression')
                    ORDER BY sequence_number DESC
                    LIMIT %s
                    """,
                    (session_id, count)
                )
                rows = cursor.fetchall()
                
                messages = []
                for row in reversed(rows):  # 反转以保持时间顺序
                    message = Message(
                        message_id=row[0],
                        session_id=row[1],
                        role=row[2],
                        content=row[3],
                        message_type=MessageType(row[4]),
                        token_count=row[5],
                        created_at=row[6],
                        is_compressed=row[7],
                        compression_id=row[8],
                        sequence_number=row[9],
                        metadata=row[10] if row[10] else {}
                    )
                    messages.append(message)
                
                logger.debug(f"Loaded {len(messages)} recent messages: {session_id}")
                return messages
        finally:
            self.pg_pool.putconn(conn)
    
    def mark_messages_compressed(
        self,
        message_ids: List[str],
        compression_id: str
    ) -> None:
        """标记消息为已压缩"""
        conn = self.pg_pool.getconn()
        try:
            with conn.cursor() as cursor:
                # 获取session_id用于缓存失效
                cursor.execute(
                    "SELECT DISTINCT session_id FROM agent_messages WHERE message_id = ANY(%s)",
                    (message_ids,)
                )
                session_ids = [row[0] for row in cursor.fetchall()]

                # 标记为已压缩
                cursor.execute(
                    """
                    UPDATE agent_messages
                    SET is_compressed = TRUE,
                        compression_id = %s
                    WHERE message_id = ANY(%s)
                    """,
                    (compression_id, message_ids)
                )
            conn.commit()
            logger.info(f"Marked {len(message_ids)} messages as compressed")
            
            # 使消息缓存失效
            if self.settings.enable_cache:
                for session_id in session_ids:
                    self._invalidate_message_cache(session_id)
                    logger.debug(f"Message cache invalidated for session: {session_id}")
        finally:
            self.pg_pool.putconn(conn)
    
    # ========================================================================
    # Compression 操作
    # ========================================================================
    
    def save_compression_record(self, record: CompressionRecord) -> None:
        """保存压缩记录"""
        conn = self.pg_pool.getconn()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO agent_compression_history
                    (compression_id, session_id, round, original_message_count,
                     compressed_token_count, summary_token_count, summary_content,
                     compressed_message_ids, created_at, metadata)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        record.compression_id,
                        record.session_id,
                        record.round,
                        record.original_message_count,
                        record.compressed_token_count,
                        record.summary_token_count,
                        record.summary_content,
                        record.compressed_message_ids,
                        record.created_at,
                        json.dumps(record.metadata)
                    )
                )
            conn.commit()
            logger.info(f"Compression record saved: {record.compression_id}, round={record.round}")
        finally:
            self.pg_pool.putconn(conn)
    
    def get_compression_history(self, session_id: str) -> List[CompressionRecord]:
        """获取压缩历史"""
        conn = self.pg_pool.getconn()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT compression_id, session_id, round, original_message_count,
                           compressed_token_count, summary_token_count, summary_content,
                           compressed_message_ids, created_at, metadata
                    FROM agent_compression_history
                    WHERE session_id = %s
                    ORDER BY round ASC
                    """,
                    (session_id,)
                )
                rows = cursor.fetchall()
                
                records = []
                for row in rows:
                    record = CompressionRecord(
                        compression_id=row[0],
                        session_id=row[1],
                        round=row[2],
                        original_message_count=row[3],
                        compressed_token_count=row[4],
                        summary_token_count=row[5],
                        summary_content=row[6],
                        compressed_message_ids=row[7],
                        created_at=row[8],
                        metadata=row[9] if row[9] else {}
                    )
                    records.append(record)
                
                logger.debug(f"Loaded {len(records)} compression records: {session_id}")
                return records
        finally:
            self.pg_pool.putconn(conn)
    
    # ========================================================================
    # 缓存操作
    # ========================================================================
    
    def _cache_session(self, session: Session) -> None:
        """
        缓存会话
        
        Raises:
            Exception: Redis缓存失败
        """
        key = f"session:{session.session_id}"
        self.redis_client.setex(
            key,
            self.settings.session_cache_ttl,
            json.dumps(session.to_dict())
        )
        logger.debug(f"Session cached: {session.session_id}")
    
    def _get_cached_session(self, session_id: str) -> Optional[Session]:
        """
        从缓存获取会话
        
        Returns:
            Session对象，如果缓存未命中返回None
            
        Raises:
            Exception: Redis读取失败
        """
        key = f"session:{session_id}"
        data = self.redis_client.get(key)
        if data:
            return Session.from_dict(json.loads(data))
        return None
    
    def _cache_messages(self, cache_key: str, messages: List[Message]) -> None:
        """
        缓存消息列表
        
        Raises:
            Exception: Redis缓存失败
        """
        data = json.dumps([msg.to_dict() for msg in messages])
        self.redis_client.setex(
            cache_key,
            self.settings.message_cache_ttl,
            data
        )
        logger.debug(f"Messages cached: {cache_key}")
    
    def _get_cached_messages(self, cache_key: str) -> Optional[List[Message]]:
        """
        从缓存获取消息列表
        
        Returns:
            消息列表，如果缓存未命中返回None
            
        Raises:
            Exception: Redis读取失败
        """
        data = self.redis_client.get(cache_key)
        if data:
            messages_data = json.loads(data)
            return [Message.from_dict(msg_dict) for msg_dict in messages_data]
        return None
    
    def _invalidate_cache(self, session_id: str) -> None:
        """
        使会话缓存失效
        
        Raises:
            Exception: Redis删除失败
        """
        key = f"session:{session_id}"
        self.redis_client.delete(key)
        logger.debug(f"Session cache invalidated: {session_id}")
    
    def _invalidate_message_cache(self, session_id: str) -> None:
        """
        使消息缓存失效（使用 SCAN 替代 KEYS，避免阻塞 Redis）
        """
        pattern = f"messages:{session_id}:*"
        cursor = 0
        keys_to_delete = []
        
        # 使用 SCAN 迭代获取匹配的键
        while True:
            cursor, keys = self.redis_client.scan(
                cursor=cursor,
                match=pattern,
                count=100
            )
            keys_to_delete.extend(keys)
            if cursor == 0:
                break
        
        # 使用 pipeline 批量删除
        if keys_to_delete:
            pipe = self.redis_client.pipeline()
            for key in keys_to_delete:
                pipe.delete(key)
            pipe.execute()
            logger.debug(f"Message cache invalidated: {session_id}, deleted {len(keys_to_delete)} keys")

