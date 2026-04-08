-- 为用户增加模型配额覆盖字段（token）
ALTER TABLE users
ADD COLUMN IF NOT EXISTS model_quota_limit BIGINT;

COMMENT ON COLUMN users.model_quota_limit IS '用户模型 token 配额覆盖值（为空时按 user_level/is_admin 规则）';

-- 将现有管理员用户提额到 1 亿 token
UPDATE users
SET model_quota_limit = 100000000
WHERE is_admin = TRUE
  AND (model_quota_limit IS NULL OR model_quota_limit <> 100000000);
