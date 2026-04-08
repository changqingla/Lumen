-- Create user-scoped provider credentials and model bindings

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TABLE IF NOT EXISTS user_model_provider_credentials (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    provider_code VARCHAR(64) NOT NULL,
    api_key_encrypted TEXT NOT NULL,
    api_key_masked VARCHAR(255) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    last_verified_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_user_model_provider_credentials_user_provider UNIQUE (user_id, provider_code)
);

CREATE INDEX IF NOT EXISTS ix_user_model_provider_credentials_user_id
    ON user_model_provider_credentials (user_id);

CREATE INDEX IF NOT EXISTS ix_user_model_provider_credentials_provider_code
    ON user_model_provider_credentials (provider_code);

CREATE TABLE IF NOT EXISTS user_model_bindings (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    provider_credential_id UUID NOT NULL REFERENCES user_model_provider_credentials (id) ON DELETE CASCADE,
    provider_code VARCHAR(64) NOT NULL,
    binding_name VARCHAR(120) NOT NULL,
    provider_model_name VARCHAR(200) NOT NULL,
    display_name VARCHAR(200) NOT NULL,
    description TEXT NULL,
    supports_vision BOOLEAN NOT NULL DEFAULT FALSE,
    supports_thinking BOOLEAN NOT NULL DEFAULT FALSE,
    supports_reasoning_effort BOOLEAN NOT NULL DEFAULT FALSE,
    is_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_user_model_bindings_binding_name UNIQUE (binding_name),
    CONSTRAINT uq_user_model_bindings_user_provider_model UNIQUE (user_id, provider_credential_id, provider_model_name)
);

CREATE INDEX IF NOT EXISTS ix_user_model_bindings_user_id
    ON user_model_bindings (user_id);

CREATE INDEX IF NOT EXISTS ix_user_model_bindings_provider_credential_id
    ON user_model_bindings (provider_credential_id);

CREATE INDEX IF NOT EXISTS ix_user_model_bindings_provider_code
    ON user_model_bindings (provider_code);
