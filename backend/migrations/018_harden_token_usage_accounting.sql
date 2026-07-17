-- Make Runtime usage events durable, idempotent, and attributable to one billing window.

ALTER TABLE token_usage_records
ADD COLUMN IF NOT EXISTS event_id UUID,
ADD COLUMN IF NOT EXISTS reservation_id UUID,
ADD COLUMN IF NOT EXISTS usage_source VARCHAR(32),
ADD COLUMN IF NOT EXISTS occurred_at TIMESTAMP WITH TIME ZONE,
ADD COLUMN IF NOT EXISTS billing_window_start TIMESTAMP WITH TIME ZONE;

UPDATE token_usage_records
SET event_id = COALESCE(event_id, uuid_generate_v4()),
    reservation_id = COALESCE(reservation_id, uuid_generate_v4()),
    usage_source = COALESCE(usage_source, 'legacy'),
    occurred_at = COALESCE(occurred_at, created_at),
    billing_window_start = COALESCE(
        billing_window_start,
        date_trunc('month', created_at AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'
    )
WHERE event_id IS NULL
   OR reservation_id IS NULL
   OR usage_source IS NULL
   OR occurred_at IS NULL
   OR billing_window_start IS NULL;

ALTER TABLE token_usage_records
ALTER COLUMN event_id SET NOT NULL,
ALTER COLUMN reservation_id SET NOT NULL,
ALTER COLUMN usage_source SET NOT NULL,
ALTER COLUMN occurred_at SET NOT NULL,
ALTER COLUMN billing_window_start SET NOT NULL,
ALTER COLUMN input_tokens TYPE BIGINT,
ALTER COLUMN output_tokens TYPE BIGINT,
ALTER COLUMN total_tokens TYPE BIGINT,
ALTER COLUMN request_type TYPE VARCHAR(64),
ALTER COLUMN model_name TYPE VARCHAR(255);

UPDATE token_usage_records
SET request_type = 'legacy'
WHERE request_type IS NULL;

ALTER TABLE token_usage_records
ALTER COLUMN request_type SET NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_token_usage_event_id
ON token_usage_records(event_id);

CREATE INDEX IF NOT EXISTS idx_token_usage_user_window
ON token_usage_records(user_id, billing_window_start);

COMMENT ON COLUMN token_usage_records.event_id IS 'Runtime-generated idempotency key';
COMMENT ON COLUMN token_usage_records.reservation_id IS 'Quota reservation settled by this event';
COMMENT ON COLUMN token_usage_records.usage_source IS 'usage_metadata, response_metadata, estimated, or legacy';
COMMENT ON COLUMN token_usage_records.occurred_at IS 'Timestamp reported by the trusted Runtime';
COMMENT ON COLUMN token_usage_records.billing_window_start IS 'Canonical UTC calendar-month start fixed at run admission';
