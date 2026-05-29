"""Paper translation task orchestration for Creative Workshop."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import html
import json
import logging
import mimetypes
import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse
from uuid import UUID, uuid4

import httpx

from config.database import AsyncSessionLocal
from config.settings import settings
from modules.chat.services.insight_runtime_service import InsightRuntimeService, insight_runtime_service
from modules.model_config.services.model_config_service import ModelConfigService
from utils.mineru_service import MineruService

logger = logging.getLogger(__name__)

PaperTranslationStatus = Literal["queued", "converting", "translating", "completed", "failed"]
QUEUE_PENDING_KEY = "creative_workshop:paper_translation:queue:pending"
QUEUE_PROCESSING_KEY = "creative_workshop:paper_translation:queue:processing"
QUEUE_PROCESSING_HEARTBEAT_KEY = "creative_workshop:paper_translation:queue:processing:heartbeat"
QUEUE_SCHEDULED_KEY = "creative_workshop:paper_translation:queue:scheduled"
WORKER_IDLE_TIMEOUT_SECONDS = 2
WORKER_HEARTBEAT_INTERVAL_SECONDS = 30.0


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


@dataclass
class PaperTranslationQueueItem:
    owner_id: str
    task_id: str
    filename: str
    source_pdf_path: str
    model_name: str | None = None
    attempt: int = 1


def _parse_queue_item(raw_item: Any) -> PaperTranslationQueueItem:
    raw_text = raw_item.decode("utf-8", errors="strict") if isinstance(raw_item, bytes) else str(raw_item)
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


def _parse_utc_datetime(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or ""))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


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
        raise ValueError("Translated markdown artifact must be under /mnt/user-data/outputs")
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


def _parse_markdown_link_destination(markdown: str, start: int) -> tuple[int, int, int] | None:
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
        if alt_end == -1 or alt_end + 1 >= len(markdown) or markdown[alt_end + 1] != "(":
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
    match = re.fullmatch(r"```(?:markdown|md)?\s*\n(?P<body>[\s\S]*?)\n```", text, re.IGNORECASE)
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
        raise RuntimeError("Markdown PDF export requires the 'markdown' package") from exc

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


def _build_pdf_bytes(markdown: str, title: str, *, base_url: str | Path = ".") -> bytes:
    html_content = _markdown_to_html(markdown, title)
    try:
        from weasyprint import HTML
    except ImportError as exc:
        raise RuntimeError("PDF export requires the 'weasyprint' package") from exc
    return HTML(string=html_content, base_url=str(base_url), url_fetcher=_safe_pdf_url_fetcher).write_pdf()


def _safe_pdf_url_fetcher(url: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
    parsed = urlparse(str(url or ""))
    if parsed.scheme in {"http", "https", "ftp"}:
        raise RuntimeError("Remote resources are not allowed during PDF export")

    from weasyprint import default_url_fetcher

    return default_url_fetcher(url, *args, **kwargs)


class PaperTranslationService:
    """Create, run, and serve paper translation tasks."""

    def __init__(
        self,
        *,
        storage_root: str | Path | None = None,
        runtime_service: InsightRuntimeService | None = None,
        model_config_session_factory: Any | None = None,
    ) -> None:
        self.storage_root = Path(storage_root or settings.CREATIVE_WORKSHOP_PAPER_TRANSLATION_STORAGE_DIR)
        self.runtime_service = runtime_service or insight_runtime_service
        self.model_config_session_factory = model_config_session_factory or AsyncSessionLocal
        self._tasks: dict[str, PaperTranslationTask] = {}
        self._lock = asyncio.Lock()

    def _stale_after_seconds(self) -> float:
        mineru_budget = (
            float(settings.CREATIVE_WORKSHOP_PAPER_TRANSLATION_MINERU_POLL_INTERVAL_SECONDS)
            * int(settings.CREATIVE_WORKSHOP_PAPER_TRANSLATION_MINERU_MAX_ATTEMPTS)
        )
        agent_budget = float(settings.CREATIVE_WORKSHOP_PAPER_TRANSLATION_AGENT_TIMEOUT_SECONDS)
        return max(mineru_budget + agent_budget + 300.0, 900.0)

    def _queue_visibility_timeout_seconds(self) -> float:
        return max(float(settings.CREATIVE_WORKSHOP_PAPER_TRANSLATION_QUEUE_VISIBILITY_TIMEOUT_SECONDS), 60.0)

    def _max_retries(self) -> int:
        return max(int(settings.CREATIVE_WORKSHOP_PAPER_TRANSLATION_QUEUE_MAX_RETRIES), 0)

    def _retry_delay_seconds(self, attempt: int) -> float:
        base_delay = max(float(settings.CREATIVE_WORKSHOP_PAPER_TRANSLATION_QUEUE_RETRY_DELAY_SECONDS), 0.0)
        return min(base_delay * max(int(attempt), 1), 300.0)

    @staticmethod
    def _public_error_message(exc: BaseException) -> str:
        message = str(exc or "").strip()
        return message or "论文翻译失败，请重新上传后再试"

    async def promote_due_scheduled_tasks(self, redis_client: Any) -> int:
        now = time.time()
        raw_items = await redis_client.zrangebyscore(QUEUE_SCHEDULED_KEY, min=0, max=now)
        promoted = 0
        for raw_item in raw_items:
            removed = await redis_client.zrem(QUEUE_SCHEDULED_KEY, raw_item)
            if not removed:
                continue
            await redis_client.lpush(QUEUE_PENDING_KEY, raw_item)
            promoted += int(removed)
        return promoted

    async def enqueue_translation_task(self, redis_client: Any, item: PaperTranslationQueueItem) -> None:
        payload = json.dumps(asdict(item), ensure_ascii=False)
        await redis_client.lpush(QUEUE_PENDING_KEY, payload)

    async def recover_processing_queue(self, redis_client: Any) -> int:
        recovered = 0
        now = time.time()
        timeout_seconds = self._queue_visibility_timeout_seconds()
        items = await redis_client.lrange(QUEUE_PROCESSING_KEY, 0, -1)
        for item in items:
            heartbeat = await redis_client.hget(QUEUE_PROCESSING_HEARTBEAT_KEY, item)
            if heartbeat is None:
                await redis_client.hset(QUEUE_PROCESSING_HEARTBEAT_KEY, item, str(now))
                continue
            heartbeat_text = heartbeat.decode("utf-8", errors="ignore") if isinstance(heartbeat, bytes) else str(heartbeat)
            try:
                heartbeat_at = float(heartbeat_text)
            except ValueError:
                heartbeat_at = 0.0
            if now - heartbeat_at < timeout_seconds:
                continue
            removed = await redis_client.lrem(QUEUE_PROCESSING_KEY, 1, item)
            if removed:
                await redis_client.hdel(QUEUE_PROCESSING_HEARTBEAT_KEY, item)
                if self._max_retries() == 0:
                    with contextlib.suppress(Exception):
                        parsed = _parse_queue_item(item)
                        await self.mark_task_failed(
                            owner_id=parsed.owner_id,
                            task_id=parsed.task_id,
                            error="论文翻译任务已中断，请重新上传后再试",
                        )
                else:
                    await redis_client.lpush(QUEUE_PENDING_KEY, item)
                recovered += int(removed)
        if recovered:
            logger.info("Recovered %s paper translation queue item(s)", recovered)
        return recovered

    async def dequeue_translation_task(self, redis_client: Any) -> tuple[str, PaperTranslationQueueItem] | None:
        raw_item = await redis_client.brpoplpush(
            QUEUE_PENDING_KEY,
            QUEUE_PROCESSING_KEY,
            timeout=WORKER_IDLE_TIMEOUT_SECONDS,
        )
        if raw_item is None:
            return None
        try:
            return raw_item, _parse_queue_item(raw_item)
        except Exception as exc:
            logger.warning("Dropping invalid paper translation queue item: %s", exc)
            await redis_client.lrem(QUEUE_PROCESSING_KEY, 1, raw_item)
            await redis_client.hdel(QUEUE_PROCESSING_HEARTBEAT_KEY, raw_item)
            return None

    async def heartbeat_translation_task(self, redis_client: Any, raw_item: Any) -> None:
        await redis_client.hset(QUEUE_PROCESSING_HEARTBEAT_KEY, raw_item, str(time.time()))
        try:
            item = _parse_queue_item(raw_item)
        except Exception:
            return
        with contextlib.suppress(Exception):
            await self._update_task(owner_id=item.owner_id, task_id=item.task_id)

    async def acknowledge_translation_task(self, redis_client: Any, raw_item: Any) -> None:
        await redis_client.lrem(QUEUE_PROCESSING_KEY, 1, raw_item)
        await redis_client.hdel(QUEUE_PROCESSING_HEARTBEAT_KEY, raw_item)

    async def requeue_processing_task(self, redis_client: Any, raw_item: Any) -> bool:
        removed = await redis_client.lrem(QUEUE_PROCESSING_KEY, 1, raw_item)
        await redis_client.hdel(QUEUE_PROCESSING_HEARTBEAT_KEY, raw_item)
        if not removed:
            return False
        await redis_client.lpush(QUEUE_PENDING_KEY, raw_item)
        return True

    async def retry_translation_task(
        self,
        redis_client: Any,
        *,
        raw_item: Any,
        item: PaperTranslationQueueItem,
    ) -> bool:
        if item.attempt > self._max_retries():
            await self.acknowledge_translation_task(redis_client, raw_item)
            return False
        retry_item = PaperTranslationQueueItem(
            owner_id=item.owner_id,
            task_id=item.task_id,
            filename=item.filename,
            source_pdf_path=item.source_pdf_path,
            model_name=item.model_name,
            attempt=item.attempt + 1,
        )
        retry_payload = json.dumps(asdict(retry_item), ensure_ascii=False)
        retry_at = time.time() + self._retry_delay_seconds(item.attempt)
        pipeline = redis_client.pipeline(transaction=True)
        pipeline.zadd(QUEUE_SCHEDULED_KEY, {retry_payload: retry_at})
        pipeline.lrem(QUEUE_PROCESSING_KEY, 1, raw_item)
        pipeline.hdel(QUEUE_PROCESSING_HEARTBEAT_KEY, raw_item)
        await pipeline.execute()
        await self._update_task(
            owner_id=item.owner_id,
            task_id=item.task_id,
            status="queued",
            error=None,
        )
        return True

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

    async def get_task(self, *, owner_id: str, task_id: str) -> PaperTranslationTask | None:
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                task = self._load_task(owner_id=owner_id, task_id=task_id)
                if task is not None:
                    self._tasks[task_id] = task
            if task is None or task.owner_id != str(owner_id):
                return None
            task = self._mark_stale_task_if_needed(task)
            return task

    async def get_latest_active_task(self, *, owner_id: str) -> PaperTranslationTask | None:
        active_statuses = {"queued", "converting", "translating"}
        candidates: dict[str, PaperTranslationTask] = {}

        async with self._lock:
            for task in self._tasks.values():
                if task.owner_id == str(owner_id) and task.status in active_statuses:
                    candidates[task.task_id] = task

            owner_dir = self._owner_dir(owner_id)
            if owner_dir.exists():
                for manifest_path in owner_dir.glob("*/task.json"):
                    task_id = manifest_path.parent.name
                    if task_id in candidates:
                        continue
                    task = self._load_task(owner_id=owner_id, task_id=task_id)
                    if task is not None and task.owner_id == str(owner_id) and task.status in active_statuses:
                        candidates[task.task_id] = task

        def sort_key(task: PaperTranslationTask) -> datetime:
            return (
                _parse_utc_datetime(task.updated_at)
                or _parse_utc_datetime(task.created_at)
                or datetime.min.replace(tzinfo=timezone.utc)
            )

        # Refresh outside the lock because get_task also acquires it.
        for task in sorted(candidates.values(), key=sort_key, reverse=True):
            fresh_task = await self.get_task(owner_id=owner_id, task_id=task.task_id)
            if fresh_task is not None and fresh_task.status in active_statuses:
                return fresh_task
        return None

    def source_pdf_path(self, *, owner_id: str, task_id: str) -> Path:
        return self._task_dir(owner_id, task_id) / "source.pdf"

    async def attach_source_pdf(self, *, owner_id: str, task_id: str, source_pdf_path: Path) -> PaperTranslationTask:
        resolved_path = self._resolve_task_file(
            owner_id=owner_id,
            task_id=task_id,
            stored_path=str(source_pdf_path),
            fallback_name="source.pdf",
        )
        return await self._update_task(owner_id=owner_id, task_id=task_id, source_pdf_path=str(resolved_path))

    async def mark_task_failed(self, *, owner_id: str, task_id: str, error: str) -> PaperTranslationTask:
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
    ) -> None:
        try:
            await self._update_task(owner_id=owner_id, task_id=task_id, status="converting", error=None)
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
            )
            source_path = self._write_task_text(owner_id, task_id, "source.md", source.markdown)
            if source.assets:
                self._write_task_assets(owner_id, task_id, source.assets)
            await self._update_task(
                owner_id=owner_id,
                task_id=task_id,
                source_markdown_path=str(source_path),
                status="translating",
            )

            translated_markdown = await self._translate_markdown_with_agent(
                owner_id=owner_id,
                task_id=task_id,
                filename=filename,
                markdown=source.markdown,
            )
            normalized_translation = _remove_mermaid_diagram_blocks(_strip_markdown_code_fence(translated_markdown))
            if not normalized_translation.strip():
                raise RuntimeError("Agent did not return translated markdown")

            translated_path = self._write_task_text(owner_id, task_id, "translation.zh.md", normalized_translation)
            await self._update_task(
                owner_id=owner_id,
                task_id=task_id,
                translated_markdown_path=str(translated_path),
                translated_pdf_path=None,
                status="completed",
                error=None,
            )
        except Exception as exc:
            logger.exception("Paper translation task failed: task_id=%s owner=%s", task_id, owner_id)
            if mark_failed_on_error:
                await self._update_task(
                    owner_id=owner_id,
                    task_id=task_id,
                    status="failed",
                    error=str(exc) or "论文翻译失败",
                )
                return
            raise

    async def get_translated_markdown(
        self,
        *,
        owner_id: str,
        task_id: str,
        inline_assets: bool = False,
    ) -> tuple[str, str]:
        task = await self.get_task(owner_id=owner_id, task_id=task_id)
        if task is None or task.status != "completed" or not task.translated_markdown_path:
            raise FileNotFoundError("Translated markdown is not available")
        path = self._resolve_task_file(
            owner_id=owner_id,
            task_id=task_id,
            stored_path=task.translated_markdown_path,
            fallback_name="translation.zh.md",
        )
        markdown = _remove_mermaid_diagram_blocks(path.read_text(encoding="utf-8"))
        if inline_assets:
            markdown = self._inline_markdown_assets(owner_id=owner_id, task_id=task_id, markdown=markdown)
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

    async def get_translated_pdf(self, *, owner_id: str, task_id: str) -> tuple[str, bytes]:
        task = await self.get_task(owner_id=owner_id, task_id=task_id)
        if task is None or task.status != "completed" or not task.translated_markdown_path:
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
        markdown = _remove_mermaid_diagram_blocks(markdown_path.read_text(encoding="utf-8"))
        pdf_ready_markdown = self._rewrite_markdown_asset_paths_for_local_pdf(
            owner_id=owner_id,
            task_id=task_id,
            markdown=markdown,
        )
        pdf_bytes = _build_pdf_bytes(
            pdf_ready_markdown,
            task.filename,
            base_url=self._task_dir(owner_id, task_id),
        )
        pdf_path.write_bytes(pdf_bytes)
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
    ) -> PaperTranslationSource:
        result = await MineruService.convert_document(file_data, filename)
        batch_id = str(result.get("batch_id") or result.get("task_id") or "").strip()
        if not batch_id:
            raise RuntimeError("MinerU did not return a batch_id")
        await self._update_task(owner_id=owner_id, task_id=task_id, mineru_batch_id=batch_id)

        poll_interval = max(float(settings.CREATIVE_WORKSHOP_PAPER_TRANSLATION_MINERU_POLL_INTERVAL_SECONDS), 0.5)
        max_attempts = max(int(settings.CREATIVE_WORKSHOP_PAPER_TRANSLATION_MINERU_MAX_ATTEMPTS), 1)

        for attempt in range(max_attempts):
            task_status = await MineruService.get_task_status(batch_id)
            status_value = str(task_status.get("status") or "pending")
            if status_value == "completed":
                result = await MineruService.get_content_with_assets(batch_id)
                return PaperTranslationSource(markdown=result.markdown, assets=result.assets)
            if status_value == "failed":
                raise RuntimeError(str(task_status.get("message") or "MinerU task failed"))
            if attempt % 6 == 0:
                logger.info("MinerU paper translation task pending: task=%s batch=%s status=%s", task_id, batch_id, status_value)
            await asyncio.sleep(poll_interval)

        raise TimeoutError("MinerU task timeout")

    async def _translate_markdown_with_agent(
        self,
        *,
        owner_id: str,
        task_id: str,
        filename: str,
        markdown: str,
    ) -> str:
        task = await self.get_task(owner_id=owner_id, task_id=task_id)
        if task is None:
            raise FileNotFoundError("Task not found")
        if not _is_uuid_text(task.thread_id):
            task = await self._update_task(owner_id=owner_id, task_id=task_id, thread_id=str(uuid4()))
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
        uploaded_filename = str(uploaded.get("filename") or source_filename).strip() or source_filename
        uploaded_size = int(uploaded.get("size") or len(markdown.encode("utf-8")))
        uploaded_files = [
            {
                "filename": uploaded_filename,
                "size": uploaded_size,
                "path": str(uploaded.get("virtual_path") or f"/mnt/user-data/uploads/{uploaded_filename}"),
            }
        ]
        run_request = self.runtime_service.build_run_request_template(
            thread_id=normalized_thread_id,
            assistant_id=assistant_id,
            model_name=model_name,
            thinking_enabled=False,
            is_plan_mode=False,
            subagent_enabled=False,
        )
        if dynamic_model_token:
            context_payload = run_request.get("context")
            if not isinstance(context_payload, dict):
                context_payload = {}
                run_request["context"] = context_payload
            context_payload["dynamic_model_token"] = dynamic_model_token
        run_request["input"] = {
            "messages": [
                {
                    "role": "user",
                    "content": self._build_translation_prompt(uploaded_filename),
                    "additional_kwargs": {
                        "files": uploaded_files
                    },
                }
            ]
        }

        url = f"{self.runtime_service.langgraph_url}{self.runtime_service.build_run_stream_path(normalized_thread_id)}"
        timeout_seconds = max(float(settings.CREATIVE_WORKSHOP_PAPER_TRANSLATION_AGENT_TIMEOUT_SECONDS), 60.0)
        timeout = httpx.Timeout(timeout_seconds, connect=20.0)
        last_values_text = ""
        tuple_text_parts: list[str] = []
        values_artifact_paths: list[str] = []

        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST",
                url,
                headers={"Accept": "text/event-stream", "Content-Type": "application/json"},
                json=run_request,
            ) as response:
                response.raise_for_status()
                async for event_name, payload in self._iter_sse_events(response):
                    if event_name == "error":
                        error_message = self._extract_error_message(payload)
                        logger.error(
                            "Paper translation agent stream error: task_id=%s thread_id=%s payload=%r",
                            task_id,
                            normalized_thread_id,
                            payload,
                        )
                        raise RuntimeError(error_message)
                    if event_name == "values":
                        values_text = _last_ai_text_from_values(payload)
                        if values_text:
                            last_values_text = values_text
                        values_artifact_paths.extend(_artifact_paths_from_values(payload))
                        continue
                    if event_name in {"messages", "messages-tuple"}:
                        text = _ai_text_from_message_payload(payload)
                        if text:
                            tuple_text_parts.append(text)

        if last_values_text.strip():
            artifact_path = _extract_translated_markdown_path(last_values_text)
        else:
            artifact_path = _extract_translated_markdown_path("".join(tuple_text_parts).strip())
        if not artifact_path and values_artifact_paths:
            markdown_artifacts = [
                path for path in values_artifact_paths
                if path.strip().lower().endswith(".md")
            ]
            artifact_path = markdown_artifacts[-1] if markdown_artifacts else values_artifact_paths[-1]
        if not artifact_path:
            raise RuntimeError("Agent did not return translated markdown path")
        artifact_path = _normalize_translated_markdown_artifact_path(artifact_path)
        translated_markdown = await self.runtime_service.download_thread_artifact_text(
            normalized_thread_id,
            artifact_path,
        )
        if not translated_markdown.strip():
            raise RuntimeError("Agent returned an empty translated markdown artifact")
        return translated_markdown

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
        return resolved_model_name, str(dynamic_model_token).strip() if dynamic_model_token else None

    @staticmethod
    def _build_translation_prompt(uploaded_filename: str) -> str:
        return (
            f"请使用 `paper-translation` skill 翻译上传的 Markdown 论文文件 `{uploaded_filename}`。\n\n"
            "必须严格执行：\n"
            "1. 先读取并遵循 `paper-translation` skill。\n"
            "2. 使用 read_file 读取上传的 Markdown 文件，不要要求用户重新提供内容。\n"
            "3. 在 `/mnt/user-data/outputs` 下创建新的 `.zh.md` 译文文件。\n"
            "4. 按章节逐段翻译，并将内容 append 到该译文文件。\n"
            "5. 参考文献部分不翻译，保持原文。\n"
            "6. 完成后调用 present_files 发布该 Markdown 文件。\n"
            "7. 最终回复不要输出完整译文，只返回 JSON，格式必须为："
            "{\"translated_markdown_path\":\"/mnt/user-data/outputs/文件名.zh.md\"}"
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
                        yield PaperTranslationService._parse_sse_event(event_name, data_lines)
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

    @staticmethod
    def _extract_error_message(payload: Any) -> str:
        if isinstance(payload, dict):
            return str(payload.get("message") or payload.get("error") or "Insight runtime error")
        return str(payload or "Insight runtime error")

    async def _update_task(self, *, owner_id: str, task_id: str, **updates: Any) -> PaperTranslationTask:
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                task = self._load_task(owner_id=owner_id, task_id=task_id)
            if task is None or task.owner_id != str(owner_id):
                raise FileNotFoundError("Task not found")
            for key, value in updates.items():
                if hasattr(task, key):
                    setattr(task, key, value)
            if "updated_at" not in updates:
                task.updated_at = _utc_now()
            self._tasks[task_id] = task
            self._persist_task(task)
            return task

    def _task_dir(self, owner_id: str, task_id: str) -> Path:
        safe_task = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(task_id)).strip("._") or "task"
        return self._owner_dir(owner_id) / safe_task

    def _owner_dir(self, owner_id: str) -> Path:
        safe_owner = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(owner_id)).strip("._") or "owner"
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

    def _write_task_assets(self, owner_id: str, task_id: str, assets: dict[str, bytes]) -> None:
        assets_dir = self._assets_dir(owner_id, task_id)
        assets_dir.mkdir(parents=True, exist_ok=True)
        for asset_path, content in assets.items():
            normalized = _normalize_asset_path(asset_path)
            if not normalized:
                logger.warning("Skipping unsafe MinerU asset path: %r", asset_path)
                continue
            destination = assets_dir / normalized
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)

    def _resolve_asset_file(self, *, owner_id: str, task_id: str, asset_path: str) -> Path | None:
        normalized = _normalize_asset_path(asset_path)
        if not normalized:
            return None
        assets_dir = self._assets_dir(owner_id, task_id).resolve()
        candidate = (assets_dir / normalized).resolve()
        try:
            candidate.relative_to(assets_dir)
        except ValueError:
            return None
        if candidate.is_file():
            return candidate
        return None

    def _inline_markdown_assets(self, *, owner_id: str, task_id: str, markdown: str) -> str:
        def replace(image_path: str) -> str:
            image_path = image_path.strip()
            if re.match(r"^(?:https?:|data:|blob:|mailto:)", image_path, re.IGNORECASE):
                return image_path
            asset_file = self._resolve_asset_file(owner_id=owner_id, task_id=task_id, asset_path=image_path)
            if asset_file is None:
                return image_path
            mime_type = mimetypes.guess_type(asset_file.name)[0] or "application/octet-stream"
            encoded = base64.b64encode(asset_file.read_bytes()).decode("ascii")
            return f"data:{mime_type};base64,{encoded}"

        return _replace_markdown_image_destinations(markdown, replace)

    def _rewrite_markdown_asset_paths_for_local_pdf(self, *, owner_id: str, task_id: str, markdown: str) -> str:
        def replace(image_path: str) -> str:
            image_path = image_path.strip()
            if re.match(r"^(?:https?:|data:|blob:|mailto:)", image_path, re.IGNORECASE):
                return image_path
            asset_file = self._resolve_asset_file(owner_id=owner_id, task_id=task_id, asset_path=image_path)
            if asset_file is None:
                return image_path
            relative = asset_file.resolve().relative_to(self._task_dir(owner_id, task_id).resolve())
            return relative.as_posix()

        return _replace_markdown_image_destinations(markdown, replace)

    def _task_manifest_path(self, owner_id: str, task_id: str) -> Path:
        return self._task_dir(owner_id, task_id) / "task.json"

    def _persist_task(self, task: PaperTranslationTask) -> None:
        task_dir = self._task_dir(task.owner_id, task.task_id)
        task_dir.mkdir(parents=True, exist_ok=True)
        self._task_manifest_path(task.owner_id, task.task_id).write_text(
            json.dumps(asdict(task), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _load_task(self, *, owner_id: str, task_id: str) -> PaperTranslationTask | None:
        path = self._task_manifest_path(owner_id, task_id)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return PaperTranslationTask(**payload)
        except Exception:
            logger.warning("Failed to load paper translation task manifest: %s", path, exc_info=True)
            return None

    def _write_task_text(self, owner_id: str, task_id: str, filename: str, content: str) -> Path:
        task_dir = self._task_dir(owner_id, task_id)
        task_dir.mkdir(parents=True, exist_ok=True)
        path = task_dir / filename
        path.write_text(content, encoding="utf-8")
        return path

    def _mark_stale_task_if_needed(self, task: PaperTranslationTask) -> PaperTranslationTask:
        if task.status not in {"converting", "translating"}:
            return task
        updated_at = _parse_utc_datetime(task.updated_at)
        if updated_at is None:
            return task
        age_seconds = (datetime.now(timezone.utc) - updated_at).total_seconds()
        if age_seconds < self._stale_after_seconds():
            return task

        task.status = "failed"
        task.error = "论文翻译任务已中断，请重新上传后再试"
        task.updated_at = _utc_now()
        self._tasks[task.task_id] = task
        self._persist_task(task)
        return task


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
        self._running = False
        self._tasks: list[asyncio.Task] = []

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        await self.service.recover_processing_queue(self.redis)
        await self.service.promote_due_scheduled_tasks(self.redis)
        self._tasks = [
            asyncio.create_task(self._consume_loop(index), name=f"paper-translation-worker-{index}")
            for index in range(self.concurrency)
        ]
        logger.info("Paper translation queue worker started with concurrency=%s", self.concurrency)

    async def stop(self) -> None:
        self._running = False
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks = []
        logger.info("Paper translation queue worker stopped")

    async def _consume_loop(self, worker_index: int) -> None:
        while self._running:
            raw_item = None
            item: PaperTranslationQueueItem | None = None
            heartbeat_task: asyncio.Task | None = None
            try:
                await self.service.promote_due_scheduled_tasks(self.redis)
                await self.service.recover_processing_queue(self.redis)
                dequeued = await self.service.dequeue_translation_task(self.redis)
                if dequeued is None:
                    continue
                raw_item, item = dequeued
                await self.service.heartbeat_translation_task(self.redis, raw_item)
                heartbeat_task = asyncio.create_task(self._heartbeat_loop(raw_item))
                await self.service.run_translation_task(
                    owner_id=item.owner_id,
                    task_id=item.task_id,
                    filename=item.filename,
                    source_pdf_path=item.source_pdf_path,
                    mark_failed_on_error=False,
                )
                await self.service.acknowledge_translation_task(self.redis, raw_item)
                raw_item = None
            except asyncio.CancelledError:
                if raw_item is not None:
                    with contextlib.suppress(Exception):
                        await self.service.requeue_processing_task(self.redis, raw_item)
                break
            except Exception as exc:
                if raw_item is not None and item is not None:
                    retried = await self.service.retry_translation_task(self.redis, raw_item=raw_item, item=item)
                    if retried:
                        logger.exception(
                            "Paper translation worker %s scheduled retry for task_id=%s attempt=%s",
                            worker_index,
                            item.task_id,
                            item.attempt + 1,
                        )
                        raw_item = None
                        continue
                    with contextlib.suppress(Exception):
                        await self.service.mark_task_failed(
                            owner_id=item.owner_id,
                            task_id=item.task_id,
                            error=self.service._public_error_message(exc),
                        )
                    raw_item = None
                elif raw_item is not None:
                    with contextlib.suppress(Exception):
                        await self.service.acknowledge_translation_task(self.redis, raw_item)
                    raw_item = None
                logger.exception("Paper translation worker %s failed while processing queue item", worker_index)
                await asyncio.sleep(2.0)
            finally:
                if heartbeat_task is not None:
                    heartbeat_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await heartbeat_task

    async def _heartbeat_loop(self, raw_item: Any) -> None:
        while self._running:
            await asyncio.sleep(WORKER_HEARTBEAT_INTERVAL_SECONDS)
            await self.service.heartbeat_translation_task(self.redis, raw_item)


_paper_translation_worker: PaperTranslationQueueWorker | None = None


async def init_paper_translation_queue(redis_client: Any, *, concurrency: int = 1) -> None:
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
