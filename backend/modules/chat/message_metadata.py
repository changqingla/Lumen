"""Shared helpers for chat message extension metadata."""

from typing import Any, Dict, List, Optional, Tuple

ParsedMessageMetadata = Tuple[
    Optional[List[Dict[str, Any]]],
    List[str],
    Optional[Dict[str, Any]],
    List[Dict[str, Any]],
    List[Dict[str, Any]],
    List[Dict[str, Any]],
    List[Dict[str, Any]],
    Optional[Dict[str, Any]],
]


def _empty_metadata() -> ParsedMessageMetadata:
    return None, [], None, [], [], [], [], None


def _clean_string(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _coerce_non_negative_int(value: Any) -> Optional[int]:
    try:
        if value is None:
            return None
        resolved = int(value)
    except (TypeError, ValueError):
        return None
    return resolved if resolved >= 0 else None


def _coerce_positive_int(value: Any) -> Optional[int]:
    resolved = _coerce_non_negative_int(value)
    return resolved if resolved and resolved > 0 else None


def normalize_artifacts(artifacts: Any) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    if not isinstance(artifacts, list):
        return normalized

    for item in artifacts:
        if not isinstance(item, dict):
            continue
        object_path = _clean_string(item.get("object_path"))
        if not object_path:
            continue
        artifact: Dict[str, Any] = {"object_path": object_path}
        for key in ("name", "path", "mime_type", "session_id"):
            value = _clean_string(item.get(key))
            if value:
                artifact[key] = value
        size_bytes = _coerce_non_negative_int(item.get("size_bytes"))
        if size_bytes is not None:
            artifact["size_bytes"] = size_bytes
        normalized.append(artifact)
    return normalized


def normalize_attachments(attachments: Any) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    if not isinstance(attachments, list):
        return normalized

    for item in attachments:
        if not isinstance(item, dict):
            continue
        attachment_id = _clean_string(item.get("attachment_id"))
        name = _clean_string(item.get("name"))
        object_path = _clean_string(item.get("object_path"))
        workspace_path = _clean_string(item.get("workspace_path"))
        if not attachment_id or not name or not object_path or not workspace_path:
            continue

        attachment: Dict[str, Any] = {
            "attachment_id": attachment_id,
            "name": name,
            "object_path": object_path,
            "workspace_path": workspace_path,
        }
        for key in (
            "mime_type",
            "source_kind",
            "role",
            "input_mode",
            "sha256",
            "created_at",
            "parent_attachment_id",
            "view_type",
            "parse_status",
        ):
            value = _clean_string(item.get(key))
            if value:
                attachment[key] = value

        size_bytes = _coerce_non_negative_int(item.get("size_bytes"))
        if size_bytes is not None:
            attachment["size_bytes"] = size_bytes

        for key in ("available_views", "capabilities"):
            value = item.get(key)
            if isinstance(value, list):
                attachment[key] = [
                    normalized_value
                    for raw_value in value
                    if (normalized_value := _clean_string(raw_value))
                ]

        metadata = item.get("metadata")
        if isinstance(metadata, dict):
            attachment["metadata"] = metadata
        normalized.append(attachment)
    return normalized


def normalize_tool_traces(tool_traces: Any) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    if not isinstance(tool_traces, list):
        return normalized

    for item in tool_traces:
        if not isinstance(item, dict):
            continue
        name = _clean_string(item.get("name"))
        if not name:
            continue
        trace: Dict[str, Any] = {"name": name}

        call_id = _clean_string(item.get("call_id"))
        if call_id:
            trace["call_id"] = call_id

        iteration = _coerce_positive_int(item.get("iteration"))
        if iteration is not None:
            trace["iteration"] = iteration

        for key in ("args", "result"):
            if key in item:
                trace[key] = item.get(key)

        success = item.get("success")
        if isinstance(success, bool):
            trace["success"] = success

        for key in ("error", "status"):
            value = _clean_string(item.get(key))
            if value:
                trace[key] = value

        duration_ms = _coerce_non_negative_int(item.get("duration_ms"))
        if duration_ms is not None:
            trace["duration_ms"] = duration_ms
        normalized.append(trace)
    return normalized


def normalize_assistant_tuple_messages(messages: Any) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    if not isinstance(messages, list):
        return normalized

    for item in messages:
        if not isinstance(item, dict):
            continue
        tuple_type = _clean_string(item.get("type"))
        if tuple_type not in {"ai", "tool"}:
            continue
        tuple_id = _clean_string(item.get("id"))
        if not tuple_id:
            continue

        tuple_message: Dict[str, Any] = {
            "type": tuple_type,
            "id": tuple_id,
        }
        if "content" in item and isinstance(item.get("content"), str):
            tuple_message["content"] = item.get("content")

        raw_tool_calls = item.get("tool_calls")
        normalized_tool_calls: List[Dict[str, Any]] = []
        if isinstance(raw_tool_calls, list):
            for raw_call in raw_tool_calls:
                if not isinstance(raw_call, dict):
                    continue
                tool_name = _clean_string(raw_call.get("name"))
                if not tool_name:
                    continue
                tool_call: Dict[str, Any] = {"name": tool_name}
                call_id = _clean_string(raw_call.get("id"))
                if call_id:
                    tool_call["id"] = call_id
                if "args" in raw_call:
                    tool_call["args"] = raw_call.get("args")
                normalized_tool_calls.append(tool_call)
        if normalized_tool_calls:
            tuple_message["tool_calls"] = normalized_tool_calls

        tool_call_id = _clean_string(item.get("tool_call_id"))
        if tool_call_id:
            tuple_message["tool_call_id"] = tool_call_id
        tool_name = _clean_string(item.get("name"))
        if tool_name:
            tuple_message["name"] = tool_name

        if tuple_type == "ai":
            has_content = bool(str(tuple_message.get("content", "")).strip())
            has_tool_calls = bool(tuple_message.get("tool_calls"))
            if not has_content and not has_tool_calls:
                continue
        if tuple_type == "tool" and (
            not tuple_message.get("tool_call_id") or not tuple_message.get("name")
        ):
            continue

        normalized.append(tuple_message)
    return normalized


def normalize_truncation(truncation: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(truncation, dict) or not truncation.get("was_truncated"):
        return None
    normalized: Dict[str, Any] = {"was_truncated": True}
    truncated_at = _clean_string(truncation.get("truncated_at"))
    if truncated_at:
        normalized["truncated_at"] = truncated_at
    return normalized


def normalize_interruption(interruption: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(interruption, dict):
        return None
    reason = _clean_string(interruption.get("reason"))
    if not reason:
        return None
    normalized: Dict[str, Any] = {
        "reason": reason,
        "retryable": bool(interruption.get("retryable", True)),
    }
    interrupted_at = _clean_string(interruption.get("interrupted_at"))
    if interrupted_at:
        normalized["interrupted_at"] = interrupted_at
    return normalized


def parse_message_metadata(raw: Any) -> ParsedMessageMetadata:
    """Parse stored chat message extension metadata."""
    if raw is None:
        return _empty_metadata()

    if isinstance(raw, list):
        return raw, [], None, [], [], [], [], None

    if not isinstance(raw, dict):
        return _empty_metadata()

    doc_summaries = raw.get("document_summaries")
    image_data_urls = raw.get("image_data_urls")
    parsed_doc_summaries = doc_summaries if isinstance(doc_summaries, list) else None
    parsed_image_urls = (
        [str(url) for url in image_data_urls if isinstance(url, str)]
        if isinstance(image_data_urls, list)
        else []
    )

    return (
        parsed_doc_summaries,
        parsed_image_urls,
        normalize_truncation(raw.get("truncation")),
        normalize_artifacts(raw.get("artifacts")),
        normalize_attachments(raw.get("attachments")),
        normalize_tool_traces(raw.get("tool_traces")),
        normalize_assistant_tuple_messages(raw.get("assistant_tuple_messages")),
        normalize_interruption(raw.get("interruption")),
    )


def build_message_metadata(
    document_summaries: Optional[list] = None,
    image_data_urls: Optional[list[str]] = None,
    artifacts: Optional[list[dict]] = None,
    attachments: Optional[list[dict]] = None,
    tool_traces: Optional[list[dict]] = None,
    assistant_tuple_messages: Optional[list[dict]] = None,
    truncation_metadata: Optional[dict] = None,
    interruption: Optional[dict] = None,
) -> Optional[Any]:
    """Build stored chat message extension metadata while preserving legacy shape."""
    normalized_images = [url for url in (image_data_urls or []) if isinstance(url, str) and url.strip()]
    normalized_artifacts = normalize_artifacts(artifacts)
    normalized_attachments = normalize_attachments(attachments)
    normalized_tool_traces = normalize_tool_traces(tool_traces)
    normalized_assistant_tuple_messages = normalize_assistant_tuple_messages(assistant_tuple_messages)
    normalized_truncation = normalize_truncation(truncation_metadata)
    normalized_interruption = normalize_interruption(interruption)

    if (
        normalized_images
        or normalized_artifacts
        or normalized_attachments
        or normalized_tool_traces
        or normalized_assistant_tuple_messages
        or normalized_truncation
        or normalized_interruption
    ):
        return {
            "document_summaries": document_summaries if isinstance(document_summaries, list) else None,
            "image_data_urls": normalized_images,
            "artifacts": normalized_artifacts,
            "attachments": normalized_attachments,
            "tool_traces": normalized_tool_traces,
            "assistant_tuple_messages": normalized_assistant_tuple_messages,
            "truncation": normalized_truncation,
            "interruption": normalized_interruption,
        }
    return document_summaries if isinstance(document_summaries, list) else None
