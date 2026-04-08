ALTER TABLE user_model_provider_credentials
ADD COLUMN IF NOT EXISTS custom_base_url TEXT NULL;
