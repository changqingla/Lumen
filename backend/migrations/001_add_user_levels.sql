-- Migration: 添加用户等级和身份相关字段
-- Date: 2025-12-09
-- Description: 为用户表添加等级、会员到期时间、激活历史等字段

-- 1. 添加用户名唯一约束和索引
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'users_name_unique'
    ) AND to_regclass('public.ix_users_name') IS NULL
      AND to_regclass('public.idx_users_name') IS NULL THEN
        ALTER TABLE users ADD CONSTRAINT users_name_unique UNIQUE (name);
    END IF;
END $$;

DO $$
BEGIN
    IF to_regclass('public.idx_users_name') IS NULL
       AND to_regclass('public.ix_users_name') IS NULL THEN
        IF to_regclass('public.users_name_unique') IS NULL THEN
            CREATE INDEX idx_users_name ON users(name);
        END IF;
    END IF;
END $$;

-- 2. 添加用户等级相关字段
ALTER TABLE users ADD COLUMN IF NOT EXISTS user_level VARCHAR(20) DEFAULT 'basic';
ALTER TABLE users ADD COLUMN IF NOT EXISTS membership_expires_at TIMESTAMP NULL;
ALTER TABLE users ADD COLUMN IF NOT EXISTS activated_codes TEXT[] DEFAULT '{}';
ALTER TABLE users ADD COLUMN IF NOT EXISTS is_admin BOOLEAN DEFAULT FALSE;

-- 3. 添加索引提高查询性能
DO $$
BEGIN
    IF to_regclass('public.idx_users_level') IS NULL
       AND to_regclass('public.ix_users_user_level') IS NULL THEN
        CREATE INDEX idx_users_level ON users(user_level);
    END IF;
END $$;

DO $$
BEGIN
    IF to_regclass('public.idx_users_admin') IS NULL
       AND to_regclass('public.ix_users_is_admin') IS NULL THEN
        CREATE INDEX idx_users_admin ON users(is_admin);
    END IF;
END $$;

DO $$
BEGIN
    IF to_regclass('public.idx_users_membership_expires') IS NULL
       AND to_regclass('public.ix_users_membership_expires_at') IS NULL THEN
        CREATE INDEX idx_users_membership_expires ON users(membership_expires_at);
    END IF;
END $$;

-- 4. 添加注释
COMMENT ON COLUMN users.user_level IS '用户等级: basic(普通用户)/member(会员)/premium(高级会员)';
COMMENT ON COLUMN users.membership_expires_at IS '会员到期时间，NULL表示非会员或永久会员';
COMMENT ON COLUMN users.activated_codes IS '已激活的激活码列表';
COMMENT ON COLUMN users.is_admin IS '是否为系统管理员';
