-- Reader core schema bootstrap.
-- This baseline is intentionally limited to the original core tables so that
-- subsequent numbered migrations remain the single source of truth for later
-- schema evolution.

CREATE TABLE IF NOT EXISTS users (
    id UUID NOT NULL,
    email VARCHAR NOT NULL,
    password_hash VARCHAR NOT NULL,
    name VARCHAR NOT NULL,
    avatar VARCHAR,
    created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    PRIMARY KEY (id)
);

CREATE UNIQUE INDEX IF NOT EXISTS ix_users_email ON users (email);

CREATE TABLE IF NOT EXISTS chat_sessions (
    id UUID NOT NULL,
    user_id UUID NOT NULL,
    title VARCHAR(200) NOT NULL,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    config JSONB,
    PRIMARY KEY (id),
    FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_chat_sessions_user_id ON chat_sessions (user_id);

CREATE TABLE IF NOT EXISTS chat_messages (
    id UUID NOT NULL,
    session_id UUID NOT NULL,
    role VARCHAR(20) NOT NULL,
    content TEXT NOT NULL,
    thinking TEXT,
    mode VARCHAR(20),
    created_at TIMESTAMP NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY (session_id) REFERENCES chat_sessions (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_chat_messages_session_id ON chat_messages (session_id);

CREATE TABLE IF NOT EXISTS knowledge_bases (
    id UUID NOT NULL,
    owner_id UUID NOT NULL,
    name VARCHAR NOT NULL,
    description VARCHAR,
    category VARCHAR(50) NOT NULL,
    is_public BOOLEAN NOT NULL DEFAULT FALSE,
    subscribers_count INTEGER NOT NULL DEFAULT 0,
    view_count INTEGER NOT NULL DEFAULT 0,
    contents_count INTEGER NOT NULL DEFAULT 0,
    avatar VARCHAR,
    created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    last_updated_at TIMESTAMPTZ,
    PRIMARY KEY (id),
    FOREIGN KEY (owner_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_knowledge_bases_category ON knowledge_bases (category);
CREATE INDEX IF NOT EXISTS ix_knowledge_bases_is_public ON knowledge_bases (is_public);
CREATE INDEX IF NOT EXISTS ix_knowledge_bases_owner_id ON knowledge_bases (owner_id);
CREATE INDEX IF NOT EXISTS ix_knowledge_bases_subscribers_count ON knowledge_bases (subscribers_count);

CREATE TABLE IF NOT EXISTS kb_documents (
    id UUID NOT NULL,
    kb_id UUID NOT NULL,
    name VARCHAR NOT NULL,
    size BIGINT NOT NULL DEFAULT 0,
    status VARCHAR NOT NULL DEFAULT 'uploading',
    source VARCHAR NOT NULL,
    file_path VARCHAR,
    markdown_path VARCHAR,
    mineru_task_id VARCHAR,
    parse_task_id VARCHAR,
    chunk_count INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY (kb_id) REFERENCES knowledge_bases (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_kb_documents_kb_id ON kb_documents (kb_id);

CREATE TABLE IF NOT EXISTS kb_subscriptions (
    id UUID NOT NULL,
    user_id UUID NOT NULL,
    kb_id UUID NOT NULL,
    subscribed_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    last_viewed_at TIMESTAMPTZ,
    PRIMARY KEY (id),
    CONSTRAINT uq_user_kb_subscription UNIQUE (user_id, kb_id),
    FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
    FOREIGN KEY (kb_id) REFERENCES knowledge_bases (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_kb_subscriptions_kb_id ON kb_subscriptions (kb_id);
CREATE INDEX IF NOT EXISTS ix_kb_subscriptions_user_id ON kb_subscriptions (user_id);

CREATE TABLE IF NOT EXISTS favorites (
    id UUID NOT NULL,
    user_id UUID NOT NULL,
    item_type VARCHAR(20) NOT NULL,
    item_id UUID NOT NULL,
    source VARCHAR(20),
    created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_user_item_favorite UNIQUE (user_id, item_type, item_id),
    FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_favorites_user_type ON favorites (user_id, item_type);
CREATE INDEX IF NOT EXISTS ix_favorites_user_id ON favorites (user_id);

CREATE TABLE IF NOT EXISTS note_folders (
    id UUID NOT NULL,
    user_id UUID NOT NULL,
    name VARCHAR NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_user_folder_name UNIQUE (user_id, name),
    FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS notes (
    id UUID NOT NULL,
    user_id UUID NOT NULL,
    folder_id UUID,
    title VARCHAR NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    tags VARCHAR[] DEFAULT '{}' NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
    FOREIGN KEY (folder_id) REFERENCES note_folders (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS ix_notes_folder_id ON notes (folder_id);
CREATE INDEX IF NOT EXISTS ix_notes_user_id ON notes (user_id);
