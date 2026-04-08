-- Migration: 添加 chat_messages.document_summaries 列
-- Date: 2026-03-14
-- Description: 将历史 init-db 补丁纳入主迁移链路，统一由 run_migrations.py 管理

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_name = 'chat_messages'
          AND column_name = 'document_summaries'
    ) THEN
        ALTER TABLE chat_messages
        ADD COLUMN document_summaries JSONB;

        COMMENT ON COLUMN chat_messages.document_summaries IS
            '文档总结信息，格式: [{"doc_id": "xxx", "doc_name": "xxx.pdf", "summary": "...", "from_cache": true}, ...]';
    END IF;
END $$;
