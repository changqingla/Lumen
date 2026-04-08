"""用于读取、写入和更新记忆数据的模块。"""

import json
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from src.agents.memory.prompt import (
    MEMORY_UPDATE_PROMPT,
    format_conversation_for_update,
)
from src.config.memory_config import get_memory_config
from src.config.paths import get_paths
from src.models import create_chat_model


def _get_memory_file_path(agent_name: str | None = None) -> Path:
    """获取记忆文件路径。

    参数：
        agent_name: 若提供，则返回对应 agent 的独立记忆文件路径；
            若为 None，则返回全局记忆文件路径。

    返回：
        记忆文件路径。
    """
    if agent_name is not None:
        return get_paths().agent_memory_file(agent_name)

    config = get_memory_config()
    if config.storage_path:
        p = Path(config.storage_path)
        # 绝对路径直接使用；相对路径则基于 base_dir 解析
        return p if p.is_absolute() else get_paths().base_dir / p
    return get_paths().memory_file


def _create_empty_memory() -> dict[str, Any]:
    """创建空的记忆数据结构。"""
    return {
        "version": "1.0",
        "lastUpdated": datetime.utcnow().isoformat() + "Z",
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


# 按 agent 维度缓存记忆：键为 agent_name（None 表示全局）
# 值结构为：（记忆数据，文件修改时间）
_memory_cache: dict[str | None, tuple[dict[str, Any], float | None]] = {}


def get_memory_data(agent_name: str | None = None) -> dict[str, Any]:
    """获取记忆数据。

    若记忆文件自上次加载后发生变化，缓存会自动失效，
    以确保返回的数据始终是最新内容。

    参数：
        agent_name: 若提供则加载该 agent 的记忆；否则加载全局记忆。

    返回：
        记忆数据字典。
    """
    file_path = _get_memory_file_path(agent_name)

    # 获取当前文件修改时间
    try:
        current_mtime = file_path.stat().st_mtime if file_path.exists() else None
    except OSError:
        current_mtime = None

    cached = _memory_cache.get(agent_name)

    # 当文件已修改或缓存不存在时，重新加载数据
    if cached is None or cached[1] != current_mtime:
        memory_data = _load_memory_from_file(agent_name)
        _memory_cache[agent_name] = (memory_data, current_mtime)
        return memory_data

    return cached[0]


def reload_memory_data(agent_name: str | None = None) -> dict[str, Any]:
    """强制重新加载记忆数据并刷新缓存。

    参数：
        agent_name: 若提供则重载该 agent 的记忆；否则重载全局记忆。

    返回：
        重新加载后的记忆数据字典。
    """
    file_path = _get_memory_file_path(agent_name)
    memory_data = _load_memory_from_file(agent_name)

    try:
        mtime = file_path.stat().st_mtime if file_path.exists() else None
    except OSError:
        mtime = None

    _memory_cache[agent_name] = (memory_data, mtime)
    return memory_data


def _load_memory_from_file(agent_name: str | None = None) -> dict[str, Any]:
    """从文件读取记忆数据。

    参数：
        agent_name: 若提供则读取该 agent 的记忆文件；否则读取全局记忆文件。

    返回：
        记忆数据字典。
    """
    file_path = _get_memory_file_path(agent_name)

    if not file_path.exists():
        return _create_empty_memory()

    try:
        with open(file_path, encoding="utf-8") as f:
            data = json.load(f)
        return data
    except (json.JSONDecodeError, OSError) as e:
        print(f"Failed to load memory file: {e}")
        return _create_empty_memory()


# 匹配“文件上传事件”描述句，而非一般文件相关工作描述。
# 规则刻意收窄，避免误删合法事实，例如“用户处理 CSV 文件”或“偏好 PDF 导出”。
_UPLOAD_SENTENCE_RE = re.compile(
    r"[^.!?]*\b(?:"
    r"upload(?:ed|ing)?(?:\s+\w+){0,3}\s+(?:file|files?|document|documents?|attachment|attachments?)"
    r"|file\s+upload"
    r"|/mnt/user-data/uploads/"
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


def _save_memory_to_file(memory_data: dict[str, Any], agent_name: str | None = None) -> bool:
    """将记忆数据保存到文件。

    参数：
        memory_data: 待保存的记忆数据。
        agent_name: 若提供则保存到该 agent 的记忆文件；否则保存到全局文件。

    返回：
        保存成功返回 True，否则返回 False。
    """
    file_path = _get_memory_file_path(agent_name)

    try:
        # 确保目录存在
        file_path.parent.mkdir(parents=True, exist_ok=True)

        # 更新 `lastUpdated` 时间戳
        memory_data["lastUpdated"] = datetime.utcnow().isoformat() + "Z"

        # 通过临时文件进行原子写入
        temp_path = file_path.with_suffix(".tmp")
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(memory_data, f, indent=2, ensure_ascii=False)

        # 将临时文件替换为正式文件（多数系统上为原子操作）
        temp_path.replace(file_path)

        # 更新缓存及文件修改时间
        try:
            mtime = file_path.stat().st_mtime
        except OSError:
            mtime = None

        _memory_cache[agent_name] = (memory_data, mtime)

        print(f"Memory saved to {file_path}")
        return True
    except OSError as e:
        print(f"Failed to save memory file: {e}")
        return False


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

    def update_memory(self, messages: list[Any], thread_id: str | None = None, agent_name: str | None = None) -> bool:
        """根据对话消息更新记忆。

        参数：
            messages: 对话消息列表。
            thread_id: 可选线程 ID，用于标记来源。
            agent_name: 若提供则更新该 agent 记忆；否则更新全局记忆。

        返回：
            更新成功返回 True，否则返回 False。
        """
        config = get_memory_config()
        if not config.enabled:
            return False

        if not messages:
            return False

        try:
            # 获取当前记忆
            current_memory = get_memory_data(agent_name)

            # 将对话格式化为提示词输入
            conversation_text = format_conversation_for_update(messages)

            if not conversation_text.strip():
                return False

            # 构建提示词
            prompt = MEMORY_UPDATE_PROMPT.format(
                current_memory=json.dumps(current_memory, indent=2),
                conversation=conversation_text,
            )

            # 调用 LLM
            model = self._get_model()
            response = model.invoke(prompt)
            response_text = str(response.content).strip()

            # 解析响应
            # 若包含 Markdown 代码块则先去除包裹
            if response_text.startswith("```"):
                lines = response_text.split("\n")
                response_text = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])

            update_data = json.loads(response_text)

            # 应用增量更新
            updated_memory = self._apply_updates(current_memory, update_data, thread_id)

            # 保存前清理所有 summary 中的上传事件描述。
            # 上传文件属于会话级资源，不会在后续会话中持续可用，
            # 若写入长期记忆会导致代理后续反复尝试定位这些文件并失败。
            updated_memory = _strip_upload_mentions_from_memory(updated_memory)

            # 持久化保存
            return _save_memory_to_file(updated_memory, agent_name)

        except json.JSONDecodeError as e:
            print(f"Failed to parse LLM response for memory update: {e}")
            return False
        except Exception as e:
            print(f"Memory update failed: {e}")
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
        now = datetime.utcnow().isoformat() + "Z"

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
