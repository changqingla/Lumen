"""通道存储（ChannelStore）：持久化 IM 会话到 lumen 线程的映射。"""

from __future__ import annotations

import json
import logging
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class ChannelStore:
    """
    磁盘数据结构::

        {
            "<channel_name>:<chat_id>": {
                "thread_id": "<uuid>",
                "user_id": "<platform_user>",
                "created_at": 1700000000.0,
                "updated_at": 1700000000.0
            },
            ...
        }

    该存储设计保持简单：使用单个 JSON 文件，并在每次变更时以原子方式重写。
    若生产环境并发较高，可替换为数据库后端。
    """

    def __init__(self, path: str | Path | None = None) -> None:
        if path is None:
            from src.config.paths import get_paths

            path = Path(get_paths().base_dir) / "channels" / "store.json"
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._data: dict[str, dict[str, Any]] = self._load()
        self._lock = threading.Lock()

    # -- 持久化 -------------------------------------------------------------

    def _load(self) -> dict[str, dict[str, Any]]:
        if self._path.exists():
            try:
                return json.loads(self._path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                logger.warning("Corrupt channel store; starting fresh")
        return {}

    def _save(self) -> None:
        fd = tempfile.NamedTemporaryFile(
            mode="w",
            dir=self._path.parent,
            suffix=".tmp",
            delete=False,
        )
        try:
            json.dump(self._data, fd, indent=2)
            fd.close()
            Path(fd.name).replace(self._path)
        except BaseException:
            fd.close()
            Path(fd.name).unlink(missing_ok=True)
            raise

    # -- 键辅助 -------------------------------------------------------------

    @staticmethod
    def _key(channel_name: str, chat_id: str, topic_id: str | None = None) -> str:
        if topic_id:
            return f"{channel_name}:{chat_id}:{topic_id}"
        return f"{channel_name}:{chat_id}"

    # -- 对外 API -----------------------------------------------------------

    def get_thread_id(self, channel_name: str, chat_id: str, topic_id: str | None = None) -> str | None:
        """查询指定 IM 会话/主题对应的 lumen thread_id。"""
        entry = self._data.get(self._key(channel_name, chat_id, topic_id))
        return entry["thread_id"] if entry else None

    def set_thread_id(
        self,
        channel_name: str,
        chat_id: str,
        thread_id: str,
        *,
        topic_id: str | None = None,
        user_id: str = "",
    ) -> None:
        """为 IM 会话/主题创建或更新映射。"""
        with self._lock:
            key = self._key(channel_name, chat_id, topic_id)
            now = time.time()
            existing = self._data.get(key)
            self._data[key] = {
                "thread_id": thread_id,
                "user_id": user_id,
                "created_at": existing["created_at"] if existing else now,
                "updated_at": now,
            }
            self._save()

    def remove(self, channel_name: str, chat_id: str, topic_id: str | None = None) -> bool:
        """
        若提供 ``topic_id``，仅删除该会话/主题映射。
        若省略 ``topic_id``，则删除所有键以
        ``"<channel_name>:<chat_id>"`` 开头的映射（含主题子键）。

        若至少删除一条映射则返回 True。
        """
        with self._lock:
            # 删除指定会话/主题映射
            if topic_id is not None:
                key = self._key(channel_name, chat_id, topic_id)
                if key in self._data:
                    del self._data[key]
                    self._save()
                    return True
                return False

            # 删除该 channel/chat_id 下全部映射（基础键与主题子键）
            prefix = self._key(channel_name, chat_id)
            keys_to_delete = [k for k in self._data if k == prefix or k.startswith(prefix + ":")]
            if not keys_to_delete:
                return False

            for k in keys_to_delete:
                del self._data[k]
            self._save()
            return True

    def list_entries(self, channel_name: str | None = None) -> list[dict[str, Any]]:
        """列出全部存储映射，可按 channel 过滤。"""
        results = []
        for key, entry in self._data.items():
            parts = key.split(":", 2)
            ch = parts[0]
            chat = parts[1] if len(parts) > 1 else ""
            topic = parts[2] if len(parts) > 2 else None
            if channel_name and ch != channel_name:
                continue
            item: dict[str, Any] = {"channel_name": ch, "chat_id": chat, **entry}
            if topic is not None:
                item["topic_id"] = topic
            results.append(item)
        return results
