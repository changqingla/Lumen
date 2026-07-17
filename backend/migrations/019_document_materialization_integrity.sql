-- Give Runtime knowledge preparation a monotonic database revision and an
-- optional persisted digest for the exact Markdown object.

ALTER TABLE kb_documents
ADD COLUMN IF NOT EXISTS materialization_revision BIGINT NOT NULL DEFAULT 0,
ADD COLUMN IF NOT EXISTS markdown_sha256 VARCHAR(64);

UPDATE kb_documents
SET materialization_revision = 0
WHERE materialization_revision IS NULL;

ALTER TABLE kb_documents
ALTER COLUMN materialization_revision SET DEFAULT 0,
ALTER COLUMN materialization_revision SET NOT NULL;

UPDATE kb_documents
SET materialization_revision = 1
WHERE materialization_revision = 0
  AND markdown_path IS NOT NULL
  AND btrim(markdown_path) <> '';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'ck_kb_documents_materialization_revision_nonnegative'
          AND conrelid = 'kb_documents'::regclass
    ) THEN
        ALTER TABLE kb_documents
        ADD CONSTRAINT ck_kb_documents_materialization_revision_nonnegative
        CHECK (materialization_revision >= 0);
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'ck_kb_documents_markdown_sha256'
          AND conrelid = 'kb_documents'::regclass
    ) THEN
        ALTER TABLE kb_documents
        ADD CONSTRAINT ck_kb_documents_markdown_sha256
        CHECK (markdown_sha256 IS NULL OR markdown_sha256 ~ '^[0-9a-f]{64}$');
    END IF;
END
$$;

COMMENT ON COLUMN kb_documents.materialization_revision IS
'Monotonic revision of the Markdown bytes exposed to Runtime';
COMMENT ON COLUMN kb_documents.markdown_sha256 IS
'Lowercase SHA-256 of the persisted Markdown object when known';
