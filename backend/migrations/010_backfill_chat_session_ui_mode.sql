UPDATE chat_sessions
SET config = COALESCE(config, '{}'::jsonb) || '{"uiMode":"normal"}'::jsonb
WHERE config IS NULL
   OR NOT (COALESCE(config, '{}'::jsonb) ? 'uiMode');
