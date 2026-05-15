import os

os.environ["DEBUG"] = "false"

import pytest

from modules.chat.entities.chat_session import ChatMessage
from modules.chat.message_metadata import build_message_metadata, parse_message_metadata


def test_message_metadata_round_trips_normalized_extension_payload():
    metadata = build_message_metadata(
        document_summaries=[{"doc_id": "doc-1"}],
        image_data_urls=["data:image/png;base64,abc", ""],
        artifacts=[
            {
                "object_path": " artifacts/report.md ",
                "name": " Report ",
                "path": "output/report.md",
                "mime_type": " text/markdown ",
                "size_bytes": "42",
            },
            {"name": "missing object path"},
        ],
        attachments=[
            {
                "attachment_id": " att-1 ",
                "name": " source.pdf ",
                "object_path": "uploads/source.pdf",
                "workspace_path": "input/source.pdf",
                "mime_type": " application/pdf ",
                "available_views": [" original ", "", 7],
                "capabilities": [" vision_read "],
                "size_bytes": "100",
                "metadata": {"parse_status": "ready"},
            }
        ],
        tool_traces=[
            {
                "name": " search ",
                "call_id": " call-1 ",
                "iteration": "2",
                "args": {"q": "hello"},
                "success": True,
                "duration_ms": "12",
            }
        ],
        assistant_tuple_messages=[
            {
                "type": "ai",
                "id": "ai-1",
                "tool_calls": [{"id": "tool-call-1", "name": " search ", "args": {"q": "hello"}}],
            },
            {
                "type": "tool",
                "id": "tool-1",
                "tool_call_id": "tool-call-1",
                "name": "search",
                "content": "[]",
            },
            {"type": "ai", "id": "empty-ai"},
        ],
        truncation_metadata={"was_truncated": True, "truncated_at": " 2026-05-15T00:00:00Z "},
        interruption={"reason": " stopped ", "retryable": False, "interrupted_at": " now "},
    )

    assert metadata == {
        "document_summaries": [{"doc_id": "doc-1"}],
        "image_data_urls": ["data:image/png;base64,abc"],
        "artifacts": [
            {
                "object_path": "artifacts/report.md",
                "name": "Report",
                "path": "output/report.md",
                "mime_type": "text/markdown",
                "size_bytes": 42,
            }
        ],
        "attachments": [
            {
                "attachment_id": "att-1",
                "name": "source.pdf",
                "object_path": "uploads/source.pdf",
                "workspace_path": "input/source.pdf",
                "mime_type": "application/pdf",
                "available_views": ["original"],
                "capabilities": ["vision_read"],
                "size_bytes": 100,
                "metadata": {"parse_status": "ready"},
            }
        ],
        "tool_traces": [
            {
                "name": "search",
                "call_id": "call-1",
                "iteration": 2,
                "args": {"q": "hello"},
                "success": True,
                "duration_ms": 12,
            }
        ],
        "assistant_tuple_messages": [
            {
                "type": "ai",
                "id": "ai-1",
                "tool_calls": [{"name": "search", "id": "tool-call-1", "args": {"q": "hello"}}],
            },
            {
                "type": "tool",
                "id": "tool-1",
                "content": "[]",
                "tool_call_id": "tool-call-1",
                "name": "search",
            },
        ],
        "truncation": {"was_truncated": True, "truncated_at": "2026-05-15T00:00:00Z"},
        "interruption": {"reason": "stopped", "retryable": False, "interrupted_at": "now"},
    }

    assert parse_message_metadata(metadata) == ChatMessage._parse_message_metadata(metadata)


def test_message_metadata_uses_canonical_object_for_document_summaries():
    document_summaries = [{"doc_id": "doc-1"}]

    metadata = build_message_metadata(document_summaries=document_summaries)

    assert metadata == {
        "document_summaries": document_summaries,
        "image_data_urls": [],
        "artifacts": [],
        "attachments": [],
        "tool_traces": [],
        "assistant_tuple_messages": [],
        "truncation": None,
        "interruption": None,
    }
    assert parse_message_metadata(metadata) == (
        document_summaries,
        [],
        None,
        [],
        [],
        [],
        [],
        None,
    )


def test_message_metadata_rejects_non_object_payloads():
    with pytest.raises(ValueError, match="JSON object"):
        parse_message_metadata([{"doc_id": "doc-1"}])
