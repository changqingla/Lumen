"""Lightweight file-backed audit logs for user prompts."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import re
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from config.settings import settings

logger = logging.getLogger(__name__)

_FILENAME_SAFE_PATTERN = re.compile(r"[^\w.@-]+")
_WRITE_LOCK = asyncio.Lock()
_MAX_FILENAME_PART_LENGTH = 80
_SENSITIVE_METADATA_KEY_PARTS = (
    "api_key",
    "authorization",
    "cookie",
    "credential",
    "password",
    "secret",
    "token",
)
_last_pruned_on: date | None = None


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


def _prompt_fingerprint(prompt: str) -> str:
    key = str(settings.SECRET_KEY).encode("utf-8")
    return hmac.new(key, prompt.encode("utf-8"), hashlib.sha256).hexdigest()


def _sanitize_metadata(value: Any, *, key: str = "") -> Any:
    normalized_key = key.lower()
    if normalized_key and any(part in normalized_key for part in _SENSITIVE_METADATA_KEY_PARTS):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {
            str(item_key): _sanitize_metadata(item_value, key=str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize_metadata(item) for item in value]
    return value


def _prune_expired_logs(log_root: Path, today: date, retention_days: int) -> None:
    cutoff = today - timedelta(days=max(1, retention_days))
    if not log_root.exists():
        return
    for child in log_root.iterdir():
        if not child.is_dir():
            continue
        try:
            directory_date = datetime.strptime(child.name, "%Y-%m-%d").date()
        except ValueError:
            continue
        if directory_date < cutoff:
            shutil.rmtree(child)


def _write_audit_line(
    *,
    log_root: Path,
    log_dir: Path,
    log_path: Path,
    line: str,
    today: date,
    retention_days: int,
    prune: bool,
) -> None:
    if prune:
        _prune_expired_logs(log_root, today, retention_days)
    log_dir.mkdir(parents=True, exist_ok=True)
    _append_line(log_path, line)


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
    record: dict[str, Any] = {
        "timestamp": now.isoformat(timespec="seconds"),
        "event_type": safe_event_type,
        "user": {
            "id": str(getattr(user, "id", "") or ""),
        },
        "prompt_length": len(normalized_prompt),
        "prompt_fingerprint": _prompt_fingerprint(normalized_prompt),
        "metadata": _sanitize_metadata(metadata or {}),
    }
    if settings.AUDIT_LOG_INCLUDE_PROMPTS:
        record["prompt"] = normalized_prompt

    try:
        global _last_pruned_on
        log_root = _resolve_log_root()
        log_dir = log_root / now.strftime("%Y-%m-%d")
        log_path = log_dir / f"{_build_user_label(user)}.jsonl"
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        async with _WRITE_LOCK:
            should_prune = _last_pruned_on != now.date()
            await asyncio.to_thread(
                _write_audit_line,
                log_root=log_root,
                log_dir=log_dir,
                log_path=log_path,
                line=line,
                today=now.date(),
                retention_days=settings.AUDIT_LOG_RETENTION_DAYS,
                prune=should_prune,
            )
            if should_prune:
                _last_pruned_on = now.date()
    except Exception as exc:
        logger.warning(
            "Failed to write user prompt audit log (error_type=%s)",
            type(exc).__name__,
        )
