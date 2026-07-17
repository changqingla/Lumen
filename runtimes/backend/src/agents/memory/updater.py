"""Read, update, and atomically persist tenant-scoped long-term memory."""

import fcntl
import json
import logging
import os
import re
import tempfile
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.agents.memory.prompt import (
    MEMORY_UPDATE_PROMPT,
    format_conversation_for_update,
)
from src.agents.memory.scope import (
    normalize_agent_name,
    normalize_memory_scope,
)
from src.config.memory_config import get_memory_config
from src.config.paths import get_paths
from src.models import create_chat_model

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _get_memory_storage_root() -> Path:
    """Resolve the new scoped root without ever addressing legacy files."""
    base_dir = get_paths().base_dir.resolve()
    config = get_memory_config()
    if config.storage_path:
        configured = Path(config.storage_path)
        if configured.is_absolute():
            configured = configured.resolve()
        else:
            configured = (base_dir / configured).resolve()
            if not configured.is_relative_to(base_dir):
                raise ValueError("relative memory storage_path escapes LUMEN_HOME")

        # ``storage_path`` historically named a single global JSON file. Put
        # scoped data beside it under a distinct directory; never migrate or
        # inject that legacy file automatically.
        if configured.suffix:
            return configured.parent / f"{configured.name}.scoped"
        return configured
    return base_dir / "memories"


def _get_memory_file_path(
    memory_scope: str,
    agent_name: str | None = None,
) -> Path:
    """Return a traversal-safe path partitioned by scope and optional agent."""
    scope = normalize_memory_scope(memory_scope)
    normalized_agent = normalize_agent_name(agent_name)
    root = _get_memory_storage_root().resolve()
    if normalized_agent is None:
        path = root / scope / "memory.json"
    else:
        path = root / scope / "agents" / normalized_agent / "memory.json"

    if not path.parent.resolve().is_relative_to(root):
        raise ValueError("resolved memory path escapes the scoped storage root")
    return path


def _create_empty_memory() -> dict[str, Any]:
    """创建空的记忆数据结构。"""
    return {
        "version": "1.0",
        "lastUpdated": _utc_now_iso(),
        "user": {
            "workContext": {"summary": "", "updatedAt": ""},
            "personalContext": {"summary": "", "updatedAt": ""},
            "topOfMind": {"summary": "", "updatedAt": ""},
        },
        "history": {
            "recentMonths": {"summary": "", "updatedAt": ""},
            "earlierContext": {"summary": "", "updatedAt": ""},
            "longTermBackground": {"summary": "", "updatedAt": ""},
        },
        "facts": [],
    }


MemoryCacheKey = tuple[Path, str, str | None]
FileSignature = tuple[int, int, int] | None

# Cache entries are scoped by both tenant and agent. Deep copies are returned
# so one request cannot mutate data observed by another request.
_memory_cache: dict[MemoryCacheKey, tuple[dict[str, Any], FileSignature]] = {}
_cache_lock = threading.RLock()
_path_locks: dict[Path, threading.RLock] = {}
_path_locks_guard = threading.Lock()


def _file_signature(path: Path) -> FileSignature:
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_mtime_ns, stat.st_size, stat.st_ino


def _local_path_lock(path: Path) -> threading.RLock:
    with _path_locks_guard:
        return _path_locks.setdefault(path, threading.RLock())


@contextmanager
def _memory_file_lock(path: Path, *, exclusive: bool) -> Iterator[None]:
    """Coordinate updates across threads and Runtime worker processes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    local_lock = _local_path_lock(path)
    with local_lock:
        lock_path = path.with_name(f".{path.name}.lock")
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(lock_path, flags, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)


def get_memory_data(
    memory_scope: str | None = None,
    agent_name: str | None = None,
) -> dict[str, Any]:
    """获取记忆数据。

    若记忆文件自上次加载后发生变化，缓存会自动失效，
    以确保返回的数据始终是最新内容。

    参数：
        memory_scope: Backend-issued opaque tenant partition. Missing scope
            disables persistent memory and returns an empty profile.
        agent_name: Optional agent subpartition within the tenant scope.

    返回：
        记忆数据字典。
    """
    scope = normalize_memory_scope(memory_scope, allow_none=True)
    if scope is None:
        return _create_empty_memory()
    normalized_agent = normalize_agent_name(agent_name)
    file_path = _get_memory_file_path(scope, normalized_agent)
    cache_key = (file_path, scope, normalized_agent)

    with _memory_file_lock(file_path, exclusive=False):
        signature = _file_signature(file_path)
        with _cache_lock:
            cached = _memory_cache.get(cache_key)
            if cached is not None and cached[1] == signature:
                return deepcopy(cached[0])

        memory_data = _load_memory_path_unlocked(file_path)
        with _cache_lock:
            _memory_cache[cache_key] = (deepcopy(memory_data), signature)
        return deepcopy(memory_data)


def reload_memory_data(
    memory_scope: str | None = None,
    agent_name: str | None = None,
) -> dict[str, Any]:
    """强制重新加载记忆数据并刷新缓存。

    参数：
        memory_scope: Backend-issued opaque tenant partition.
        agent_name: Optional agent subpartition.

    返回：
        重新加载后的记忆数据字典。
    """
    scope = normalize_memory_scope(memory_scope, allow_none=True)
    if scope is None:
        return _create_empty_memory()
    normalized_agent = normalize_agent_name(agent_name)
    file_path = _get_memory_file_path(scope, normalized_agent)
    cache_key = (file_path, scope, normalized_agent)
    with _memory_file_lock(file_path, exclusive=False):
        memory_data = _load_memory_path_unlocked(file_path)
        signature = _file_signature(file_path)
        with _cache_lock:
            _memory_cache[cache_key] = (deepcopy(memory_data), signature)
        return deepcopy(memory_data)


def _load_memory_path_unlocked(file_path: Path) -> dict[str, Any]:
    """Load a profile while the caller holds the corresponding file lock."""
    if file_path.is_symlink():
        raise ValueError("scoped memory file must not be a symbolic link")
    if not file_path.exists():
        return _create_empty_memory()

    try:
        with file_path.open(encoding="utf-8") as file:
            data = json.load(file)
        if not isinstance(data, dict):
            raise ValueError("memory file root must be an object")
        return data
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        logger.warning("Failed to load scoped memory (%s)", type(exc).__name__)
        return _create_empty_memory()


# 匹配“文件上传事件”描述句，而非一般文件相关工作描述。
# 规则刻意收窄，避免误删合法事实，例如“用户处理 CSV 文件”或“偏好 PDF 导出”。
_UPLOAD_SENTENCE_RE = re.compile(
    r"[^.!?]*\b(?:"
    r"upload(?:ed|ing)?(?:\s+\w+){0,3}\s+(?:file|files?|document|documents?|attachment|attachments?)"
    r"|file\s+upload"
    r"|/mnt/user-data/uploads/"
    r"|/mnt/user-data/knowledge/"
    r"|<uploaded_files>"
    r")[^.!?]*[.!?]?\s*",
    re.IGNORECASE,
)


def _strip_upload_mentions_from_memory(memory_data: dict[str, Any]) -> dict[str, Any]:
    """移除记忆中与上传事件相关的描述。

    上传文件是会话级资源；若将上传事件写入长期记忆，
    后续会话中代理会尝试查找已不存在的文件。
    """
    # 清理 user/history 各分区中的摘要文本
    for section in ("user", "history"):
        section_data = memory_data.get(section, {})
        for _key, val in section_data.items():
            if isinstance(val, dict) and "summary" in val:
                cleaned = _UPLOAD_SENTENCE_RE.sub("", val["summary"]).strip()
                cleaned = re.sub(r"  +", " ", cleaned)
                val["summary"] = cleaned

    # 同时删除描述上传事件的事实项
    facts = memory_data.get("facts", [])
    if facts:
        memory_data["facts"] = [f for f in facts if not _UPLOAD_SENTENCE_RE.search(f.get("content", ""))]

    return memory_data


def _save_memory_path_unlocked(
    memory_data: dict[str, Any],
    *,
    file_path: Path,
    cache_key: MemoryCacheKey,
) -> bool:
    """Atomically publish a profile while the caller holds its file lock."""
    temp_path: Path | None = None
    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        memory_data["lastUpdated"] = _utc_now_iso()
        fd, raw_temp_path = tempfile.mkstemp(
            prefix=f".{file_path.name}.",
            suffix=".tmp",
            dir=file_path.parent,
        )
        temp_path = Path(raw_temp_path)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(memory_data, file, indent=2, ensure_ascii=False)
            file.flush()
            os.fsync(file.fileno())

        os.replace(temp_path, file_path)
        temp_path = None
        directory_fd = os.open(file_path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

        signature = _file_signature(file_path)
        with _cache_lock:
            _memory_cache[cache_key] = (deepcopy(memory_data), signature)
        logger.info("Scoped memory saved")
        return True
    except OSError as exc:
        logger.error("Failed to save scoped memory (%s)", type(exc).__name__)
        return False
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def _save_memory_to_file(
    memory_data: dict[str, Any],
    memory_scope: str | None = None,
    agent_name: str | None = None,
) -> bool:
    """Persist memory only when an explicit valid tenant scope is supplied."""
    scope = normalize_memory_scope(memory_scope, allow_none=True)
    if scope is None:
        return False
    normalized_agent = normalize_agent_name(agent_name)
    file_path = _get_memory_file_path(scope, normalized_agent)
    with _memory_file_lock(file_path, exclusive=True):
        return _save_memory_path_unlocked(
            deepcopy(memory_data),
            file_path=file_path,
            cache_key=(file_path, scope, normalized_agent),
        )


class MemoryUpdater:
    """基于对话上下文调用 LLM 更新记忆。"""

    def __init__(self, model_name: str | None = None):
        """初始化记忆更新器。

        参数：
            model_name: 可选模型名；若为 None，则使用配置或默认模型。
        """
        self._model_name = model_name

    def _get_model(self):
        """获取用于记忆更新的模型实例。"""
        config = get_memory_config()
        model_name = self._model_name or config.model_name
        return create_chat_model(name=model_name, thinking_enabled=False)

    def update_memory(
        self,
        messages: list[Any],
        *,
        memory_scope: str | None,
        thread_id: str | None = None,
        agent_name: str | None = None,
    ) -> bool:
        """根据对话消息更新记忆。

        参数：
            messages: 对话消息列表。
            memory_scope: Required Backend-issued tenant partition. Missing
                scope disables persistence.
            thread_id: 可选线程 ID，用于标记来源。
            agent_name: Optional agent subpartition within the tenant scope.

        返回：
            更新成功返回 True，否则返回 False。
        """
        config = get_memory_config()
        if not config.enabled:
            return False

        scope = normalize_memory_scope(memory_scope, allow_none=True)
        if scope is None:
            return False
        normalized_agent = normalize_agent_name(agent_name)

        if not messages:
            return False

        file_path = _get_memory_file_path(scope, normalized_agent)
        cache_key = (file_path, scope, normalized_agent)
        try:
            # The exclusive lock spans read -> LLM merge -> atomic replace so
            # workers serving the same scope cannot overwrite each other from
            # stale snapshots. Different scopes use different lock files.
            with _memory_file_lock(file_path, exclusive=True):
                current_memory = _load_memory_path_unlocked(file_path)
                conversation_text = format_conversation_for_update(messages)
                if not conversation_text.strip():
                    return False

                prompt = MEMORY_UPDATE_PROMPT.format(
                    current_memory=json.dumps(current_memory, indent=2),
                    conversation=conversation_text,
                )
                model = self._get_model()
                response = model.invoke(prompt)
                response_text = str(response.content).strip()

                if response_text.startswith("```"):
                    lines = response_text.split("\n")
                    response_text = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])

                update_data = json.loads(response_text)
                updated_memory = self._apply_updates(
                    current_memory,
                    update_data,
                    thread_id,
                )
                updated_memory = _strip_upload_mentions_from_memory(updated_memory)
                return _save_memory_path_unlocked(
                    updated_memory,
                    file_path=file_path,
                    cache_key=cache_key,
                )

        except json.JSONDecodeError:
            logger.warning("Failed to parse model response for scoped memory update")
            return False
        except Exception as exc:
            logger.error("Scoped memory update failed (%s)", type(exc).__name__)
            return False

    def _apply_updates(
        self,
        current_memory: dict[str, Any],
        update_data: dict[str, Any],
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        """将 LLM 产生的增量更新应用到当前记忆。

        参数：
            current_memory: 当前记忆数据。
            update_data: 来自 LLM 的更新内容。
            thread_id: 可选线程 ID，用于来源追踪。

        返回：
            更新后的记忆数据。
        """
        config = get_memory_config()
        now = _utc_now_iso()

        # 更新 user 分区
        user_updates = update_data.get("user", {})
        for section in ["workContext", "personalContext", "topOfMind"]:
            section_data = user_updates.get(section, {})
            if section_data.get("shouldUpdate") and section_data.get("summary"):
                current_memory["user"][section] = {
                    "summary": section_data["summary"],
                    "updatedAt": now,
                }

        # 更新 history 分区
        history_updates = update_data.get("history", {})
        for section in ["recentMonths", "earlierContext", "longTermBackground"]:
            section_data = history_updates.get(section, {})
            if section_data.get("shouldUpdate") and section_data.get("summary"):
                current_memory["history"][section] = {
                    "summary": section_data["summary"],
                    "updatedAt": now,
                }

        # 删除指定事实
        facts_to_remove = set(update_data.get("factsToRemove", []))
        if facts_to_remove:
            current_memory["facts"] = [f for f in current_memory.get("facts", []) if f.get("id") not in facts_to_remove]

        # 新增事实
        new_facts = update_data.get("newFacts", [])
        for fact in new_facts:
            confidence = fact.get("confidence", 0.5)
            if confidence >= config.fact_confidence_threshold:
                fact_entry = {
                    "id": f"fact_{uuid.uuid4().hex[:8]}",
                    "content": fact.get("content", ""),
                    "category": fact.get("category", "context"),
                    "confidence": confidence,
                    "createdAt": now,
                    "source": thread_id or "unknown",
                }
                current_memory["facts"].append(fact_entry)

        # 强制执行事实数量上限
        if len(current_memory["facts"]) > config.max_facts:
            # 按置信度排序，仅保留前 N 条
            current_memory["facts"] = sorted(
                current_memory["facts"],
                key=lambda f: f.get("confidence", 0),
                reverse=True,
            )[: config.max_facts]

        return current_memory
