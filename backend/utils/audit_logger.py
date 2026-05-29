"""Lightweight file-backed audit logs for user prompts."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from config.settings import settings

logger = logging.getLogger(__name__)

_FILENAME_SAFE_PATTERN = re.compile(r"[^\w.@-]+")
_WRITE_LOCK = asyncio.Lock()
_MAX_FILENAME_PART_LENGTH = 80


def _safe_filename_part(value: object, fallback: str = "unknown") -> str:
    normalized = str(value or "").strip() or fallback
    normalized = _FILENAME_SAFE_PATTERN.sub("_", normalized)
    normalized = normalized.strip("._-") or fallback
    return normalized[:_MAX_FILENAME_PART_LENGTH]


def _repo_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if parent.name == "backend":
            return parent.parent
    return current.parents[1]


def _resolve_log_root() -> Path:
    configured_dir = str(settings.AUDIT_LOG_DIR or "").strip()
    if configured_dir:
        path = Path(configured_dir)
        return path if path.is_absolute() else _repo_root() / path
    return _repo_root() / "logs"


def _build_user_label(user: Any) -> str:
    user_id = _safe_filename_part(getattr(user, "id", None), "unknown-user")
    return f"user-{user_id}"


def _append_line(path: Path, line: str) -> None:
    with path.open("a", encoding="utf-8") as file:
        file.write(line)


async def record_user_prompt_event(
    *,
    event_type: str,
    user: Any,
    prompt: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Append one user prompt audit event.

    Audit logging is best-effort: write failures are logged but never propagated
    to the request path.
    """
    normalized_prompt = str(prompt or "").strip()
    if not normalized_prompt:
        return

    now = datetime.now().astimezone()
    safe_event_type = _safe_filename_part(event_type, "event")
    record = {
        "timestamp": now.isoformat(timespec="seconds"),
        "event_type": safe_event_type,
        "user": {
            "id": str(getattr(user, "id", "") or ""),
            "name": str(getattr(user, "name", "") or ""),
        },
        "prompt": normalized_prompt,
        "metadata": metadata or {},
    }

    try:
        log_dir = _resolve_log_root() / now.strftime("%Y-%m-%d")
        log_path = log_dir / f"{_build_user_label(user)}.jsonl"
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        async with _WRITE_LOCK:
            log_dir.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(_append_line, log_path, line)
    except Exception:
        logger.warning("Failed to write user prompt audit log", exc_info=True)
