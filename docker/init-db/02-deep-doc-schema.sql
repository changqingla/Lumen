-- ============================================================================
-- Deep Doc Agent 上下文管理系统数据库表结构
-- 使用 reader_qaq 数据库，与 Lumen 项目共享
-- ============================================================================

-- 1. 会话表（Deep Doc Agent）
CREATE TABLE IF NOT EXISTS agent_sessions (
    session_id VARCHAR(255) PRIMARY KEY,
    user_id VARCHAR(255) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    total_token_count INTEGER NOT NULL DEFAULT 0,
    message_count INTEGER NOT NULL DEFAULT 0,
    compression_count INTEGER NOT NULL DEFAULT 0,
    status VARCHAR(50) NOT NULL DEFAULT 'active',
    metadata JSONB DEFAULT '{}'::jsonb
);

-- 会话表索引
CREATE INDEX IF NOT EXISTS idx_agent_sessions_user ON agent_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_agent_sessions_status ON agent_sessions(status);
CREATE INDEX IF NOT EXISTS idx_agent_sessions_updated ON agent_sessions(updated_at DESC);

-- 会话表注释
COMMENT ON TABLE agent_sessions IS 'Deep Doc Agent 用户会话表';
COMMENT ON COLUMN agent_sessions.session_id IS '会话唯一标识';
COMMENT ON COLUMN agent_sessions.user_id IS '用户标识';
COMMENT ON COLUMN agent_sessions.total_token_count IS '会话累积token总数';
COMMENT ON COLUMN agent_sessions.compression_count IS '压缩执行次数';


-- 2. 消息表（Deep Doc Agent）
CREATE TABLE IF NOT EXISTS agent_messages (
    message_id VARCHAR(255) PRIMARY KEY,
    session_id VARCHAR(255) NOT NULL REFERENCES agent_sessions(session_id) ON DELETE CASCADE,
    role VARCHAR(50) NOT NULL,
    content TEXT NOT NULL,
    message_type VARCHAR(50) NOT NULL DEFAULT 'user',
    token_count INTEGER NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    is_compressed BOOLEAN NOT NULL DEFAULT FALSE,
    compression_id VARCHAR(255),
    sequence_number INTEGER NOT NULL,
    metadata JSONB DEFAULT '{}'::jsonb
);

-- 消息表索引
CREATE INDEX IF NOT EXISTS idx_agent_messages_session ON agent_messages(session_id, sequence_number);
CREATE INDEX IF NOT EXISTS idx_agent_messages_session_created ON agent_messages(session_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_messages_compressed ON agent_messages(is_compressed) WHERE is_compressed = TRUE;
CREATE INDEX IF NOT EXISTS idx_agent_messages_type ON agent_messages(message_type);

-- 消息表注释
COMMENT ON TABLE agent_messages IS 'Deep Doc Agent 对话消息表';
COMMENT ON COLUMN agent_messages.message_id IS '消息唯一标识';
COMMENT ON COLUMN agent_messages.role IS '消息角色：user/assistant/system';
COMMENT ON COLUMN agent_messages.message_type IS '消息类型：user/assistant/system/compression';
COMMENT ON COLUMN agent_messages.is_compressed IS '是否已被压缩';
COMMENT ON COLUMN agent_messages.compression_id IS '关联的压缩记录ID';
COMMENT ON COLUMN agent_messages.sequence_number IS '消息序号（保证顺序）';


-- 3. 压缩记录表（Deep Doc Agent）
CREATE TABLE IF NOT EXISTS agent_compression_history (
    compression_id VARCHAR(255) PRIMARY KEY,
    session_id VARCHAR(255) NOT NULL REFERENCES agent_sessions(session_id) ON DELETE CASCADE,
    round INTEGER NOT NULL,
    original_message_count INTEGER NOT NULL,
    compressed_token_count INTEGER NOT NULL,
    summary_token_count INTEGER NOT NULL,
    summary_content TEXT NOT NULL,
    compressed_message_ids TEXT[] NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    metadata JSONB DEFAULT '{}'::jsonb
);

-- 压缩记录表索引
CREATE INDEX IF NOT EXISTS idx_agent_compression_session ON agent_compression_history(session_id, round);
CREATE INDEX IF NOT EXISTS idx_agent_compression_created ON agent_compression_history(created_at DESC);

-- 压缩记录表注释
COMMENT ON TABLE agent_compression_history IS 'Deep Doc Agent 上下文压缩历史记录';
COMMENT ON COLUMN agent_compression_history.round IS '压缩轮次';
COMMENT ON COLUMN agent_compression_history.original_message_count IS '原始消息数量';
COMMENT ON COLUMN agent_compression_history.compressed_token_count IS '压缩前的token数';
COMMENT ON COLUMN agent_compression_history.summary_token_count IS '摘要的token数';
COMMENT ON COLUMN agent_compression_history.compressed_message_ids IS '被压缩的消息ID列表';


-- 4. 创建更新时间触发器函数（Deep Doc Agent）
CREATE OR REPLACE FUNCTION update_agent_session_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ language 'plpgsql';

-- 为 agent_sessions 表创建更新触发器
DROP TRIGGER IF EXISTS update_agent_sessions_updated_at ON agent_sessions;
CREATE TRIGGER update_agent_sessions_updated_at
    BEFORE UPDATE ON agent_sessions
    FOR EACH ROW
    EXECUTE FUNCTION update_agent_session_updated_at();


-- 5. 创建视图：会话统计（Deep Doc Agent）
CREATE OR REPLACE VIEW agent_session_statistics AS
SELECT 
    s.session_id,
    s.user_id,
    s.total_token_count,
    s.message_count,
    s.compression_count,
    COUNT(m.message_id) as actual_message_count,
    SUM(CASE WHEN m.is_compressed THEN 1 ELSE 0 END) as compressed_message_count,
    MAX(m.created_at) as last_message_at,
    s.created_at,
    s.updated_at,
    s.status
FROM agent_sessions s
LEFT JOIN agent_messages m ON s.session_id = m.session_id
GROUP BY s.session_id, s.user_id, s.total_token_count, s.message_count, 
         s.compression_count, s.created_at, s.updated_at, s.status;

COMMENT ON VIEW agent_session_statistics IS 'Deep Doc Agent 会话统计视图';


-- 6. 创建视图：压缩效果统计（Deep Doc Agent）
CREATE OR REPLACE VIEW agent_compression_statistics AS
SELECT 
    session_id,
    COUNT(*) as compression_count,
    SUM(original_message_count) as total_compressed_messages,
    SUM(compressed_token_count) as total_compressed_tokens,
    SUM(summary_token_count) as total_summary_tokens,
    SUM(compressed_token_count - summary_token_count) as total_saved_tokens,
    ROUND(CAST(AVG(1.0 - (summary_token_count::numeric / NULLIF(compressed_token_count::numeric, 0))) * 100 AS numeric), 2) as avg_compression_ratio,
    MIN(created_at) as first_compression_at,
    MAX(created_at) as last_compression_at
FROM agent_compression_history
GROUP BY session_id;

COMMENT ON VIEW agent_compression_statistics IS 'Deep Doc Agent 压缩效果统计视图';
