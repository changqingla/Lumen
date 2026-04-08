-- Migration: 扩展知识库表以支持可见性控制
-- Date: 2025-12-09
-- Description: 添加知识库可见性和组织共享功能

-- 1. 添加可见性相关字段
ALTER TABLE knowledge_bases ADD COLUMN IF NOT EXISTS visibility VARCHAR(20) DEFAULT 'private';
ALTER TABLE knowledge_bases ADD COLUMN IF NOT EXISTS shared_to_orgs UUID[] DEFAULT '{}';

-- 2. 创建索引
DO $$
BEGIN
    IF to_regclass('public.idx_kb_visibility') IS NULL
       AND to_regclass('public.ix_knowledge_bases_visibility') IS NULL THEN
        CREATE INDEX idx_kb_visibility ON knowledge_bases(visibility);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_kb_shared_orgs ON knowledge_bases USING GIN(shared_to_orgs);

-- 3. 添加注释
COMMENT ON COLUMN knowledge_bases.visibility IS '可见性：private(私有)/organization(组织可见)/public(全局公开，仅管理员)';
COMMENT ON COLUMN knowledge_bases.shared_to_orgs IS '共享到哪些组织（UUID数组）';
