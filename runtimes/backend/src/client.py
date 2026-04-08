"""用于 lumen 的嵌入式 Python 客户端（InsightFlowClient）。

无需依赖 LangGraph Server 或 Gateway API 进程，
即可通过代码直接调用 lumen 的代理能力。

用法示例：
    from src.client import InsightFlowClient

    client = InsightFlowClient()
    response = client.chat("帮我分析这篇论文", thread_id="my-thread")
    print(response)

    # 流式输出
    for event in client.stream("hello"):
        print(event)
"""

import asyncio
import json
import logging
import mimetypes
import re
import shutil
import tempfile
import uuid
import zipfile
from collections.abc import Generator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig

from src.agents.lead_agent.agent import _build_middlewares
from src.agents.lead_agent.prompt import apply_prompt_template
from src.agents.thread_state import ThreadState
from src.config.app_config import get_app_config, reload_app_config
from src.config.extensions_config import ExtensionsConfig, SkillStateConfig, get_extensions_config, reload_extensions_config
from src.config.paths import get_paths
from src.models import create_chat_model

logger = logging.getLogger(__name__)


@dataclass
class StreamEvent:
    """流式代理响应中的单个事件。

    事件类型与 LangGraph SSE 协议保持一致：
        - ``"values"``：完整状态快照（title、messages、artifacts）
        - ``"messages-tuple"``：单条消息更新（AI 文本、工具调用、工具结果）
        - ``"end"``：流结束

    属性：
        type: 事件类型。
        data: 事件载荷，不同类型结构不同。
    """

    type: str
    data: dict[str, Any] = field(default_factory=dict)


class InsightFlowClient:
    """用于 lumen 代理系统的嵌入式 Python 客户端封装类。

    通过编程方式直接访问 lumen 代理能力，
    无需额外启动 LangGraph Server 或 Gateway API 进程。

    注意：
        多轮会话依赖 ``checkpointer``。若未配置，
        每次 ``stream()`` / ``chat()`` 都是无状态调用，
        ``thread_id`` 仅用于文件隔离（uploads / artifacts）。

        系统提示词（包含日期、记忆与技能上下文）会在内部 agent 首次创建时生成，
        并在配置键不变时复用缓存。长生命周期进程中如需强制刷新，
        请调用 :meth:`reset_agent`。

    示例::

        from src.client import InsightFlowClient

        client = InsightFlowClient()

        # 简单单轮调用
        print(client.chat("hello"))

        # 流式输出
        for event in client.stream("hello"):
            print(event.type, event.data)

        # 配置查询
        print(client.list_models())
        print(client.list_skills())
    """

    def __init__(
        self,
        config_path: str | None = None,
        checkpointer=None,
        *,
        model_name: str | None = None,
        thinking_enabled: bool = True,
        subagent_enabled: bool = False,
        plan_mode: bool = False,
    ):
        """初始化客户端。

        会先加载配置，但延迟到首次调用时才创建内部 agent。

        参数：
            config_path: `config.yaml` 路径；为 None 时按默认规则解析。
            checkpointer: LangGraph checkpointer 实例，用于状态持久化。
                多轮会话复用同一 `thread_id` 时需要该实例；
                若未配置，则每次调用均为无状态。
            model_name: 覆盖配置中的默认模型名。
            thinking_enabled: 是否启用模型扩展思考能力。
            subagent_enabled: 是否启用子代理委派。
            plan_mode: 是否启用计划模式（TodoList 中间件）。
        """
        if config_path is not None:
            reload_app_config(config_path)
        self._app_config = get_app_config()

        self._checkpointer = checkpointer
        self._model_name = model_name
        self._thinking_enabled = thinking_enabled
        self._subagent_enabled = subagent_enabled
        self._plan_mode = plan_mode

        # 延迟初始化 agent：首次调用时创建，配置变化后重建。
        self._agent = None
        self._agent_config_key: tuple | None = None

    def reset_agent(self) -> None:
        """强制在下次调用时重建内部 agent。

        当外部状态发生变化（例如记忆更新、技能安装）且希望
        反映到系统提示词或工具集合时可调用此方法。
        """
        self._agent = None
        self._agent_config_key = None

    # ------------------------------------------------------------------
    # 内部辅助方法
    # ------------------------------------------------------------------

    @staticmethod
    def _atomic_write_json(path: Path, data: dict) -> None:
        """以原子方式将 JSON 写入 *path*（临时文件 + replace）。"""
        fd = tempfile.NamedTemporaryFile(
            mode="w",
            dir=path.parent,
            suffix=".tmp",
            delete=False,
        )
        try:
            json.dump(data, fd, indent=2)
            fd.close()
            Path(fd.name).replace(path)
        except BaseException:
            fd.close()
            Path(fd.name).unlink(missing_ok=True)
            raise

    def _get_runnable_config(self, thread_id: str, **overrides) -> RunnableConfig:
        """构建 agent 调用所需的 `RunnableConfig`。"""
        configurable = {
            "thread_id": thread_id,
            "model_name": overrides.get("model_name", self._model_name),
            "thinking_enabled": overrides.get("thinking_enabled", self._thinking_enabled),
            "is_plan_mode": overrides.get("plan_mode", self._plan_mode),
            "subagent_enabled": overrides.get("subagent_enabled", self._subagent_enabled),
        }
        return RunnableConfig(
            configurable=configurable,
            recursion_limit=overrides.get("recursion_limit", 300),
        )

    def _ensure_agent(self, config: RunnableConfig):
        """在配置相关参数变化时创建或重建 agent。"""
        cfg = config.get("configurable", {})
        key = (
            cfg.get("model_name"),
            cfg.get("thinking_enabled"),
            cfg.get("is_plan_mode"),
            cfg.get("subagent_enabled"),
        )

        if self._agent is not None and self._agent_config_key == key:
            return

        thinking_enabled = cfg.get("thinking_enabled", True)
        model_name = cfg.get("model_name")
        subagent_enabled = cfg.get("subagent_enabled", False)
        max_concurrent_subagents = cfg.get("max_concurrent_subagents", 3)

        kwargs: dict[str, Any] = {
            "model": create_chat_model(name=model_name, thinking_enabled=thinking_enabled),
            "tools": self._get_tools(model_name=model_name, subagent_enabled=subagent_enabled),
            "middleware": _build_middlewares(config, model_name=model_name),
            "system_prompt": apply_prompt_template(
                subagent_enabled=subagent_enabled,
                max_concurrent_subagents=max_concurrent_subagents,
            ),
            "state_schema": ThreadState,
        }
        checkpointer = self._checkpointer
        if checkpointer is None:
            from src.agents.checkpointer import get_checkpointer

            checkpointer = get_checkpointer()
        if checkpointer is not None:
            kwargs["checkpointer"] = checkpointer

        self._agent = create_agent(**kwargs)
        self._agent_config_key = key
        logger.info("Agent created: model=%s, thinking=%s", model_name, thinking_enabled)

    @staticmethod
    def _get_tools(*, model_name: str | None, subagent_enabled: bool):
        """延迟导入工具，避免模块级循环依赖。"""
        from src.tools import get_available_tools

        return get_available_tools(model_name=model_name, subagent_enabled=subagent_enabled)

    @staticmethod
    def _serialize_message(msg) -> dict:
        """将 LangChain 消息序列化为普通字典（用于 values 事件）。"""
        if isinstance(msg, AIMessage):
            d: dict[str, Any] = {"type": "ai", "content": msg.content, "id": getattr(msg, "id", None)}
            if msg.tool_calls:
                d["tool_calls"] = [{"name": tc["name"], "args": tc["args"], "id": tc.get("id")} for tc in msg.tool_calls]
            return d
        if isinstance(msg, ToolMessage):
            return {
                "type": "tool",
                "content": msg.content if isinstance(msg.content, str) else str(msg.content),
                "name": getattr(msg, "name", None),
                "tool_call_id": getattr(msg, "tool_call_id", None),
                "id": getattr(msg, "id", None),
            }
        if isinstance(msg, HumanMessage):
            return {"type": "human", "content": msg.content, "id": getattr(msg, "id", None)}
        if isinstance(msg, SystemMessage):
            return {"type": "system", "content": msg.content, "id": getattr(msg, "id", None)}
        return {"type": "unknown", "content": str(msg), "id": getattr(msg, "id", None)}

    @staticmethod
    def _extract_text(content) -> str:
        """从 AIMessage 内容中提取纯文本（兼容 str / 内容块列表）。"""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, str):
                    parts.append(block)
                elif isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block["text"])
            return "\n".join(parts) if parts else ""
        return str(content)

    # ------------------------------------------------------------------
    # 对外 API：会话
    # ------------------------------------------------------------------

    def stream(
        self,
        message: str,
        *,
        thread_id: str | None = None,
        **kwargs,
    ) -> Generator[StreamEvent, None, None]:
        """流式执行一轮会话，按增量产出事件。

        每次调用会发送一条用户消息，并持续产出事件直到 agent 完成当前轮。
        若希望跨调用保留多轮上下文，需要在初始化时提供 ``checkpointer``。

        事件类型遵循 LangGraph SSE 协议，便于在 HTTP 流式与嵌入式调用间
        复用同一套事件处理逻辑。

        参数：
            message: 用户输入文本。
            thread_id: 会话线程 ID；为 None 时自动生成。
            **kwargs: 覆盖客户端默认配置（如 model_name、thinking_enabled、
                plan_mode、subagent_enabled、recursion_limit）。

        产出：
            `StreamEvent`，类型包括：
            - `type="values"`：`data={"title": str|None, "messages": [...], "artifacts": [...]}`
            - `type="messages-tuple"`：`data={"type": "ai", "content": str, "id": str}`
            - `type="messages-tuple"`：`data={"type": "ai", "content": "", "id": str, "tool_calls": [...]}`
            - `type="messages-tuple"`：`data={"type": "tool", "content": str, "name": str, "tool_call_id": str, "id": str}`
            - `type="end"`：`data={}`
        """
        if thread_id is None:
            thread_id = str(uuid.uuid4())

        config = self._get_runnable_config(thread_id, **kwargs)
        self._ensure_agent(config)

        state: dict[str, Any] = {"messages": [HumanMessage(content=message)]}
        context = {"thread_id": thread_id}

        seen_ids: set[str] = set()

        for chunk in self._agent.stream(state, config=config, context=context, stream_mode="values"):
            messages = chunk.get("messages", [])

            for msg in messages:
                msg_id = getattr(msg, "id", None)
                if msg_id and msg_id in seen_ids:
                    continue
                if msg_id:
                    seen_ids.add(msg_id)

                if isinstance(msg, AIMessage):
                    if msg.tool_calls:
                        yield StreamEvent(
                            type="messages-tuple",
                            data={
                                "type": "ai",
                                "content": "",
                                "id": msg_id,
                                "tool_calls": [{"name": tc["name"], "args": tc["args"], "id": tc.get("id")} for tc in msg.tool_calls],
                            },
                        )

                    text = self._extract_text(msg.content)
                    if text:
                        yield StreamEvent(
                            type="messages-tuple",
                            data={"type": "ai", "content": text, "id": msg_id},
                        )

                elif isinstance(msg, ToolMessage):
                    yield StreamEvent(
                        type="messages-tuple",
                        data={
                            "type": "tool",
                            "content": msg.content if isinstance(msg.content, str) else str(msg.content),
                            "name": getattr(msg, "name", None),
                            "tool_call_id": getattr(msg, "tool_call_id", None),
                            "id": msg_id,
                        },
                    )

            # 为每个状态快照发出一条 values 事件
            yield StreamEvent(
                type="values",
                data={
                    "title": chunk.get("title"),
                    "messages": [self._serialize_message(m) for m in messages],
                    "artifacts": chunk.get("artifacts", []),
                },
            )

        yield StreamEvent(type="end", data={})

    def chat(self, message: str, *, thread_id: str | None = None, **kwargs) -> str:
        """发送消息并返回最终文本回复。

        这是 :meth:`stream` 的便捷封装，仅返回 ``messages-tuple`` 事件中
        **最后一条** AI 文本。若同一轮产生多段文本，中间段会被丢弃；
        若需要完整事件流，请直接使用 :meth:`stream`。

        参数：
            message: 用户输入文本。
            thread_id: 会话线程 ID；为 None 时自动生成。
            **kwargs: 覆盖客户端默认参数（与 `stream()` 一致）。

        返回：
            最后一条 AI 文本；若无回复则返回空字符串。
        """
        last_text = ""
        for event in self.stream(message, thread_id=thread_id, **kwargs):
            if event.type == "messages-tuple" and event.data.get("type") == "ai":
                content = event.data.get("content", "")
                if content:
                    last_text = content
        return last_text

    # ------------------------------------------------------------------
    # 对外 API：配置查询
    # ------------------------------------------------------------------

    def list_models(self) -> dict:
        """列出配置中的可用模型。

        返回：
            包含 `models` 键的字典，结构与 Gateway API
            `ModelsListResponse` 模式一致。
        """
        return {
            "models": [
                {
                    "name": model.name,
                    "display_name": getattr(model, "display_name", None),
                    "description": getattr(model, "description", None),
                    "supports_thinking": getattr(model, "supports_thinking", False),
                    "supports_reasoning_effort": getattr(model, "supports_reasoning_effort", False),
                }
                for model in self._app_config.models
            ]
        }

    def list_skills(self, enabled_only: bool = False) -> dict:
        """列出可用技能。

        参数：
            enabled_only: 为 True 时仅返回已启用技能。

        返回：
            包含 `skills` 键的字典，结构与 Gateway API
            `SkillsListResponse` 模式一致。
        """
        from src.skills.loader import load_skills

        return {
            "skills": [
                {
                    "name": s.name,
                    "description": s.description,
                    "license": s.license,
                    "category": s.category,
                    "enabled": s.enabled,
                }
                for s in load_skills(enabled_only=enabled_only)
            ]
        }

    def get_memory(self) -> dict:
        """获取当前记忆数据。

        返回：
            记忆数据字典（结构见 `src/agents/memory/updater.py`）。
        """
        from src.agents.memory.updater import get_memory_data

        return get_memory_data()

    def get_model(self, name: str) -> dict | None:
        """按名称获取指定模型配置。

        参数：
            name: 模型名称。

        返回：
            与 Gateway API `ModelResponse` 模式一致的模型信息字典；
            未找到时返回 None。
        """
        model = self._app_config.get_model_config(name)
        if model is None:
            return None
        return {
            "name": model.name,
            "display_name": getattr(model, "display_name", None),
            "description": getattr(model, "description", None),
            "supports_thinking": getattr(model, "supports_thinking", False),
            "supports_reasoning_effort": getattr(model, "supports_reasoning_effort", False),
        }

    # ------------------------------------------------------------------
    # 对外 API：MCP 配置
    # ------------------------------------------------------------------

    def get_mcp_config(self) -> dict:
        """获取 MCP 服务配置。

        返回：
            包含 `mcp_servers` 键的字典，键为服务名、值为服务配置，
            结构与 Gateway API `McpConfigResponse` 模式一致。
        """
        config = get_extensions_config()
        return {"mcp_servers": {name: server.model_dump() for name, server in config.mcp_servers.items()}}

    def update_mcp_config(self, mcp_servers: dict[str, dict]) -> dict:
        """更新 MCP 服务配置。

        该方法会写入 `extensions_config.json` 并重载缓存。

        参数：
            mcp_servers: 服务名到配置字典的映射。
                每个配置通常包含 enabled、type、command、args、env、url 等字段。

        返回：
            包含 `mcp_servers` 键的结果字典，结构与 Gateway API
            `McpConfigResponse` 模式一致。

        异常：
            OSError: 配置文件写入失败时抛出。
        """
        config_path = ExtensionsConfig.resolve_config_path()
        if config_path is None:
            raise FileNotFoundError("Cannot locate extensions_config.json. Set LUMEN_EXTENSIONS_CONFIG_PATH or ensure it exists in the project root.")

        current_config = get_extensions_config()

        config_data = {
            "mcpServers": mcp_servers,
            "skills": {name: {"enabled": skill.enabled} for name, skill in current_config.skills.items()},
        }

        self._atomic_write_json(config_path, config_data)

        self._agent = None
        reloaded = reload_extensions_config()
        return {"mcp_servers": {name: server.model_dump() for name, server in reloaded.mcp_servers.items()}}

    # ------------------------------------------------------------------
    # 对外 API：技能管理
    # ------------------------------------------------------------------

    def get_skill(self, name: str) -> dict | None:
        """按名称获取技能信息。

        参数：
            name: 技能名称。

        返回：
            技能信息字典；未找到时返回 None。
        """
        from src.skills.loader import load_skills

        skill = next((s for s in load_skills(enabled_only=False) if s.name == name), None)
        if skill is None:
            return None
        return {
            "name": skill.name,
            "description": skill.description,
            "license": skill.license,
            "category": skill.category,
            "enabled": skill.enabled,
        }

    def update_skill(self, name: str, *, enabled: bool) -> dict:
        """更新技能启用状态。

        参数：
            name: 技能名称。
            enabled: 新的启用状态。

        返回：
            更新后的技能信息字典。

        异常：
            ValueError: 技能不存在时抛出。
            OSError: 配置文件写入失败时抛出。
        """
        from src.skills.loader import load_skills

        skills = load_skills(enabled_only=False)
        skill = next((s for s in skills if s.name == name), None)
        if skill is None:
            raise ValueError(f"Skill '{name}' not found")

        config_path = ExtensionsConfig.resolve_config_path()
        if config_path is None:
            raise FileNotFoundError("Cannot locate extensions_config.json. Set LUMEN_EXTENSIONS_CONFIG_PATH or ensure it exists in the project root.")

        extensions_config = get_extensions_config()
        extensions_config.skills[name] = SkillStateConfig(enabled=enabled)

        config_data = {
            "mcpServers": {n: s.model_dump() for n, s in extensions_config.mcp_servers.items()},
            "skills": {n: {"enabled": sc.enabled} for n, sc in extensions_config.skills.items()},
        }

        self._atomic_write_json(config_path, config_data)

        self._agent = None
        reload_extensions_config()

        updated = next((s for s in load_skills(enabled_only=False) if s.name == name), None)
        if updated is None:
            raise RuntimeError(f"Skill '{name}' disappeared after update")
        return {
            "name": updated.name,
            "description": updated.description,
            "license": updated.license,
            "category": updated.category,
            "enabled": updated.enabled,
        }

    def install_skill(self, skill_path: str | Path) -> dict:
        """从 `.skill` 压缩包（ZIP）安装技能。

        参数：
            skill_path: `.skill` 文件路径。

        返回：
            包含 success、skill_name、message 的结果字典。

        异常：
            FileNotFoundError: 文件不存在时抛出。
            ValueError: 文件内容或格式非法时抛出。
        """
        from src.gateway.routers.skills import _validate_skill_frontmatter
        from src.skills.loader import get_skills_root_path

        path = Path(skill_path)
        if not path.exists():
            raise FileNotFoundError(f"Skill file not found: {skill_path}")
        if not path.is_file():
            raise ValueError(f"Path is not a file: {skill_path}")
        if path.suffix != ".skill":
            raise ValueError("File must have .skill extension")
        if not zipfile.is_zipfile(path):
            raise ValueError("File is not a valid ZIP archive")

        skills_root = get_skills_root_path()
        custom_dir = skills_root / "custom"
        custom_dir.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            with zipfile.ZipFile(path, "r") as zf:
                total_size = sum(info.file_size for info in zf.infolist())
                if total_size > 100 * 1024 * 1024:
                    raise ValueError("Skill archive too large when extracted (>100MB)")
                for info in zf.infolist():
                    if Path(info.filename).is_absolute() or ".." in Path(info.filename).parts:
                        raise ValueError(f"Unsafe path in archive: {info.filename}")
                zf.extractall(tmp_path)
            for p in tmp_path.rglob("*"):
                if p.is_symlink():
                    p.unlink()

            items = list(tmp_path.iterdir())
            if not items:
                raise ValueError("Skill archive is empty")

            skill_dir = items[0] if len(items) == 1 and items[0].is_dir() else tmp_path

            is_valid, message, skill_name = _validate_skill_frontmatter(skill_dir)
            if not is_valid:
                raise ValueError(f"Invalid skill: {message}")
            if not re.fullmatch(r"[a-zA-Z0-9_-]+", skill_name):
                raise ValueError(f"Invalid skill name: {skill_name}")

            target = custom_dir / skill_name
            if target.exists():
                raise ValueError(f"Skill '{skill_name}' already exists")

            shutil.copytree(skill_dir, target)

        return {"success": True, "skill_name": skill_name, "message": f"Skill '{skill_name}' installed successfully"}

    # ------------------------------------------------------------------
    # 对外 API：记忆管理
    # ------------------------------------------------------------------

    def reload_memory(self) -> dict:
        """从文件重载记忆数据，并强制缓存失效。

        返回：
            重载后的记忆数据字典。
        """
        from src.agents.memory.updater import reload_memory_data

        return reload_memory_data()

    def get_memory_config(self) -> dict:
        """获取记忆系统配置。

        返回：
            记忆配置字典。
        """
        from src.config.memory_config import get_memory_config

        config = get_memory_config()
        return {
            "enabled": config.enabled,
            "storage_path": config.storage_path,
            "debounce_seconds": config.debounce_seconds,
            "max_facts": config.max_facts,
            "fact_confidence_threshold": config.fact_confidence_threshold,
            "injection_enabled": config.injection_enabled,
            "max_injection_tokens": config.max_injection_tokens,
        }

    def get_memory_status(self) -> dict:
        """获取记忆状态（配置 + 当前数据）。

        返回：
            包含 `config` 与 `data` 键的字典。
        """
        return {
            "config": self.get_memory_config(),
            "data": self.get_memory(),
        }

    # ------------------------------------------------------------------
    # 对外 API：文件上传
    # ------------------------------------------------------------------

    @staticmethod
    def _get_uploads_dir(thread_id: str) -> Path:
        """获取线程 uploads 目录（不存在则创建）。"""
        base = get_paths().sandbox_uploads_dir(thread_id)
        base.mkdir(parents=True, exist_ok=True)
        return base

    def upload_files(self, thread_id: str, files: list[str | Path]) -> dict:
        """将本地文件上传到指定线程的 uploads 目录。

        对 PDF、PPT、Excel、Word 文件会额外尝试转换为 Markdown。

        参数：
            thread_id: 目标线程 ID。
            files: 待上传本地文件路径列表。

        返回：
            包含 success、files、message 的结果字典，
            与 Gateway API `UploadResponse` 结构一致。

        异常：
            FileNotFoundError: 任一文件不存在时抛出。
        """
        from src.gateway.routers.uploads import convert_file_to_markdown, get_markdown_extensions

        # 先校验全部文件，避免发生部分上传成功的情况。
        resolved_files = []
        for f in files:
            p = Path(f)
            if not p.exists():
                raise FileNotFoundError(f"File not found: {f}")
            resolved_files.append(p)

        uploads_dir = self._get_uploads_dir(thread_id)
        uploaded_files: list[dict] = []

        for src_path in resolved_files:
            dest = uploads_dir / src_path.name
            shutil.copy2(src_path, dest)

            info: dict[str, Any] = {
                "filename": src_path.name,
                "size": str(dest.stat().st_size),
                "path": str(dest),
                "virtual_path": f"/mnt/user-data/uploads/{src_path.name}",
                "artifact_url": f"/api/threads/{thread_id}/artifacts/mnt/user-data/uploads/{src_path.name}",
            }

            if src_path.suffix.lower() in get_markdown_extensions():
                try:
                    try:
                        asyncio.get_running_loop()
                        import concurrent.futures

                        with concurrent.futures.ThreadPoolExecutor() as pool:
                            md_path = pool.submit(lambda: asyncio.run(convert_file_to_markdown(dest))).result()
                    except RuntimeError:
                        md_path = asyncio.run(convert_file_to_markdown(dest))
                except Exception:
                    logger.warning("Failed to convert %s to markdown", src_path.name, exc_info=True)
                    md_path = None

                if md_path is not None:
                    info["markdown_file"] = md_path.name
                    info["markdown_virtual_path"] = f"/mnt/user-data/uploads/{md_path.name}"
                    info["markdown_artifact_url"] = f"/api/threads/{thread_id}/artifacts/mnt/user-data/uploads/{md_path.name}"

            uploaded_files.append(info)

        return {
            "success": True,
            "files": uploaded_files,
            "message": f"Successfully uploaded {len(uploaded_files)} file(s)",
        }

    def list_uploads(self, thread_id: str) -> dict:
        """列出线程 uploads 目录中的文件。

        参数：
            thread_id: 线程 ID。

        返回：
            包含 `files` 与 `count` 的结果字典，
            与 Gateway API `list_uploaded_files` 返回结构一致。
        """
        uploads_dir = self._get_uploads_dir(thread_id)
        if not uploads_dir.exists():
            return {"files": [], "count": 0}

        files = []
        for fp in sorted(uploads_dir.iterdir()):
            if fp.is_file():
                stat = fp.stat()
                files.append(
                    {
                        "filename": fp.name,
                        "size": str(stat.st_size),
                        "path": str(fp),
                        "virtual_path": f"/mnt/user-data/uploads/{fp.name}",
                        "artifact_url": f"/api/threads/{thread_id}/artifacts/mnt/user-data/uploads/{fp.name}",
                        "extension": fp.suffix,
                        "modified": stat.st_mtime,
                    }
                )
        return {"files": files, "count": len(files)}

    def delete_upload(self, thread_id: str, filename: str) -> dict:
        """删除线程 uploads 目录中的文件。

        参数：
            thread_id: 线程 ID。
            filename: 待删除文件名。

        返回：
            包含 success 与 message 的结果字典，
            与 Gateway API `delete_uploaded_file` 返回结构一致。

        异常：
            FileNotFoundError: 文件不存在时抛出。
            PermissionError: 检测到路径穿越时抛出。
        """
        uploads_dir = self._get_uploads_dir(thread_id)
        file_path = (uploads_dir / filename).resolve()

        try:
            file_path.relative_to(uploads_dir.resolve())
        except ValueError as exc:
            raise PermissionError("Access denied: path traversal detected") from exc

        if not file_path.is_file():
            raise FileNotFoundError(f"File not found: {filename}")

        file_path.unlink()
        return {"success": True, "message": f"Deleted {filename}"}

    # ------------------------------------------------------------------
    # 对外 API：产物文件
    # ------------------------------------------------------------------

    def get_artifact(self, thread_id: str, path: str) -> tuple[bytes, str]:
        """读取 agent 生成的产物文件。

        参数：
            thread_id: 线程 ID。
            path: 虚拟路径（如 `"mnt/user-data/outputs/file.txt"`）。

        返回：
            `(file_bytes, mime_type)` 元组。

        异常：
            FileNotFoundError: 产物文件不存在时抛出。
            ValueError: 路径非法时抛出。
        """
        virtual_prefix = "mnt/user-data"
        clean_path = path.lstrip("/")
        if not clean_path.startswith(virtual_prefix):
            raise ValueError(f"Path must start with /{virtual_prefix}")

        relative = clean_path[len(virtual_prefix) :].lstrip("/")
        base_dir = get_paths().sandbox_user_data_dir(thread_id)
        actual = (base_dir / relative).resolve()

        try:
            actual.relative_to(base_dir.resolve())
        except ValueError as exc:
            raise PermissionError("Access denied: path traversal detected") from exc
        if not actual.exists():
            raise FileNotFoundError(f"Artifact not found: {path}")
        if not actual.is_file():
            raise ValueError(f"Path is not a file: {path}")

        mime_type, _ = mimetypes.guess_type(actual)
        return actual.read_bytes(), mime_type or "application/octet-stream"
