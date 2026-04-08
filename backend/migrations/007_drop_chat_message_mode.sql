-- Migration: 删除 chat_messages.mode 列
-- Date: 2026-03-05
-- Description: 移除已废弃的 deep/search 模式字段，统一单模式链路

ALTER TABLE chat_messages
DROP COLUMN IF EXISTS mode;
