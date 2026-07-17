"""Paper translation orchestration with a fenced, at-least-once Redis queue.

The task manifest and source PDF are the durable authority. Redis coordinates
delivery through token/generation-bound visibility leases; worker outputs live
under lease-specific directories and become visible only through an atomically
published manifest owned by the same active lease.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import fcntl
import hashlib
import html
import json
import logging
import mimetypes
import os
import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Literal
from urllib.parse import quote, unquote, urlparse
from uuid import UUID, uuid4

import httpx

from config.database import AsyncSessionLocal
from config.settings import settings
from modules.chat.services.insight_runtime_service import (
    InsightRuntimeService,
    insight_runtime_service,
)
from modules.model_config.services.model_config_service import ModelConfigService
from utils.mineru_service import MineruService

logger = logging.getLogger(__name__)

_PDF_LOCAL_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
_PDF_DATA_IMAGE_RE = re.compile(
    r"^data:image/(?:png|jpeg|gif|webp|bmp);base64,",
    re.IGNORECASE,
)
_PDF_MAX_DATA_URL_BYTES = 32 * 1024 * 1024

PaperTranslationStatus = Literal[
    "queued", "converting", "translating", "completed", "failed"
]
QUEUE_PENDING_KEY = "creative_workshop:paper_translation:queue:v2:pending"
QUEUE_PROCESSING_KEY = "creative_workshop:paper_translation:queue:v2:processing"
QUEUE_SCHEDULED_KEY = "creative_workshop:paper_translation:queue:v2:scheduled"
QUEUE_PAYLOADS_KEY = "creative_workshop:paper_translation:queue:v2:payloads"
QUEUE_ATTEMPTS_KEY = "creative_workshop:paper_translation:queue:v2:attempts"
QUEUE_LEASE_TOKENS_KEY = "creative_workshop:paper_translation:queue:v2:lease_tokens"
QUEUE_GENERATIONS_KEY = "creative_workshop:paper_translation:queue:v2:generations"
QUEUE_RECONCILE_CURSOR_KEY = (
    "creative_workshop:paper_translation:queue:v2:reconcile_cursor"
)
WORKER_IDLE_TIMEOUT_SECONDS = 1.0
REDIS_ERROR_BACKOFF_SECONDS = 2.0

_QUEUE_ENQUEUE_SCRIPT = """
local task_id = ARGV[1]
if redis.call('ZSCORE', KEYS[1], task_id)
    or redis.call('ZSCORE', KEYS[2], task_id)
    or redis.call('ZSCORE', KEYS[3], task_id) then
    return 0
end
redis.call('HSET', KEYS[4], task_id, ARGV[2])
redis.call('HSET', KEYS[5], task_id, ARGV[3])
redis.call('ZADD', KEYS[1], tonumber(ARGV[4]), task_id)
return 1
"""

_QUEUE_ACQUIRE_SCRIPT = """
local now = tonumber(ARGV[1])
local lease_until = tonumber(ARGV[2])
local token = ARGV[3]
for _ = 1, 20 do
    local tasks = redis.call('ZRANGEBYSCORE', KEYS[1], '-inf', now, 'LIMIT', 0, 1)
    if #tasks == 0 then
        return {}
    end
    local task_id = tasks[1]
    if redis.call('ZREM', KEYS[1], task_id) == 1 then
        local payload = redis.call('HGET', KEYS[4], task_id)
        if payload then
            local attempt = redis.call('HGET', KEYS[5], task_id) or '1'
            redis.call('ZADD', KEYS[2], lease_until, task_id)
            redis.call('HSET', KEYS[6], task_id, token)
            local redis_time = redis.call('TIME')
            local generation = tonumber(redis_time[1]) * 1000000 + tonumber(redis_time[2])
            local previous_generation = tonumber(redis.call('HGET', KEYS[7], task_id) or '0')
            if generation <= previous_generation then
                generation = previous_generation + 1
            end
            redis.call('HSET', KEYS[7], task_id, generation)
            return {task_id, payload, attempt, generation}
        end
        redis.call('HDEL', KEYS[5], task_id)
    end
end
return {}
"""

_QUEUE_HEARTBEAT_SCRIPT = """
local task_id = ARGV[1]
local token = ARGV[2]
local now = tonumber(ARGV[3])
local lease_until = tonumber(ARGV[4])
if redis.call('HGET', KEYS[6], task_id) ~= token then
    return 0
end
local current_deadline = redis.call('ZSCORE', KEYS[2], task_id)
if not current_deadline or tonumber(current_deadline) < now then
    return 0
end
redis.call('ZADD', KEYS[2], lease_until, task_id)
return 1
"""

_QUEUE_ACK_SCRIPT = """
local task_id = ARGV[1]
local token = ARGV[2]
local now = tonumber(ARGV[3])
if redis.call('HGET', KEYS[6], task_id) ~= token then
    return 0
end
local current_deadline = redis.call('ZSCORE', KEYS[2], task_id)
if not current_deadline or tonumber(current_deadline) < now then
    return 0
end
redis.call('ZREM', KEYS[1], task_id)
redis.call('ZREM', KEYS[2], task_id)
redis.call('ZREM', KEYS[3], task_id)
redis.call('HDEL', KEYS[4], task_id)
redis.call('HDEL', KEYS[5], task_id)
redis.call('HDEL', KEYS[6], task_id)
redis.call('HDEL', KEYS[7], task_id)
return 1
"""

_QUEUE_RELEASE_SCRIPT = """
local task_id = ARGV[1]
local token = ARGV[2]
local now = tonumber(ARGV[3])
if redis.call('HGET', KEYS[6], task_id) ~= token then
    return 0
end
local current_deadline = redis.call('ZSCORE', KEYS[2], task_id)
if not current_deadline or tonumber(current_deadline) < now then
    return 0
end
redis.call('ZREM', KEYS[2], task_id)
redis.call('HDEL', KEYS[6], task_id)
redis.call('ZADD', KEYS[1], now, task_id)
return 1
"""

_QUEUE_RETRY_SCRIPT = """
local task_id = ARGV[1]
local token = ARGV[2]
local now = tonumber(ARGV[3])
local ready_at = tonumber(ARGV[4])
local next_attempt = ARGV[5]
if redis.call('HGET', KEYS[6], task_id) ~= token then
    return 0
end
local current_deadline = redis.call('ZSCORE', KEYS[2], task_id)
if not current_deadline or tonumber(current_deadline) < now then
    return 0
end
redis.call('ZREM', KEYS[2], task_id)
redis.call('HDEL', KEYS[6], task_id)
redis.call('HSET', KEYS[5], task_id, next_attempt)
redis.call('ZADD', KEYS[3], ready_at, task_id)
return 1
"""

_QUEUE_PROMOTE_SCRIPT = """
local now = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
local tasks = redis.call('ZRANGEBYSCORE', KEYS[3], '-inf', now, 'LIMIT', 0, limit)
local promoted = 0
for _, task_id in ipairs(tasks) do
    if redis.call('ZREM', KEYS[3], task_id) == 1 then
        redis.call('ZADD', KEYS[1], now, task_id)
        promoted = promoted + 1
    end
end
return promoted
"""

_QUEUE_RECOVER_SCRIPT = """
local now = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
local tasks = redis.call('ZRANGEBYSCORE', KEYS[2], '-inf', now, 'LIMIT', 0, limit)
local recovered = 0
for _, task_id in ipairs(tasks) do
    if redis.call('ZREM', KEYS[2], task_id) == 1 then
        redis.call('HDEL', KEYS[6], task_id)
        local attempt = tonumber(redis.call('HGET', KEYS[5], task_id) or '1') + 1
        redis.call('HSET', KEYS[5], task_id, attempt)
        redis.call('ZADD', KEYS[1], now, task_id)
        recovered = recovered + 1
    end
end
return recovered
"""

_QUEUE_CANCEL_SCRIPT = """
local task_id = ARGV[1]
local token = ARGV[2]
local now = tonumber(ARGV[3])
if redis.call('HGET', KEYS[6], task_id) ~= token then
    return 0
end
local current_deadline = redis.call('ZSCORE', KEYS[2], task_id)
if not current_deadline or tonumber(current_deadline) < now then
    return 0
end
redis.call('ZREM', KEYS[1], task_id)
redis.call('ZREM', KEYS[2], task_id)
redis.call('ZREM', KEYS[3], task_id)
redis.call('HDEL', KEYS[4], task_id)
redis.call('HDEL', KEYS[5], task_id)
redis.call('HDEL', KEYS[6], task_id)
redis.call('HDEL', KEYS[7], task_id)
return 1
"""


@dataclass
class PaperTranslationSource:
    markdown: str
    assets: dict[str, bytes]


@dataclass
class PaperTranslationTask:
    """Runtime and persisted metadata for a paper translation task."""

    task_id: str
    owner_id: str
    filename: str
    thread_id: str
    status: PaperTranslationStatus
    created_at: str
    updated_at: str
    mineru_batch_id: str | None = None
    source_pdf_path: str | None = None
    source_markdown_path: str | None = None
    translated_markdown_path: str | None = None
    translated_pdf_path: str | None = None
    model_name: str | None = None
    error: str | None = None
    active_lease_token: str | None = None
    lease_generation: int = 0
    terminal_lease_token: str | None = None
    terminal_lease_generation: int | None = None


@dataclass
class PaperTranslationQueueItem:
    owner_id: str
    task_id: str
    filename: str
    source_pdf_path: str
    model_name: str | None = None
    attempt: int = 1


@dataclass(frozen=True)
class PaperTranslationTaskLease:
    item: PaperTranslationQueueItem
    token: str
    generation: int = 0

    @property
    def task_id(self) -> str:
        return self.item.task_id


class PaperTranslationLeaseLost(RuntimeError):
    """Raised when a worker can no longer commit work for its claim."""


@dataclass
class PaperTranslationAgentRunResult:
    last_values_text: str
    tuple_text: str
    artifact_paths: list[str]


class PaperTranslationAgentRecursionError(RuntimeError):
    """Raised when a bounded LangGraph run exhausts its execution budget."""


def _queue_keys() -> list[str]:
    return [
        QUEUE_PENDING_KEY,
        QUEUE_PROCESSING_KEY,
        QUEUE_SCHEDULED_KEY,
        QUEUE_PAYLOADS_KEY,
        QUEUE_ATTEMPTS_KEY,
        QUEUE_LEASE_TOKENS_KEY,
        QUEUE_GENERATIONS_KEY,
    ]


def _as_text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="strict")
    return str(value)


def _parse_queue_item(raw_item: Any) -> PaperTranslationQueueItem:
    raw_text = (
        raw_item.decode("utf-8", errors="strict")
        if isinstance(raw_item, bytes)
        else str(raw_item)
    )
    payload = json.loads(raw_text)
    if not isinstance(payload, dict):
        raise ValueError("Queue item is not an object")
    return PaperTranslationQueueItem(
        owner_id=str(payload["owner_id"]),
        task_id=str(payload["task_id"]),
        filename=str(payload["filename"]),
        source_pdf_path=str(payload["source_pdf_path"]),
        model_name=(str(payload.get("model_name") or "").strip() or None),
        attempt=max(int(payload.get("attempt") or 1), 1),
    )


def _is_uuid_text(value: str) -> bool:
    try:
        UUID(str(value))
    except (TypeError, ValueError):
        return False
    return True


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_filename(filename: str, fallback: str = "paper.pdf") -> str:
    normalized = str(filename or "").replace("\\", "/")
    normalized = Path(normalized).name.strip()
    normalized = re.sub(r"[\x00-\x1f\x7f]+", "", normalized)
    if not normalized or normalized in {".", ".."}:
        return fallback
    return normalized


def _download_filename(source_filename: str, suffix: str) -> str:
    stem = Path(_safe_filename(source_filename)).stem.strip() or "paper-translation"
    return f"{stem}.zh{suffix}"


def _extract_text_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if not isinstance(text, str):
                    text = item.get("content")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(part for part in parts if part)
    return str(content) if content is not None else ""


def _message_type(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if "ai" in normalized or "assistant" in normalized:
        return "ai"
    if "tool" in normalized:
        return "tool"
    if "human" in normalized or normalized == "user":
        return "human"
    return normalized


def _last_ai_text_from_values(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return ""

    last_text = ""
    for message in messages:
        if not isinstance(message, dict):
            continue
        if _message_type(message.get("type")) != "ai":
            continue
        text = _extract_text_content(message.get("content")).strip()
        if text:
            last_text = text
    return last_text


def _ai_text_from_message_payload(payload: Any) -> str:
    message = payload
    if isinstance(payload, list) and payload and isinstance(payload[0], dict):
        message = payload[0]
    if not isinstance(message, dict):
        return ""
    if _message_type(message.get("type")) != "ai":
        return ""
    if message.get("tool_calls"):
        return ""
    return _extract_text_content(message.get("content"))


def _is_graph_recursion_error(payload: Any) -> bool:
    if isinstance(payload, dict):
        error_name = str(payload.get("error") or "").strip().lower()
        message = str(payload.get("message") or "").strip().lower()
    else:
        error_name = ""
        message = str(payload or "").strip().lower()
    return error_name == "graphrecursionerror" or "recursion limit" in message


def _artifact_paths_from_values(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        return []
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, list):
        return []
    paths: list[str] = []
    for artifact in artifacts:
        if isinstance(artifact, str):
            candidate = artifact
        elif isinstance(artifact, dict):
            candidate = str(
                artifact.get("path")
                or artifact.get("object_path")
                or artifact.get("virtual_path")
                or ""
            )
        else:
            continue
        normalized = candidate.strip()
        if normalized:
            paths.append(normalized)
    return paths


def _extract_translated_markdown_path(text: str) -> str:
    content = _strip_markdown_code_fence(text).strip()
    if not content:
        return ""

    candidates = [content]
    match = re.search(r"\{[\s\S]*\}", content)
    if match and match.group(0) != content:
        candidates.append(match.group(0))

    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        path = str(
            payload.get("translated_markdown_path")
            or payload.get("markdown_path")
            or payload.get("path")
            or ""
        ).strip()
        if path:
            return path

    quoted_patterns = [
        r"[`\"'](?P<path>/mnt/user-data/outputs/[^`\"']+?\.md)[`\"']",
        r"<(?P<path>/mnt/user-data/outputs/[^<>]+?\.md)>",
    ]
    for pattern in quoted_patterns:
        path_match = re.search(pattern, content)
        if path_match:
            return path_match.group("path").strip()

    path_match = re.search(r"(/mnt/user-data/outputs/[^\r\n`\"'<>]+?\.md)", content)
    if path_match:
        return path_match.group(1).strip()
    return ""


def _normalize_translated_markdown_artifact_path(path: str) -> str:
    normalized = str(path or "").strip().lstrip("/")
    if not normalized:
        raise ValueError("Translated markdown artifact path is empty")
    if "\\" in normalized or ".." in normalized.split("/"):
        raise ValueError("Translated markdown artifact path is invalid")
    if not normalized.startswith("mnt/user-data/outputs/"):
        raise ValueError(
            "Translated markdown artifact must be under /mnt/user-data/outputs"
        )
    if not normalized.lower().endswith(".md"):
        raise ValueError("Translated markdown artifact must be a Markdown file")
    return f"/{normalized}"


def _normalize_asset_path(path: str) -> str | None:
    normalized = str(path or "").strip().replace("\\", "/").lstrip("/")
    if not normalized:
        return None
    parts = [part for part in normalized.split("/") if part]
    if not parts or any(part in {".", ".."} for part in parts):
        return None
    if len(parts) > 4:
        return None
    if not parts[-1]:
        return None
    return "/".join(parts)


def _find_unescaped(text: str, target: str, start: int) -> int:
    index = start
    while index < len(text):
        if text[index] == "\\":
            index += 2
            continue
        if text[index] == target:
            return index
        index += 1
    return -1


def _parse_markdown_link_destination(
    markdown: str, start: int
) -> tuple[int, int, int] | None:
    index = start
    while index < len(markdown) and markdown[index].isspace():
        index += 1
    if index >= len(markdown):
        return None

    destination_start = index
    if markdown[index] == "<":
        destination_start = index + 1
        destination_end = _find_unescaped(markdown, ">", destination_start)
        if destination_end == -1:
            return None
        close_index = _find_unescaped(markdown, ")", destination_end + 1)
        if close_index == -1:
            return None
        return destination_start, destination_end, close_index

    depth = 0
    while index < len(markdown):
        char = markdown[index]
        if char == "\\":
            index += 2
            continue
        if char == "(":
            depth += 1
            index += 1
            continue
        if char == ")":
            if depth == 0:
                return destination_start, index, index
            depth -= 1
            index += 1
            continue
        if char.isspace() and depth == 0:
            destination_end = index
            close_index = _find_unescaped(markdown, ")", index + 1)
            if close_index == -1:
                return None
            return destination_start, destination_end, close_index
        index += 1
    return None


def _replace_markdown_image_destinations(markdown: str, replace_destination) -> str:
    parts: list[str] = []
    cursor = 0
    index = 0
    while index < len(markdown):
        image_start = markdown.find("![", index)
        if image_start == -1:
            break
        alt_end = _find_unescaped(markdown, "]", image_start + 2)
        if (
            alt_end == -1
            or alt_end + 1 >= len(markdown)
            or markdown[alt_end + 1] != "("
        ):
            index = image_start + 2
            continue
        parsed = _parse_markdown_link_destination(markdown, alt_end + 2)
        if parsed is None:
            index = alt_end + 2
            continue
        destination_start, destination_end, close_index = parsed
        original_destination = markdown[destination_start:destination_end]
        next_destination = replace_destination(original_destination)
        if not next_destination or next_destination == original_destination:
            index = close_index + 1
            continue
        parts.append(markdown[cursor:destination_start])
        parts.append(next_destination)
        cursor = destination_end
        index = close_index + 1

    if not parts:
        return markdown
    parts.append(markdown[cursor:])
    return "".join(parts)


def _strip_markdown_code_fence(markdown: str) -> str:
    text = str(markdown or "").strip()
    match = re.fullmatch(
        r"```(?:markdown|md)?\s*\n(?P<body>[\s\S]*?)\n```", text, re.IGNORECASE
    )
    if match:
        return match.group("body").strip()
    return text


def _remove_mermaid_diagram_blocks(markdown: str) -> str:
    text = str(markdown or "")
    text = re.sub(
        r"\n{0,2}<details\b[^>]*>[\s\S]*?```mermaid\b[\s\S]*?```[\s\S]*?</details>\s*",
        "\n\n",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\n{0,2}```mermaid\b[^\n]*\n[\s\S]*?\n```\s*",
        "\n\n",
        text,
        flags=re.IGNORECASE,
    )
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _markdown_to_html(markdown: str, title: str) -> str:
    try:
        import markdown as markdown_lib
    except ImportError as exc:
        raise RuntimeError(
            "Markdown PDF export requires the 'markdown' package"
        ) from exc

    body = markdown_lib.markdown(
        markdown,
        extensions=["extra", "sane_lists", "tables", "fenced_code", "nl2br"],
        output_format="html5",
    )
    safe_title = html.escape(Path(_safe_filename(title)).stem or "论文翻译")
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>{safe_title}</title>
  <style>
    @page {{ size: A4; margin: 22mm 18mm; }}
    body {{
      color: #111827;
      font-family: "WenQuanYi Micro Hei", "Noto Sans CJK SC", "Source Han Sans SC", "Microsoft YaHei", "PingFang SC", sans-serif;
      font-size: 11.5pt;
      line-height: 1.72;
    }}
    h1, h2, h3, h4, h5, h6 {{
      color: #0f172a;
      line-height: 1.35;
      margin: 1.4em 0 0.65em;
      page-break-after: avoid;
    }}
    h1 {{ font-size: 22pt; }}
    h2 {{ font-size: 17pt; border-bottom: 1px solid #e5e7eb; padding-bottom: 0.25em; }}
    h3 {{ font-size: 14pt; }}
    p {{ margin: 0 0 0.75em; }}
    table {{ width: 100%; border-collapse: collapse; margin: 1em 0; font-size: 9.5pt; }}
    th, td {{ border: 1px solid #d1d5db; padding: 6px 8px; vertical-align: top; }}
    th {{ background: #f3f4f6; font-weight: 700; }}
    pre, code {{
      font-family: "WenQuanYi Micro Hei Mono", "Noto Sans Mono CJK SC", "SFMono-Regular", Consolas, monospace;
      background: #f8fafc;
      border-radius: 4px;
    }}
    pre {{ padding: 10px 12px; overflow-wrap: break-word; white-space: pre-wrap; }}
    code {{ padding: 1px 3px; }}
    blockquote {{ margin: 1em 0; padding-left: 1em; border-left: 3px solid #cbd5e1; color: #475569; }}
    img {{ max-width: 100%; height: auto; }}
    a {{ color: #1d4ed8; text-decoration: none; }}
  </style>
</head>
<body>
{body}
</body>
</html>"""


def _build_pdf_bytes(markdown: str, title: str, *, base_url: str | Path) -> bytes:
    html_content = _markdown_to_html(markdown, title)
    allowed_root = Path(base_url).resolve()
    if not allowed_root.is_dir():
        raise RuntimeError("PDF asset root is unavailable")
    try:
        from weasyprint import HTML
    except ImportError as exc:
        raise RuntimeError("PDF export requires the 'weasyprint' package") from exc

    def fetch_scoped_resource(url: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return _safe_pdf_url_fetcher(
            url,
            *args,
            allowed_root=allowed_root,
            **kwargs,
        )

    return HTML(
        string=html_content,
        base_url=str(allowed_root),
        url_fetcher=fetch_scoped_resource,
    ).write_pdf()


def _pdf_cache_key(markdown: str, title: str) -> str:
    payload = f"{title}\0{markdown}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _normalize_scoped_pdf_resource_url(
    url: str,
    *,
    allowed_root: str | Path | None,
) -> str:
    parsed = urlparse(str(url or ""))
    if parsed.scheme == "data":
        raw_url = str(url)
        if (
            _PDF_DATA_IMAGE_RE.match(raw_url) is None
            or len(raw_url.encode("utf-8")) > _PDF_MAX_DATA_URL_BYTES
        ):
            raise RuntimeError("Unsupported data resource during PDF export")
    else:
        if parsed.scheme not in {"", "file"}:
            raise RuntimeError("Remote resources are not allowed during PDF export")
        if allowed_root is None:
            raise RuntimeError("Local PDF resources require an explicit task root")
        if parsed.netloc or parsed.query or parsed.fragment:
            raise RuntimeError("Invalid local resource during PDF export")

        root = Path(allowed_root).resolve()
        raw_path = Path(unquote(parsed.path))
        candidate = (
            raw_path.resolve()
            if raw_path.is_absolute()
            else (root / raw_path).resolve()
        )
        if (
            not candidate.is_relative_to(root)
            or not candidate.is_file()
            or candidate.suffix.lower() not in _PDF_LOCAL_IMAGE_EXTENSIONS
        ):
            raise RuntimeError("Local PDF resource is outside the task asset boundary")
        if candidate.stat().st_size > int(
            settings.MINERU_MAX_ZIP_MEMBER_UNCOMPRESSED_BYTES
        ):
            raise RuntimeError("Local PDF resource exceeds the asset size limit")
        return candidate.as_uri()
    return str(url)


def _safe_pdf_url_fetcher(
    url: str,
    *args: Any,
    allowed_root: str | Path | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    normalized_url = _normalize_scoped_pdf_resource_url(
        url,
        allowed_root=allowed_root,
    )

    from weasyprint import default_url_fetcher

    return default_url_fetcher(normalized_url, *args, **kwargs)


class PaperTranslationService:
    """Create, run, and serve paper translation tasks."""

    def __init__(
        self,
        *,
        storage_root: str | Path | None = None,
        runtime_service: InsightRuntimeService | None = None,
        model_config_session_factory: Any | None = None,
    ) -> None:
        self.storage_root = Path(
            storage_root or settings.CREATIVE_WORKSHOP_PAPER_TRANSLATION_STORAGE_DIR
        )
        self.runtime_service = runtime_service or insight_runtime_service
        self.model_config_session_factory = (
            model_config_session_factory or AsyncSessionLocal
        )
        self._tasks: dict[str, PaperTranslationTask] = {}
        self._lock = asyncio.Lock()

    def _queue_visibility_timeout_seconds(self) -> float:
        return max(
            float(
                settings.CREATIVE_WORKSHOP_PAPER_TRANSLATION_QUEUE_VISIBILITY_TIMEOUT_SECONDS
            ),
            60.0,
        )

    def _max_retries(self) -> int:
        return max(
            int(settings.CREATIVE_WORKSHOP_PAPER_TRANSLATION_QUEUE_MAX_RETRIES), 0
        )

    def _retry_delay_seconds(self, attempt: int) -> float:
        base_delay = max(
            float(
                settings.CREATIVE_WORKSHOP_PAPER_TRANSLATION_QUEUE_RETRY_DELAY_SECONDS
            ),
            0.0,
        )
        return min(base_delay * max(int(attempt), 1), 300.0)

    @staticmethod
    def _public_error_message(exc: BaseException) -> str:
        if isinstance(exc, PaperTranslationAgentRecursionError):
            return "翻译任务未在允许的执行阶段内完成，请重新尝试"
        return "论文转换或翻译失败，请重新上传后再试"

    def _maintenance_batch_size(self) -> int:
        return max(
            int(
                settings.CREATIVE_WORKSHOP_PAPER_TRANSLATION_QUEUE_MAINTENANCE_BATCH_SIZE
            ),
            1,
        )

    async def promote_due_scheduled_tasks(
        self, redis_client: Any, *, limit: int | None = None
    ) -> int:
        return int(
            await redis_client.eval(
                _QUEUE_PROMOTE_SCRIPT,
                7,
                *_queue_keys(),
                time.time(),
                max(int(limit or self._maintenance_batch_size()), 1),
            )
        )

    async def enqueue_translation_task(
        self, redis_client: Any, item: PaperTranslationQueueItem
    ) -> int:
        normalized_item = PaperTranslationQueueItem(
            owner_id=str(item.owner_id),
            task_id=str(item.task_id),
            filename=_safe_filename(item.filename),
            source_pdf_path=str(item.source_pdf_path),
            model_name=(str(item.model_name or "").strip() or None),
            attempt=max(int(item.attempt), 1),
        )
        payload = json.dumps(
            asdict(normalized_item), ensure_ascii=False, separators=(",", ":")
        )
        return int(
            await redis_client.eval(
                _QUEUE_ENQUEUE_SCRIPT,
                7,
                *_queue_keys(),
                normalized_item.task_id,
                payload,
                normalized_item.attempt,
                time.time(),
            )
        )

    async def recover_processing_queue(
        self, redis_client: Any, *, limit: int | None = None
    ) -> int:
        recovered = int(
            await redis_client.eval(
                _QUEUE_RECOVER_SCRIPT,
                7,
                *_queue_keys(),
                time.time(),
                max(int(limit or self._maintenance_batch_size()), 1),
            )
        )
        if recovered:
            logger.info("Recovered %s expired paper translation lease(s)", recovered)
        return recovered

    async def dequeue_translation_task(
        self, redis_client: Any
    ) -> PaperTranslationTaskLease | None:
        now = time.time()
        token = uuid4().hex
        result = await redis_client.eval(
            _QUEUE_ACQUIRE_SCRIPT,
            7,
            *_queue_keys(),
            now,
            now + self._queue_visibility_timeout_seconds(),
            token,
        )
        if not result:
            return None
        task_id = _as_text(result[0])
        generation = max(int(_as_text(result[3])), 1)
        try:
            item = _parse_queue_item(result[1])
            attempt = max(int(_as_text(result[2])), 1)
            if item.task_id != task_id:
                raise ValueError(
                    "Queue payload task_id does not match its Redis member"
                )
            item.attempt = attempt
        except Exception as exc:
            logger.warning(
                "Dropping invalid paper translation queue item (error_type=%s)",
                type(exc).__name__,
            )
            invalid_lease = PaperTranslationTaskLease(
                item=PaperTranslationQueueItem(
                    owner_id="invalid",
                    task_id=task_id,
                    filename="paper.pdf",
                    source_pdf_path="source.pdf",
                ),
                token=token,
                generation=generation,
            )
            await self._cancel_claim(redis_client, invalid_lease)
            return None
        return PaperTranslationTaskLease(
            item=item,
            token=token,
            generation=generation,
        )

    async def heartbeat_translation_task(
        self,
        redis_client: Any,
        lease: PaperTranslationTaskLease,
    ) -> bool:
        now = time.time()
        return bool(
            await redis_client.eval(
                _QUEUE_HEARTBEAT_SCRIPT,
                7,
                *_queue_keys(),
                lease.task_id,
                lease.token,
                now,
                now + self._queue_visibility_timeout_seconds(),
            )
        )

    async def _prepare_lease_commit(
        self,
        redis_client: Any,
        lease: PaperTranslationTaskLease,
    ) -> None:
        if not await self.heartbeat_translation_task(redis_client, lease):
            raise PaperTranslationLeaseLost(lease.task_id)

    async def activate_translation_lease(
        self,
        redis_client: Any,
        lease: PaperTranslationTaskLease,
    ) -> PaperTranslationTask:
        """Bind a Redis claim to the durable manifest before processing it."""
        await self._prepare_lease_commit(redis_client, lease)
        item = lease.item
        async with self._lock:
            with self._task_file_lock(item.owner_id, item.task_id):
                task = self._load_task_unlocked(
                    owner_id=item.owner_id,
                    task_id=item.task_id,
                )
                if task is None or task.owner_id != item.owner_id:
                    raise FileNotFoundError("Task not found")

                # Terminal states written outside a worker lease (for example an
                # enqueue rejection) are authoritative and must not be restarted.
                if (
                    task.status in {"completed", "failed"}
                    and not task.terminal_lease_token
                ):
                    return task
                if (
                    task.status in {"completed", "failed"}
                    and task.terminal_lease_token == lease.token
                ):
                    return task
                if task.active_lease_token != lease.token and int(
                    lease.generation
                ) <= int(task.lease_generation):
                    raise PaperTranslationLeaseLost(item.task_id)
                foreign_terminal = (
                    task.status in {"completed", "failed"}
                    and task.terminal_lease_token != lease.token
                )
                task.active_lease_token = lease.token
                task.lease_generation = max(
                    int(task.lease_generation) + 1,
                    int(lease.generation),
                )
                if foreign_terminal:
                    task.status = "queued"
                    task.error = None
                    task.thread_id = str(uuid4())
                    task.mineru_batch_id = None
                    task.source_markdown_path = None
                    task.translated_markdown_path = None
                    task.translated_pdf_path = None
                task.terminal_lease_token = None
                task.terminal_lease_generation = None
                task.updated_at = _utc_now()
                self._tasks[item.task_id] = task
                self._persist_task_unlocked(task)
                return task

    async def acknowledge_translation_task(
        self,
        redis_client: Any,
        lease: PaperTranslationTaskLease,
    ) -> bool:
        task = await self.get_task(
            owner_id=lease.item.owner_id,
            task_id=lease.task_id,
        )
        if task is None or task.status not in {"completed", "failed"}:
            return False
        if task.terminal_lease_token not in {None, lease.token}:
            return False
        return bool(
            await redis_client.eval(
                _QUEUE_ACK_SCRIPT,
                7,
                *_queue_keys(),
                lease.task_id,
                lease.token,
                time.time(),
            )
        )

    async def requeue_processing_task(
        self,
        redis_client: Any,
        lease: PaperTranslationTaskLease,
    ) -> bool:
        return bool(
            await redis_client.eval(
                _QUEUE_RELEASE_SCRIPT,
                7,
                *_queue_keys(),
                lease.task_id,
                lease.token,
                time.time(),
            )
        )

    async def _cancel_claim(
        self,
        redis_client: Any,
        lease: PaperTranslationTaskLease,
    ) -> bool:
        return bool(
            await redis_client.eval(
                _QUEUE_CANCEL_SCRIPT,
                7,
                *_queue_keys(),
                lease.task_id,
                lease.token,
                time.time(),
            )
        )

    async def retry_translation_task(
        self,
        redis_client: Any,
        *,
        lease: PaperTranslationTaskLease,
    ) -> bool:
        item = lease.item
        if item.attempt > self._max_retries():
            return False

        await self._update_task(
            owner_id=item.owner_id,
            task_id=item.task_id,
            redis_client=redis_client,
            lease=lease,
            status="queued",
            error=None,
        )
        retry_at = time.time() + self._retry_delay_seconds(item.attempt)
        return bool(
            await redis_client.eval(
                _QUEUE_RETRY_SCRIPT,
                7,
                *_queue_keys(),
                lease.task_id,
                lease.token,
                time.time(),
                retry_at,
                item.attempt + 1,
            )
        )

    async def reconcile_queued_tasks(
        self, redis_client: Any, *, limit: int | None = None
    ) -> int:
        """Idempotently restore durable non-terminal manifests to Redis."""
        max_tasks = max(
            int(
                limit
                or settings.CREATIVE_WORKSHOP_PAPER_TRANSLATION_QUEUE_RECONCILE_MAX_TASKS
            ),
            1,
        )
        if not self.storage_root.exists():
            return 0

        reconciled = 0
        scanned = 0
        raw_cursor = await redis_client.get(QUEUE_RECONCILE_CURSOR_KEY)
        cursor = _as_text(raw_cursor) if raw_cursor else ""
        last_cursor = ""
        reached_end = True
        for owner_dir in sorted(
            self.storage_root.iterdir(), key=lambda path: path.name
        ):
            if scanned >= max_tasks:
                reached_end = False
                break
            if not owner_dir.is_dir() or owner_dir.name.startswith("_"):
                continue
            try:
                task_dirs = sorted(owner_dir.iterdir(), key=lambda path: path.name)
            except FileNotFoundError:
                continue
            for task_dir in task_dirs:
                if scanned >= max_tasks:
                    reached_end = False
                    break
                manifest_path = task_dir / "task.json"
                if not manifest_path.is_file():
                    continue
                manifest_cursor = f"{owner_dir.name}/{task_dir.name}"
                if cursor and manifest_cursor <= cursor:
                    continue
                scanned += 1
                last_cursor = manifest_cursor
                task = self._load_task(owner_id=owner_dir.name, task_id=task_dir.name)
                if task is None or task.status not in {
                    "queued",
                    "converting",
                    "translating",
                }:
                    continue
                try:
                    source_path = self._resolve_task_file(
                        owner_id=task.owner_id,
                        task_id=task.task_id,
                        stored_path=task.source_pdf_path,
                        fallback_name="source.pdf",
                    )
                except FileNotFoundError:
                    logger.warning(
                        "Skipping paper translation task with unsafe source path: %s",
                        task.task_id,
                    )
                    continue
                if not source_path.is_file():
                    continue
                if task.source_pdf_path != str(source_path):
                    try:
                        task = await self.attach_source_pdf(
                            owner_id=task.owner_id,
                            task_id=task.task_id,
                            source_pdf_path=source_path,
                        )
                    except (FileNotFoundError, RuntimeError):
                        continue
                outcome = await self.enqueue_translation_task(
                    redis_client,
                    PaperTranslationQueueItem(
                        owner_id=task.owner_id,
                        task_id=task.task_id,
                        filename=task.filename,
                        source_pdf_path=str(source_path),
                        model_name=task.model_name,
                    ),
                )
                reconciled += int(outcome > 0)
        if reached_end:
            await redis_client.delete(QUEUE_RECONCILE_CURSOR_KEY)
        elif last_cursor:
            await redis_client.set(QUEUE_RECONCILE_CURSOR_KEY, last_cursor)
        if reconciled:
            logger.info(
                "Reconciled %s durable paper translation task(s) into Redis", reconciled
            )
        return reconciled

    async def create_task(
        self,
        *,
        owner_id: str,
        filename: str,
        model_name: str | None = None,
    ) -> PaperTranslationTask:
        task_id = uuid4().hex
        thread_id = str(uuid4())
        now = _utc_now()
        normalized_model_name = (model_name or "").strip() or None
        task = PaperTranslationTask(
            task_id=task_id,
            owner_id=str(owner_id),
            filename=_safe_filename(filename),
            thread_id=thread_id,
            status="queued",
            created_at=now,
            updated_at=now,
            model_name=normalized_model_name,
        )
        async with self._lock:
            self._tasks[task_id] = task
            self._persist_task(task)
        return task

    async def get_task(
        self, *, owner_id: str, task_id: str
    ) -> PaperTranslationTask | None:
        async with self._lock:
            task = self._load_task(owner_id=owner_id, task_id=task_id)
            if task is None or task.owner_id != str(owner_id):
                return None
            self._tasks[task_id] = task
            return task

    def source_pdf_path(self, *, owner_id: str, task_id: str) -> Path:
        return self._task_dir(owner_id, task_id) / "source.pdf"

    async def attach_source_pdf(
        self, *, owner_id: str, task_id: str, source_pdf_path: Path
    ) -> PaperTranslationTask:
        resolved_path = self._resolve_task_file(
            owner_id=owner_id,
            task_id=task_id,
            stored_path=str(source_pdf_path),
            fallback_name="source.pdf",
        )
        return await self._update_task(
            owner_id=owner_id, task_id=task_id, source_pdf_path=str(resolved_path)
        )

    async def mark_task_failed(
        self, *, owner_id: str, task_id: str, error: str
    ) -> PaperTranslationTask:
        return await self._update_task(
            owner_id=owner_id,
            task_id=task_id,
            status="failed",
            error=error or "论文翻译失败",
        )

    async def run_translation_task(
        self,
        *,
        owner_id: str,
        task_id: str,
        filename: str,
        source_pdf_path: str | None = None,
        file_data: bytes | None = None,
        mark_failed_on_error: bool = True,
        redis_client: Any | None = None,
        lease: PaperTranslationTaskLease | None = None,
    ) -> None:
        fence_kwargs = (
            {"redis_client": redis_client, "lease": lease}
            if redis_client is not None and lease is not None
            else {}
        )
        try:
            await self._update_task(
                owner_id=owner_id,
                task_id=task_id,
                redis_client=redis_client,
                lease=lease,
                status="converting",
                error=None,
            )
            source_pdf = self._resolve_task_file(
                owner_id=owner_id,
                task_id=task_id,
                stored_path=source_pdf_path,
                fallback_name="source.pdf",
            )
            pdf_bytes = source_pdf.read_bytes() if source_pdf.exists() else file_data
            if not pdf_bytes:
                raise FileNotFoundError("Source PDF is not available")
            source = await self._convert_pdf_to_markdown(
                owner_id=owner_id,
                file_data=pdf_bytes,
                filename=filename,
                task_id=task_id,
                **fence_kwargs,
            )
            source_path = await self._write_task_text_fenced(
                owner_id,
                task_id,
                "source.md",
                source.markdown,
                redis_client=redis_client,
                lease=lease,
            )
            if source.assets:
                await self._write_task_assets_fenced(
                    owner_id,
                    task_id,
                    source.assets,
                    redis_client=redis_client,
                    lease=lease,
                )
            await self._update_task(
                owner_id=owner_id,
                task_id=task_id,
                redis_client=redis_client,
                lease=lease,
                source_markdown_path=str(source_path),
                status="translating",
            )

            translated_markdown = await self._translate_markdown_with_agent(
                owner_id=owner_id,
                task_id=task_id,
                filename=filename,
                markdown=source.markdown,
                **fence_kwargs,
            )
            normalized_translation = _remove_mermaid_diagram_blocks(
                _strip_markdown_code_fence(translated_markdown)
            )
            if not normalized_translation.strip():
                raise RuntimeError("Agent did not return translated markdown")

            translated_path = await self._write_task_text_fenced(
                owner_id,
                task_id,
                "translation.zh.md",
                normalized_translation,
                redis_client=redis_client,
                lease=lease,
            )
            await self._update_task(
                owner_id=owner_id,
                task_id=task_id,
                redis_client=redis_client,
                lease=lease,
                translated_markdown_path=str(translated_path),
                translated_pdf_path=None,
                status="completed",
                error=None,
            )
        except PaperTranslationLeaseLost:
            raise
        except Exception as exc:
            logger.error(
                "Paper translation task failed: task_id=%s error_type=%s",
                task_id,
                type(exc).__name__,
            )
            if mark_failed_on_error:
                await self._update_task(
                    owner_id=owner_id,
                    task_id=task_id,
                    redis_client=redis_client,
                    lease=lease,
                    status="failed",
                    error=self._public_error_message(exc),
                )
                return
            raise

    async def get_translated_markdown(
        self,
        *,
        owner_id: str,
        task_id: str,
        inline_assets: bool = False,
        asset_url_prefix: str | None = None,
        sign_asset_url=None,
    ) -> tuple[str, str]:
        task = await self.get_task(owner_id=owner_id, task_id=task_id)
        if (
            task is None
            or task.status != "completed"
            or not task.translated_markdown_path
        ):
            raise FileNotFoundError("Translated markdown is not available")
        path = self._resolve_task_file(
            owner_id=owner_id,
            task_id=task_id,
            stored_path=task.translated_markdown_path,
            fallback_name="translation.zh.md",
        )
        markdown = _remove_mermaid_diagram_blocks(path.read_text(encoding="utf-8"))
        if inline_assets:
            markdown = self._inline_markdown_assets(
                owner_id=owner_id, task_id=task_id, markdown=markdown
            )
        elif asset_url_prefix:
            markdown = self._rewrite_markdown_asset_paths_to_urls(
                owner_id=owner_id,
                task_id=task_id,
                markdown=markdown,
                asset_url_prefix=asset_url_prefix,
                sign_asset_url=sign_asset_url,
            )
        return _download_filename(task.filename, ".md"), markdown

    async def get_source_pdf(self, *, owner_id: str, task_id: str) -> tuple[str, bytes]:
        task = await self.get_task(owner_id=owner_id, task_id=task_id)
        if task is None or not task.source_pdf_path:
            raise FileNotFoundError("Source PDF is not available")
        path = self._resolve_task_file(
            owner_id=owner_id,
            task_id=task_id,
            stored_path=task.source_pdf_path,
            fallback_name="source.pdf",
        )
        return task.filename, path.read_bytes()

    async def get_translated_pdf(
        self, *, owner_id: str, task_id: str
    ) -> tuple[str, bytes]:
        task = await self.get_task(owner_id=owner_id, task_id=task_id)
        if (
            task is None
            or task.status != "completed"
            or not task.translated_markdown_path
        ):
            raise FileNotFoundError("Translated markdown is not available")

        pdf_path = self._resolve_task_file(
            owner_id=owner_id,
            task_id=task_id,
            stored_path=task.translated_pdf_path,
            fallback_name="translation.zh.pdf",
        )
        markdown_path = self._resolve_task_file(
            owner_id=owner_id,
            task_id=task_id,
            stored_path=task.translated_markdown_path,
            fallback_name="translation.zh.md",
        )
        markdown = _remove_mermaid_diagram_blocks(
            markdown_path.read_text(encoding="utf-8")
        )
        pdf_ready_markdown = self._rewrite_markdown_asset_paths_for_local_pdf(
            owner_id=owner_id,
            task_id=task_id,
            markdown=markdown,
        )
        cache_key = _pdf_cache_key(pdf_ready_markdown, task.filename)
        cache_meta_path = pdf_path.with_name(f"{pdf_path.name}.meta.json")
        cached_key = ""
        if pdf_path.exists() and cache_meta_path.exists():
            with contextlib.suppress(Exception):
                cache_payload = json.loads(cache_meta_path.read_text(encoding="utf-8"))
                cached_key = str(cache_payload.get("cache_key") or "")

        if not pdf_path.exists() or cached_key != cache_key:
            pdf_bytes = await asyncio.to_thread(
                _build_pdf_bytes,
                pdf_ready_markdown,
                task.filename,
                base_url=self._task_dir(owner_id, task_id),
            )
            cache_metadata = json.dumps(
                {"cache_key": cache_key, "generated_at": _utc_now()},
                ensure_ascii=False,
                indent=2,
            ).encode("utf-8")
            with self._task_file_lock(owner_id, task_id):
                self._atomic_write_bytes(pdf_path, pdf_bytes)
                self._atomic_write_bytes(cache_meta_path, cache_metadata)
            await self._update_task(
                owner_id=owner_id,
                task_id=task_id,
                translated_pdf_path=str(pdf_path),
            )
        return _download_filename(task.filename, ".pdf"), pdf_path.read_bytes()

    def build_response_payload(self, task: PaperTranslationTask) -> dict[str, Any]:
        return {
            "task_id": task.task_id,
            "status": task.status,
            "filename": task.filename,
            "thread_id": task.thread_id,
            "model_name": task.model_name,
            "created_at": task.created_at,
            "updated_at": task.updated_at,
            "error": task.error,
        }

    async def _convert_pdf_to_markdown(
        self,
        *,
        owner_id: str,
        file_data: bytes,
        filename: str,
        task_id: str,
        redis_client: Any | None = None,
        lease: PaperTranslationTaskLease | None = None,
    ) -> PaperTranslationSource:
        result = await MineruService.convert_document(file_data, filename)
        batch_id = str(result.get("batch_id") or result.get("task_id") or "").strip()
        if not batch_id:
            raise RuntimeError("MinerU did not return a batch_id")
        await self._update_task(
            owner_id=owner_id,
            task_id=task_id,
            redis_client=redis_client,
            lease=lease,
            mineru_batch_id=batch_id,
        )

        poll_interval = max(
            float(
                settings.CREATIVE_WORKSHOP_PAPER_TRANSLATION_MINERU_POLL_INTERVAL_SECONDS
            ),
            0.5,
        )
        max_attempts = max(
            int(settings.CREATIVE_WORKSHOP_PAPER_TRANSLATION_MINERU_MAX_ATTEMPTS), 1
        )

        for attempt in range(max_attempts):
            task_status = await MineruService.get_task_status(batch_id)
            status_value = str(task_status.get("status") or "pending")
            if status_value == "completed":
                result = await MineruService.get_content_with_assets(batch_id)
                return PaperTranslationSource(
                    markdown=result.markdown, assets=result.assets
                )
            if status_value == "failed":
                raise RuntimeError("MinerU paper conversion failed")
            if attempt % 6 == 0:
                logger.info(
                    "MinerU paper translation task pending: task=%s status=%s",
                    task_id,
                    status_value,
                )
            await asyncio.sleep(poll_interval)

        raise TimeoutError("MinerU task timeout")

    async def _translate_markdown_with_agent(
        self,
        *,
        owner_id: str,
        task_id: str,
        filename: str,
        markdown: str,
        redis_client: Any | None = None,
        lease: PaperTranslationTaskLease | None = None,
    ) -> str:
        task = await self.get_task(owner_id=owner_id, task_id=task_id)
        if task is None:
            raise FileNotFoundError("Task not found")
        if not _is_uuid_text(task.thread_id):
            task = await self._update_task(
                owner_id=owner_id,
                task_id=task_id,
                redis_client=redis_client,
                lease=lease,
                thread_id=str(uuid4()),
            )
        normalized_thread_id = self.runtime_service.build_thread_id(task.thread_id)
        await self.runtime_service.ensure_thread_exists(normalized_thread_id)
        assistant_id = await self.runtime_service.resolve_assistant_id()
        model_name, dynamic_model_token = await self._resolve_agent_model_context(
            owner_id=owner_id,
            selected_model_name=task.model_name,
            thread_id=normalized_thread_id,
        )

        source_filename = f"{Path(_safe_filename(filename)).stem or 'paper'}.source.md"
        uploaded = await self.runtime_service.upload_bytes(
            thread_id=normalized_thread_id,
            filename=source_filename,
            data=markdown.encode("utf-8"),
            content_type="text/markdown; charset=utf-8",
        )
        uploaded_filename = (
            str(uploaded.get("filename") or source_filename).strip() or source_filename
        )
        uploaded_size = int(uploaded.get("size") or len(markdown.encode("utf-8")))
        uploaded_files = [
            {
                "filename": uploaded_filename,
                "size": uploaded_size,
                "path": str(
                    uploaded.get("virtual_path")
                    or f"/mnt/user-data/uploads/{uploaded_filename}"
                ),
            }
        ]
        output_filename = f"{Path(_safe_filename(filename)).stem or 'paper'}.zh.md"
        expected_artifact_path = f"/mnt/user-data/outputs/{output_filename}"
        url = f"{self.runtime_service.langgraph_url}{self.runtime_service.build_run_stream_path(normalized_thread_id)}"
        timeout_seconds = max(
            float(settings.CREATIVE_WORKSHOP_PAPER_TRANSLATION_AGENT_TIMEOUT_SECONDS),
            60.0,
        )
        timeout = httpx.Timeout(timeout_seconds, connect=20.0)
        recursion_limit = max(
            int(settings.CREATIVE_WORKSHOP_PAPER_TRANSLATION_AGENT_RECURSION_LIMIT),
            1,
        )
        max_continuations = max(
            int(settings.CREATIVE_WORKSHOP_PAPER_TRANSLATION_AGENT_MAX_CONTINUATIONS),
            0,
        )
        previous_progress: tuple[int, str] | None = None
        run_result: PaperTranslationAgentRunResult | None = None

        for run_index in range(max_continuations + 1):
            run_request = self.runtime_service.build_run_request_template(
                thread_id=normalized_thread_id,
                assistant_id=assistant_id,
                model_name=model_name,
                thinking_enabled=False,
                is_plan_mode=False,
                subagent_enabled=False,
                recursion_limit=recursion_limit,
            )
            if dynamic_model_token:
                context_payload = run_request.get("context")
                if not isinstance(context_payload, dict):
                    context_payload = {}
                    run_request["context"] = context_payload
                context_payload["dynamic_model_token"] = dynamic_model_token
            prompt = (
                self._build_translation_prompt(
                    uploaded_filename, expected_artifact_path
                )
                if run_index == 0
                else self._build_translation_continuation_prompt(
                    uploaded_filename,
                    expected_artifact_path,
                )
            )
            run_request["input"] = {
                "messages": [
                    {
                        "role": "user",
                        "content": prompt,
                        "additional_kwargs": {"files": uploaded_files},
                    }
                ]
            }

            try:
                run_result = await self._stream_translation_agent_run(
                    task_id=task_id,
                    thread_id=normalized_thread_id,
                    url=url,
                    timeout=timeout,
                    run_request=run_request,
                )
                break
            except PaperTranslationAgentRecursionError as exc:
                partial_translation = await self._try_download_translation_artifact(
                    thread_id=normalized_thread_id,
                    artifact_path=expected_artifact_path,
                )
                if not partial_translation.strip():
                    raise RuntimeError(
                        "翻译 Agent 已达到单次执行上限，但没有生成可继续的译文文件"
                    ) from exc

                partial_bytes = partial_translation.encode("utf-8")
                progress = (
                    len(partial_bytes),
                    hashlib.sha256(partial_bytes).hexdigest(),
                )
                if previous_progress == progress:
                    raise RuntimeError(
                        "翻译 Agent 已停止产生有效进展，任务已终止以避免重复循环"
                    ) from exc
                previous_progress = progress

                if run_index >= max_continuations:
                    raise RuntimeError(
                        f"翻译 Agent 在 {max_continuations + 1} 个受限执行阶段后仍未完成"
                    ) from exc
                logger.warning(
                    "Paper translation agent reached recursion limit with progress; "
                    "continuing task_id=%s phase=%s output_bytes=%s",
                    task_id,
                    run_index + 1,
                    len(partial_bytes),
                )

        if run_result is None:
            raise RuntimeError("翻译 Agent 未返回执行结果")

        artifact_path = _extract_translated_markdown_path(run_result.last_values_text)
        if not artifact_path:
            artifact_path = _extract_translated_markdown_path(run_result.tuple_text)
        if not artifact_path and run_result.artifact_paths:
            markdown_artifacts = [
                path
                for path in run_result.artifact_paths
                if path.strip().lower().endswith(".md")
            ]
            artifact_path = (
                markdown_artifacts[-1]
                if markdown_artifacts
                else run_result.artifact_paths[-1]
            )

        fallback_translation = ""
        if not artifact_path:
            fallback_translation = await self._try_download_translation_artifact(
                thread_id=normalized_thread_id,
                artifact_path=expected_artifact_path,
            )
            if not fallback_translation.strip():
                raise RuntimeError("Agent did not return translated markdown path")
            artifact_path = expected_artifact_path

        artifact_path = _normalize_translated_markdown_artifact_path(artifact_path)
        translated_markdown = (
            fallback_translation
            or await self.runtime_service.download_thread_artifact_text(
                normalized_thread_id,
                artifact_path,
            )
        )
        if not translated_markdown.strip():
            raise RuntimeError("Agent returned an empty translated markdown artifact")
        return translated_markdown

    async def _stream_translation_agent_run(
        self,
        *,
        task_id: str,
        thread_id: str,
        url: str,
        timeout: httpx.Timeout,
        run_request: dict[str, Any],
    ) -> PaperTranslationAgentRunResult:
        last_values_text = ""
        tuple_text_parts: list[str] = []
        artifact_paths: list[str] = []

        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            async with client.stream(
                "POST",
                url,
                headers={
                    "Accept": "text/event-stream",
                    "Content-Type": "application/json",
                },
                json=run_request,
            ) as response:
                response.raise_for_status()
                async for event_name, payload in self._iter_sse_events(response):
                    if event_name == "error":
                        recursion_error = _is_graph_recursion_error(payload)
                        log_method = logger.warning if recursion_error else logger.error
                        log_method(
                            "Paper translation agent stream failed: task_id=%s recursion_limit=%s",
                            task_id,
                            recursion_error,
                        )
                        if recursion_error:
                            raise PaperTranslationAgentRecursionError(
                                "Paper translation agent reached its recursion limit"
                            )
                        raise RuntimeError("Paper translation agent run failed")
                    if event_name == "values":
                        values_text = _last_ai_text_from_values(payload)
                        if values_text:
                            last_values_text = values_text
                        artifact_paths.extend(_artifact_paths_from_values(payload))
                        continue
                    if event_name in {"messages", "messages-tuple"}:
                        text = _ai_text_from_message_payload(payload)
                        if text:
                            tuple_text_parts.append(text)

        return PaperTranslationAgentRunResult(
            last_values_text=last_values_text,
            tuple_text="".join(tuple_text_parts).strip(),
            artifact_paths=artifact_paths,
        )

    async def _try_download_translation_artifact(
        self,
        *,
        thread_id: str,
        artifact_path: str,
    ) -> str:
        try:
            return await self.runtime_service.download_thread_artifact_text(
                thread_id,
                artifact_path,
            )
        except FileNotFoundError:
            return ""
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return ""
            raise

    async def _resolve_agent_model_context(
        self,
        *,
        owner_id: str,
        selected_model_name: str | None,
        thread_id: str,
    ) -> tuple[str, str | None]:
        runtime_models = await self.runtime_service.list_runtime_models()
        async with self.model_config_session_factory() as db:
            model_config_service = ModelConfigService(db)
            model_resolution = await model_config_service.resolve_selected_model(
                user_id=UUID(str(owner_id)),
                selected_model_name=selected_model_name,
                runtime_models=runtime_models,
                thread_id=thread_id,
            )
        resolved_model_name = str(model_resolution["runtime_model_name"]).strip()
        dynamic_model_token = model_resolution.get("dynamic_model_token")
        return resolved_model_name, str(
            dynamic_model_token
        ).strip() if dynamic_model_token else None

    @staticmethod
    def _build_translation_prompt(uploaded_filename: str, output_path: str) -> str:
        return (
            f"请使用 `paper-translation` skill 翻译上传的 Markdown 论文文件 `{uploaded_filename}`。\n\n"
            "必须严格执行：\n"
            "1. 先读取并遵循 `paper-translation` skill。\n"
            "2. 使用 read_file 读取上传的 Markdown 文件，不要要求用户重新提供内容。\n"
            f"3. 只使用 `{output_path}` 作为译文文件；先创建一次，之后只以 append=true 追加。\n"
            "4. 按连续章节批量读取、直接翻译并追加；不要逐句调用工具，不要重复读取或重写已完成内容。\n"
            "5. 参考文献部分不翻译，保持原文。\n"
            "6. 禁止使用 bash、Python/其他脚本、临时文件、标题映射或全局替换来生成或修复译文。\n"
            "7. 只允许一次最终完整性检查；不要围绕格式或标题反复调试。\n"
            f"8. 完成后调用 present_files 发布 `{output_path}`。\n"
            "9. 最终回复不要输出完整译文，只返回 JSON，格式必须为："
            f'{{"translated_markdown_path":"{output_path}"}}'
        )

    @staticmethod
    def _build_translation_continuation_prompt(
        uploaded_filename: str, output_path: str
    ) -> str:
        return (
            "继续尚未完成的论文翻译任务，不要从头开始。\n\n"
            f"源文件：`/mnt/user-data/uploads/{uploaded_filename}`\n"
            f"已有译文：`{output_path}`\n\n"
            "必须严格执行：\n"
            "1. 使用 read_file 分别读取源文件和已有译文末尾，确定最后一个已完整翻译的章节。\n"
            "2. 保留已有译文，不得清空、覆盖、重建或重复追加已完成章节。\n"
            "3. 从下一段未完成内容继续，按连续章节批量翻译并使用 write_file append=true 追加。\n"
            "4. 参考文献保持原文；保留 Markdown、公式、表格、图片链接、代码块、脚注和引用。\n"
            "5. 禁止使用 bash、Python/其他脚本、临时文件、标题映射、str_replace 或全局替换。\n"
            "6. 不要调试非关键格式差异，只允许一次最终完整性检查。\n"
            f"7. 完成后调用 present_files 发布 `{output_path}`，并只返回："
            f'{{"translated_markdown_path":"{output_path}"}}'
        )

    @staticmethod
    async def _iter_sse_events(response: httpx.Response):
        buffer = ""
        event_name = "message"
        data_lines: list[str] = []

        async for chunk in response.aiter_text():
            buffer += chunk
            lines = buffer.splitlines(keepends=True)
            if lines and not lines[-1].endswith(("\n", "\r")):
                buffer = lines.pop()
            else:
                buffer = ""

            for raw_line in lines:
                line = raw_line.rstrip("\r\n")
                if not line:
                    if data_lines or event_name != "message":
                        yield PaperTranslationService._parse_sse_event(
                            event_name, data_lines
                        )
                    event_name = "message"
                    data_lines = []
                    continue
                if line.startswith(":"):
                    continue
                if line.startswith("event:"):
                    event_name = line[6:].strip() or "message"
                    continue
                if line.startswith("data:"):
                    data_lines.append(line[5:].lstrip())

        if data_lines or event_name != "message":
            yield PaperTranslationService._parse_sse_event(event_name, data_lines)

    @staticmethod
    def _parse_sse_event(event_name: str, data_lines: list[str]) -> tuple[str, Any]:
        payload_text = "\n".join(data_lines).strip()
        normalized_event = (event_name or "message").strip()
        if not payload_text:
            return normalized_event, None
        if payload_text == "[DONE]":
            return "end", None
        try:
            return normalized_event, json.loads(payload_text)
        except json.JSONDecodeError:
            return normalized_event, payload_text

    async def _update_task(
        self,
        *,
        owner_id: str,
        task_id: str,
        redis_client: Any | None = None,
        lease: PaperTranslationTaskLease | None = None,
        **updates: Any,
    ) -> PaperTranslationTask:
        if (redis_client is None) != (lease is None):
            raise ValueError("redis_client and lease must be provided together")
        if lease is not None:
            if lease.task_id != str(task_id) or lease.item.owner_id != str(owner_id):
                raise PaperTranslationLeaseLost(str(task_id))
            await self._prepare_lease_commit(redis_client, lease)
        async with self._lock:
            with self._task_file_lock(owner_id, task_id):
                task = self._load_task_unlocked(owner_id=owner_id, task_id=task_id)
                if task is None or task.owner_id != str(owner_id):
                    raise FileNotFoundError("Task not found")
                if lease is not None and task.active_lease_token != lease.token:
                    raise PaperTranslationLeaseLost(str(task_id))
                requested_status = updates.get("status")
                if task.status in {"completed", "failed"} and requested_status not in {
                    None,
                    task.status,
                }:
                    if lease is not None:
                        raise PaperTranslationLeaseLost(str(task_id))
                    raise RuntimeError(
                        "Terminal paper translation task state cannot be overwritten"
                    )
                if lease is not None and task.status in {"completed", "failed"}:
                    raise PaperTranslationLeaseLost(str(task_id))
                for key, value in updates.items():
                    if hasattr(task, key):
                        setattr(task, key, value)
                if requested_status in {"completed", "failed"}:
                    task.terminal_lease_token = (
                        lease.token if lease is not None else None
                    )
                    task.terminal_lease_generation = (
                        task.lease_generation if lease is not None else None
                    )
                if "updated_at" not in updates:
                    task.updated_at = _utc_now()
                self._tasks[task_id] = task
                self._persist_task_unlocked(task)
                return task

    def _task_dir(self, owner_id: str, task_id: str) -> Path:
        safe_task = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(task_id)).strip("._") or "task"
        return self._owner_dir(owner_id) / safe_task

    def _owner_dir(self, owner_id: str) -> Path:
        safe_owner = (
            re.sub(r"[^A-Za-z0-9_.-]+", "_", str(owner_id)).strip("._") or "owner"
        )
        return self.storage_root / safe_owner

    def _resolve_task_file(
        self,
        *,
        owner_id: str,
        task_id: str,
        stored_path: str | None,
        fallback_name: str,
    ) -> Path:
        task_dir = self._task_dir(owner_id, task_id).resolve()
        if not stored_path:
            return task_dir / fallback_name

        raw_path = Path(stored_path)
        candidates = [raw_path if raw_path.is_absolute() else raw_path.resolve()]
        if not raw_path.is_absolute():
            candidates.append(task_dir / raw_path)

        for candidate in candidates:
            resolved = candidate.resolve()
            try:
                resolved.relative_to(task_dir)
            except ValueError:
                continue
            return resolved
        raise FileNotFoundError("Task file path is outside task directory")

    def _assets_dir(self, owner_id: str, task_id: str) -> Path:
        return self._task_dir(owner_id, task_id) / "assets"

    async def _write_task_assets_fenced(
        self,
        owner_id: str,
        task_id: str,
        assets: dict[str, bytes],
        *,
        redis_client: Any | None,
        lease: PaperTranslationTaskLease | None,
    ) -> None:
        for asset_path, content in assets.items():
            normalized = _normalize_asset_path(asset_path)
            if not normalized:
                logger.warning("Skipping unsafe MinerU asset path")
                continue
            await self._write_task_bytes_fenced(
                owner_id,
                task_id,
                f"assets/{normalized}",
                content,
                redis_client=redis_client,
                lease=lease,
            )

    def _resolve_asset_file(
        self, *, owner_id: str, task_id: str, asset_path: str
    ) -> Path | None:
        normalized = _normalize_asset_path(asset_path)
        if not normalized:
            return None
        asset_roots: list[Path] = []
        task = self._load_task(owner_id=owner_id, task_id=task_id)
        if task is not None and task.translated_markdown_path:
            with contextlib.suppress(FileNotFoundError):
                translated_path = self._resolve_task_file(
                    owner_id=owner_id,
                    task_id=task_id,
                    stored_path=task.translated_markdown_path,
                    fallback_name="translation.zh.md",
                )
                asset_roots.append(translated_path.parent / "assets")
        asset_roots.append(self._assets_dir(owner_id, task_id))
        for asset_root in asset_roots:
            assets_dir = asset_root.resolve()
            candidate = (assets_dir / normalized).resolve()
            try:
                candidate.relative_to(assets_dir)
            except ValueError:
                continue
            if candidate.is_file():
                return candidate
        return None

    def resolve_asset_file(
        self, *, owner_id: str, task_id: str, asset_path: str
    ) -> Path | None:
        return self._resolve_asset_file(
            owner_id=owner_id, task_id=task_id, asset_path=asset_path
        )

    def _inline_markdown_assets(
        self, *, owner_id: str, task_id: str, markdown: str
    ) -> str:
        def replace(image_path: str) -> str:
            image_path = image_path.strip()
            if re.match(r"^(?:https?:|data:|blob:|mailto:)", image_path, re.IGNORECASE):
                return image_path
            asset_file = self._resolve_asset_file(
                owner_id=owner_id, task_id=task_id, asset_path=image_path
            )
            if asset_file is None:
                return image_path
            mime_type = (
                mimetypes.guess_type(asset_file.name)[0] or "application/octet-stream"
            )
            encoded = base64.b64encode(asset_file.read_bytes()).decode("ascii")
            return f"data:{mime_type};base64,{encoded}"

        return _replace_markdown_image_destinations(markdown, replace)

    def _rewrite_markdown_asset_paths_to_urls(
        self,
        *,
        owner_id: str,
        task_id: str,
        markdown: str,
        asset_url_prefix: str,
        sign_asset_url=None,
    ) -> str:
        prefix = asset_url_prefix.rstrip("/")

        def replace(image_path: str) -> str:
            image_path = image_path.strip()
            if re.match(r"^(?:https?:|data:|blob:|mailto:)", image_path, re.IGNORECASE):
                return image_path
            normalized = _normalize_asset_path(image_path)
            if not normalized:
                return image_path
            asset_file = self._resolve_asset_file(
                owner_id=owner_id, task_id=task_id, asset_path=normalized
            )
            if asset_file is None:
                return image_path
            encoded_path = "/".join(quote(part) for part in normalized.split("/"))
            asset_url = f"{prefix}/{encoded_path}"
            if sign_asset_url is not None:
                return sign_asset_url(asset_url, normalized)
            return asset_url

        return _replace_markdown_image_destinations(markdown, replace)

    def _rewrite_markdown_asset_paths_for_local_pdf(
        self, *, owner_id: str, task_id: str, markdown: str
    ) -> str:
        def replace(image_path: str) -> str:
            image_path = image_path.strip()
            if re.match(r"^(?:https?:|data:|blob:|mailto:)", image_path, re.IGNORECASE):
                return image_path
            asset_file = self._resolve_asset_file(
                owner_id=owner_id, task_id=task_id, asset_path=image_path
            )
            if asset_file is None:
                return image_path
            relative = asset_file.resolve().relative_to(
                self._task_dir(owner_id, task_id).resolve()
            )
            return relative.as_posix()

        return _replace_markdown_image_destinations(markdown, replace)

    def _task_manifest_path(self, owner_id: str, task_id: str) -> Path:
        return self._task_dir(owner_id, task_id) / "task.json"

    @contextlib.contextmanager
    def _task_file_lock(self, owner_id: str, task_id: str) -> Iterator[None]:
        task_dir = self._task_dir(owner_id, task_id)
        task_dir.mkdir(parents=True, exist_ok=True)
        lock_path = task_dir / ".task.lock"
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        directory_fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

    def _atomic_write_bytes(self, path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            with temp_path.open("xb") as target:
                target.write(content)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temp_path, path)
            self._fsync_directory(path.parent)
        finally:
            temp_path.unlink(missing_ok=True)

    def _persist_task(self, task: PaperTranslationTask) -> None:
        with self._task_file_lock(task.owner_id, task.task_id):
            self._persist_task_unlocked(task)

    def _persist_task_unlocked(self, task: PaperTranslationTask) -> None:
        manifest = json.dumps(asdict(task), ensure_ascii=False, indent=2).encode(
            "utf-8"
        )
        self._atomic_write_bytes(
            self._task_manifest_path(task.owner_id, task.task_id),
            manifest,
        )

    def _load_task(self, *, owner_id: str, task_id: str) -> PaperTranslationTask | None:
        with self._task_file_lock(owner_id, task_id):
            return self._load_task_unlocked(owner_id=owner_id, task_id=task_id)

    def _load_task_unlocked(
        self, *, owner_id: str, task_id: str
    ) -> PaperTranslationTask | None:
        path = self._task_manifest_path(owner_id, task_id)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return PaperTranslationTask(**payload)
        except Exception as exc:
            logger.warning(
                "Failed to load paper translation task manifest: task_id=%s error_type=%s",
                task_id,
                type(exc).__name__,
            )
            return None

    async def _write_task_text_fenced(
        self,
        owner_id: str,
        task_id: str,
        filename: str,
        content: str,
        *,
        redis_client: Any | None,
        lease: PaperTranslationTaskLease | None,
    ) -> Path:
        return await self._write_task_bytes_fenced(
            owner_id,
            task_id,
            filename,
            content.encode("utf-8"),
            redis_client=redis_client,
            lease=lease,
        )

    async def _write_task_bytes_fenced(
        self,
        owner_id: str,
        task_id: str,
        filename: str,
        content: bytes,
        *,
        redis_client: Any | None,
        lease: PaperTranslationTaskLease | None,
    ) -> Path:
        if (redis_client is None) != (lease is None):
            raise ValueError("redis_client and lease must be provided together")
        if lease is not None:
            if lease.task_id != str(task_id) or lease.item.owner_id != str(owner_id):
                raise PaperTranslationLeaseLost(str(task_id))
            await self._prepare_lease_commit(redis_client, lease)
            safe_token = re.sub(r"[^A-Za-z0-9_-]+", "", lease.token)
            if safe_token != lease.token or not safe_token:
                raise PaperTranslationLeaseLost(str(task_id))
            path = self._task_dir(owner_id, task_id) / ".leases" / safe_token / filename
        else:
            path = self._task_dir(owner_id, task_id) / filename
        async with self._lock:
            with self._task_file_lock(owner_id, task_id):
                if lease is not None:
                    task = self._load_task_unlocked(
                        owner_id=owner_id,
                        task_id=task_id,
                    )
                    if (
                        task is None
                        or task.active_lease_token != lease.token
                        or task.status in {"completed", "failed"}
                    ):
                        raise PaperTranslationLeaseLost(str(task_id))
                self._atomic_write_bytes(path, content)
        return path


paper_translation_service = PaperTranslationService()


class PaperTranslationQueueWorker:
    def __init__(
        self,
        *,
        service: PaperTranslationService,
        redis_client: Any,
        concurrency: int = 1,
    ) -> None:
        self.service = service
        self.redis = redis_client
        self.concurrency = max(int(concurrency), 1)
        self.heartbeat_interval_seconds = min(
            max(
                float(
                    settings.CREATIVE_WORKSHOP_PAPER_TRANSLATION_QUEUE_HEARTBEAT_INTERVAL_SECONDS
                ),
                1.0,
            ),
            self.service._queue_visibility_timeout_seconds() / 3,
        )
        self.reconcile_interval_seconds = max(
            float(
                settings.CREATIVE_WORKSHOP_PAPER_TRANSLATION_QUEUE_RECONCILE_INTERVAL_SECONDS
            ),
            1.0,
        )
        self._running = False
        self._tasks: list[asyncio.Task[Any]] = []
        self._active_leases: dict[str, PaperTranslationTaskLease] = {}

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        await self.service.recover_processing_queue(self.redis)
        await self.service.promote_due_scheduled_tasks(self.redis)
        await self.service.reconcile_queued_tasks(self.redis)
        self._tasks = [
            asyncio.create_task(
                self._maintenance_loop(), name="paper-translation-maintenance"
            ),
            *[
                asyncio.create_task(
                    self._consume_loop(index), name=f"paper-translation-worker-{index}"
                )
                for index in range(self.concurrency)
            ],
        ]
        logger.info(
            "Paper translation queue worker started with concurrency=%s",
            self.concurrency,
        )

    async def stop(self) -> None:
        self._running = False
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        for lease in list(self._active_leases.values()):
            with contextlib.suppress(Exception):
                await self.service.requeue_processing_task(self.redis, lease)
        self._active_leases.clear()
        self._tasks = []
        logger.info("Paper translation queue worker stopped")

    async def _maintenance_loop(self) -> None:
        while self._running:
            try:
                await self.service.promote_due_scheduled_tasks(self.redis)
                await self.service.recover_processing_queue(self.redis)
                await self.service.reconcile_queued_tasks(self.redis)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(
                    "Paper translation queue maintenance failed; it will retry "
                    "(error_type=%s)",
                    type(exc).__name__,
                )
            await asyncio.sleep(self.reconcile_interval_seconds)

    async def _consume_loop(self, worker_index: int) -> None:
        while self._running:
            lease: PaperTranslationTaskLease | None = None
            try:
                lease = await self.service.dequeue_translation_task(self.redis)
                if lease is None:
                    await asyncio.sleep(WORKER_IDLE_TIMEOUT_SECONDS)
                    continue
                self._active_leases[lease.token] = lease
                await self._handle_lease(lease, worker_index)
                self._active_leases.pop(lease.token, None)
                lease = None
            except asyncio.CancelledError:
                if lease is not None:
                    with contextlib.suppress(Exception):
                        await asyncio.shield(
                            self.service.requeue_processing_task(self.redis, lease)
                        )
                raise
            except Exception as exc:
                logger.error(
                    "Paper translation worker %s failed; its lease will be requeued "
                    "(error_type=%s)",
                    worker_index,
                    type(exc).__name__,
                )
                if lease is not None:
                    with contextlib.suppress(Exception):
                        await self.service.requeue_processing_task(self.redis, lease)
                await asyncio.sleep(REDIS_ERROR_BACKOFF_SECONDS)
            finally:
                if lease is not None:
                    self._active_leases.pop(lease.token, None)

    async def _handle_lease(
        self, lease: PaperTranslationTaskLease, worker_index: int
    ) -> None:
        item = lease.item
        task = await self.service.get_task(owner_id=item.owner_id, task_id=item.task_id)
        if task is None:
            await self.service._cancel_claim(self.redis, lease)
            return
        if task.status in {"completed", "failed"} and not task.terminal_lease_token:
            await self.service.acknowledge_translation_task(self.redis, lease)
            return
        try:
            task = await self.service.activate_translation_lease(self.redis, lease)
        except PaperTranslationLeaseLost:
            logger.warning(
                "Paper translation worker %s lost lease before activation for task_id=%s",
                worker_index,
                item.task_id,
            )
            return
        if task.status in {"completed", "failed"}:
            await self.service.acknowledge_translation_task(self.redis, lease)
            return
        if item.attempt > self.service._max_retries() + 1:
            await self._mark_failed_and_ack(
                lease,
                RuntimeError("论文翻译任务已中断，请重新上传后再试"),
            )
            return

        try:
            await self._run_with_heartbeat(lease)
            finished_task = await self.service.get_task(
                owner_id=item.owner_id,
                task_id=item.task_id,
            )
            if (
                finished_task is None
                or finished_task.status != "completed"
                or finished_task.terminal_lease_token != lease.token
            ):
                raise RuntimeError(
                    "Paper translation worker returned without a fenced completed manifest"
                )
        except PaperTranslationLeaseLost:
            logger.warning(
                "Paper translation worker %s lost lease for task_id=%s",
                worker_index,
                item.task_id,
            )
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(
                "Paper translation task failed: task_id=%s error_type=%s",
                item.task_id,
                type(exc).__name__,
            )
            if item.attempt <= self.service._max_retries():
                try:
                    retried = await self.service.retry_translation_task(
                        self.redis, lease=lease
                    )
                except PaperTranslationLeaseLost:
                    return
                if retried:
                    logger.warning(
                        "Scheduled paper translation retry task_id=%s attempt=%s",
                        item.task_id,
                        item.attempt + 1,
                    )
                    return
                return
            await self._mark_failed_and_ack(lease, exc)
            return

        acknowledged = await self.service.acknowledge_translation_task(
            self.redis, lease
        )
        if not acknowledged:
            logger.warning(
                "Completed paper translation task lost lease before ack: %s",
                item.task_id,
            )

    async def _mark_failed_and_ack(
        self,
        lease: PaperTranslationTaskLease,
        exc: BaseException,
    ) -> None:
        item = lease.item
        try:
            await self.service._update_task(
                owner_id=item.owner_id,
                task_id=item.task_id,
                redis_client=self.redis,
                lease=lease,
                status="failed",
                error=self.service._public_error_message(exc),
            )
        except PaperTranslationLeaseLost:
            return
        await self.service.acknowledge_translation_task(self.redis, lease)

    async def _run_with_heartbeat(self, lease: PaperTranslationTaskLease) -> None:
        item = lease.item
        processing_task = asyncio.create_task(
            self.service.run_translation_task(
                owner_id=item.owner_id,
                task_id=item.task_id,
                filename=item.filename,
                source_pdf_path=item.source_pdf_path,
                mark_failed_on_error=False,
                redis_client=self.redis,
                lease=lease,
            )
        )
        heartbeat_task = asyncio.create_task(self._heartbeat_loop(lease))
        try:
            done, _ = await asyncio.wait(
                {processing_task, heartbeat_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if heartbeat_task in done:
                processing_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await processing_task
                await heartbeat_task
                raise PaperTranslationLeaseLost(lease.task_id)
            await processing_task
        finally:
            heartbeat_task.cancel()
            processing_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await heartbeat_task
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await processing_task

    async def _heartbeat_loop(self, lease: PaperTranslationTaskLease) -> None:
        while self._running:
            await asyncio.sleep(self.heartbeat_interval_seconds)
            if not await self.service.heartbeat_translation_task(self.redis, lease):
                raise PaperTranslationLeaseLost(lease.task_id)


_paper_translation_worker: PaperTranslationQueueWorker | None = None


async def init_paper_translation_queue(
    redis_client: Any, *, concurrency: int = 1
) -> None:
    global _paper_translation_worker
    _paper_translation_worker = PaperTranslationQueueWorker(
        service=paper_translation_service,
        redis_client=redis_client,
        concurrency=concurrency,
    )
    await _paper_translation_worker.start()


async def shutdown_paper_translation_queue() -> None:
    global _paper_translation_worker
    if _paper_translation_worker is not None:
        await _paper_translation_worker.stop()
        _paper_translation_worker = None
