ALTER TABLE user_model_bindings
ADD COLUMN IF NOT EXISTS health_status VARCHAR(32) NOT NULL DEFAULT 'unknown';

ALTER TABLE user_model_bindings
ADD COLUMN IF NOT EXISTS last_health_checked_at TIMESTAMPTZ NULL;

ALTER TABLE user_model_bindings
ADD COLUMN IF NOT EXISTS last_health_latency_ms INTEGER NULL;

ALTER TABLE user_model_bindings
ADD COLUMN IF NOT EXISTS last_health_error TEXT NULL;
