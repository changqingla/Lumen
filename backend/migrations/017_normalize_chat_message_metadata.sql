UPDATE chat_messages
SET document_summaries = jsonb_build_object(
    'document_summaries', document_summaries,
    'image_data_urls', '[]'::jsonb,
    'artifacts', '[]'::jsonb,
    'attachments', '[]'::jsonb,
    'tool_traces', '[]'::jsonb,
    'assistant_tuple_messages', '[]'::jsonb,
    'truncation', NULL,
    'interruption', NULL
)
WHERE document_summaries IS NOT NULL
  AND jsonb_typeof(document_summaries) = 'array';

COMMENT ON COLUMN chat_messages.document_summaries IS
    '消息扩展信息对象，格式: {"document_summaries": [...], "image_data_urls": [...], "artifacts": [...], "attachments": [...], "tool_traces": [...], "assistant_tuple_messages": [...], "truncation": {...}, "interruption": {...}}';
