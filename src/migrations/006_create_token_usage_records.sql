-- Migration: 创建 Token 使用记录表
-- Date: 2026-01-05
-- Description: 用于记录每次 LLM API 调用的 token 消耗

-- 确保 uuid-ossp 扩展已启用
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- 创建 token 使用记录表
CREATE TABLE IF NOT EXISTS token_usage_records (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL,
    session_id VARCHAR(255),
    model_name VARCHAR(100) NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    request_type VARCHAR(50),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL
);

-- 创建索引优化查询性能
CREATE INDEX IF NOT EXISTS idx_token_usage_user_created ON token_usage_records(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_token_usage_created ON token_usage_records(created_at DESC);

-- 添加注释
COMMENT ON TABLE token_usage_records IS 'Token 使用记录表，用于追踪 LLM API 消耗';
COMMENT ON COLUMN token_usage_records.user_id IS '用户ID';
COMMENT ON COLUMN token_usage_records.session_id IS '会话ID';
COMMENT ON COLUMN token_usage_records.model_name IS '模型名称';
COMMENT ON COLUMN token_usage_records.input_tokens IS '输入 token 数';
COMMENT ON COLUMN token_usage_records.output_tokens IS '输出 token 数';
COMMENT ON COLUMN token_usage_records.total_tokens IS '总 token 数';
COMMENT ON COLUMN token_usage_records.request_type IS '请求类型：chat/summary/comparison 等';
COMMENT ON COLUMN token_usage_records.created_at IS '创建时间（带时区）';
