"""ReAct Agent 节点 — 原子工具架构"""
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, AsyncGenerator, Optional, List, TYPE_CHECKING

from langchain_core.messages import HumanMessage, AIMessage
from langchain_core.tools import BaseTool

from .base import BaseAgentNode
from ..state import AgentState
from ..react import (
    ReActConfig,
    Scratchpad,
    ScratchpadEntry,
    ActionParser,
    create_default_hook_manager,
    HookAction,
    CompletionDetector,
    CompletionReason,
)
from ...prompts import REACT_AGENT_PROMPT
from ...utils.logger import get_logger
from ...tools.registry import get_tool_registry
from ...tools.read_document_tool import ReadDocumentTool, ReadDocumentOutlineTool
from ...tools.note_tool import WriteNoteTool, ReadNoteTool
from ...skills.loader import get_skill_loader
from ...skills.tools import LoadSkillTool, ReadSkillResourceTool
from ...tools.command_tool import RunCommandTool
from ...tools.file_tools import AppendToFileTool, WriteFileTool
from ...tools.storage_tool import UploadToStorageTool

if TYPE_CHECKING:
    from ...mcp.tool_adapter import MCPToolAdapter

logger = get_logger(__name__)

_BEIJING_TZ = timezone(timedelta(hours=8))


def _get_beijing_date() -> str:
    """Return current date in Beijing timezone as YYYY-MM-DD string."""
    return datetime.now(_BEIJING_TZ).strftime("%Y-%m-%d")


class ReActNodes(BaseAgentNode):
    """ReAct Agent 节点 — 原子工具架构，无复合技能"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.config = ReActConfig()

        # 原子工具实例（运行时通过 set_document_context 注入文档数据）
        self._read_doc_tool = ReadDocumentTool()
        self._read_outline_tool = ReadDocumentOutlineTool()
        self._write_note_tool = WriteNoteTool()
        self._read_note_tool = ReadNoteTool()
        # 让 read_note 和 write_note 共享同一个 notes 字典
        self._read_note_tool.notes = self._write_note_tool.notes

        # Skill 工具（Anthropic Agent Skills 兼容）
        self._load_skill_tool = LoadSkillTool()
        self._read_skill_resource_tool = ReadSkillResourceTool()
        self._skill_loader = get_skill_loader()

        # 新增工具实例（workspace 工具）
        self._run_command_tool = RunCommandTool()
        self._write_file_tool = WriteFileTool()
        self._append_to_file_tool = AppendToFileTool()
        self._upload_to_storage_tool = UploadToStorageTool()

        # 动态更新可用工具列表
        self._update_available_tools()

        self.action_parser = ActionParser(self.config)

        # Hook 管理器
        self.hook_manager = create_default_hook_manager() if self.config.enable_hooks else None

        # 完成检测器
        self.completion_detector = CompletionDetector(self.config) if self.config.enable_completion_detection else None

    def set_document_context(
        self,
        document_contents: Optional[Dict[str, str]],
        document_names: Optional[Dict[str, str]],
    ) -> None:
        """注入文档数据到阅读工具"""
        contents = document_contents or {}
        names = document_names or {}
        self._read_doc_tool.document_contents = contents
        self._read_doc_tool.document_names = names
        self._read_outline_tool.document_contents = contents
        self._read_outline_tool.document_names = names

    def set_workspace_context(self, workspace_path: str, session_id: str) -> None:
        """注入工作目录上下文到所有文件工具"""
        self._run_command_tool.workspace_path = workspace_path
        self._write_file_tool.workspace_path = workspace_path
        self._append_to_file_tool.workspace_path = workspace_path
        self._upload_to_storage_tool.workspace_path = workspace_path
        self._upload_to_storage_tool.session_id = session_id
        self._load_skill_tool.workspace_path = workspace_path

    def _update_available_tools(self) -> None:
        """动态更新可用工具列表"""
        tools = [
            "recall", "web_search",
            "read_document", "read_document_outline",
            "write_note", "read_note",
        ]

        # Skill 工具（仅当有 skill 安装时才添加）
        if self._skill_loader.skills:
            tools.append("load_skill")
            tools.append("read_skill_resource")

        # MCP 工具
        registry = get_tool_registry()
        for tool in registry.get_mcp_tools():
            tools.append(tool.name)

        # 新增 workspace 工具
        tools.extend(["run_command", "write_file", "append_to_file", "upload_to_storage"])

        tools.append("finish")
        self.config.available_tools = tuple(tools)
        logger.info(f"📌 ReAct 可用工具: {', '.join(tools)}")

    def _get_available_tools_description(self) -> str:
        """获取所有可用工具的描述"""
        tools_desc = []

        # 内置工具
        tools_desc.append("1. recall(query: str) - 从用户的文档知识库中语义检索相关信息片段。输入检索查询文本。")
        tools_desc.append("2. web_search(query: str) - 联网搜索外部信息。输入搜索查询文本。")
        tools_desc.append(f"3. read_document(input: JSON) - {self._read_doc_tool.description}")
        tools_desc.append(f"4. read_document_outline(doc_id: str) - {self._read_outline_tool.description}")
        tools_desc.append(f"5. write_note(input: JSON) - {self._write_note_tool.description}")
        tools_desc.append(f"6. read_note(title: str) - {self._read_note_tool.description}")

        idx = 7

        # Skill 工具
        if self._skill_loader.skills:
            tools_desc.append(f"{idx}. {self._load_skill_tool.name}(skill_name: str) - {self._load_skill_tool.description}")
            idx += 1
            tools_desc.append(f"{idx}. {self._read_skill_resource_tool.name}(input: JSON) - {self._read_skill_resource_tool.description}")
            idx += 1

        # MCP 工具
        registry = get_tool_registry()
        for tool in registry.get_mcp_tools():
            tools_desc.append(f"{idx}. {tool.name} - {tool.description}")
            idx += 1

        # 新增 workspace 工具
        tools_desc.append(f"{idx}. run_command(command: str) - {self._run_command_tool.description}")
        idx += 1
        tools_desc.append(f"{idx}. write_file(input: JSON) - {self._write_file_tool.description}")
        idx += 1
        tools_desc.append(f"{idx}. append_to_file(input: JSON) - {self._append_to_file_tool.description}")
        idx += 1
        tools_desc.append(f"{idx}. upload_to_storage(filename: str) - {self._upload_to_storage_tool.description}")
        idx += 1

        # finish 始终最后
        tools_desc.append(f"{idx}. finish(answer: str) - 结束推理，输出最终答案给用户。输入为完整的回答内容。")

        return "\n".join(tools_desc)

    def _get_available_tool_names(self) -> List[str]:
        """获取所有可用工具名称列表"""
        names = [
            "recall", "web_search",
            "read_document", "read_document_outline",
            "write_note", "read_note",
        ]
        if self._skill_loader.skills:
            names.append("load_skill")
            names.append("read_skill_resource")
        registry = get_tool_registry()
        for tool in registry.get_mcp_tools():
            names.append(tool.name)
        names.extend(["run_command", "write_file", "append_to_file", "upload_to_storage"])
        names.append("finish")
        return names

    # ==================================================================
    # ReAct 主循环
    # ==================================================================

    async def react_agent_node_stream(
        self, state: AgentState
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """ReAct Agent 主循环 — Thought → Action → Observation"""
        try:
            user_query = state["user_query"]
            session_id = state.get("session_id")

            # 初始化 scratchpad
            scratchpad = Scratchpad(
                max_tokens=self.config.max_scratchpad_tokens,
                model=self.llm.model_name if hasattr(self.llm, "model_name") else "gpt-4",
            )

            # 获取对话历史
            context_str = await self._get_conversation_context_async(state, stage="simple_interaction")

            # 构建文档信息
            document_info = self._build_document_info(state)

            iteration = 0
            final_answer = ""

            while iteration < self.config.max_iterations:
                iteration += 1
                logger.info(f"🔄 ReAct 迭代 {iteration}/{self.config.max_iterations}")

                # 智能完成检测
                if self.completion_detector and iteration > 1:
                    completion_result = self.completion_detector.check(scratchpad, user_query)
                    if completion_result.should_finish:
                        logger.info(f"🎯 智能检测建议结束: {completion_result.reason.value}")
                        if completion_result.reason in (
                            CompletionReason.STUCK_IN_LOOP,
                            CompletionReason.MAX_ERRORS,
                            CompletionReason.TOKEN_LIMIT,
                        ):
                            final_answer = self._generate_forced_answer(scratchpad, user_query)
                            break

                # 构建 prompt
                available_tools = self._get_available_tools_description()
                tool_names = ", ".join(self._get_available_tool_names())

                current_date = _get_beijing_date()

                prompt = REACT_AGENT_PROMPT.format(
                    user_query=user_query,
                    conversation_history=context_str if context_str else "无历史对话",
                    document_info=document_info,
                    scratchpad=scratchpad.to_string() if len(scratchpad) > 0 else "（首次思考，无历史记录）",
                    available_tools=available_tools,
                    tool_names=tool_names,
                    current_date=current_date,
                    skills_summary=self._skill_loader.get_metadata_summary(),
                )

                # 流式调用 LLM
                llm_output = ""
                thought_output_started = False
                thought_output_pos = 0
                answer_streaming = False
                answer_output_started = False
                answer_output_pos = 0
                first_finish_pos = -1
                first_action_input_pos = -1
                last_chunk = None
                usage_chunk = None

                async for chunk in self.llm.astream([HumanMessage(content=prompt)]):
                    chunk_content = chunk.content if hasattr(chunk, "content") else str(chunk)
                    llm_output += chunk_content
                    llm_lower = llm_output.lower()
                    last_chunk = chunk
                    if hasattr(chunk, "usage_metadata") and chunk.usage_metadata:
                        usage_chunk = chunk

                    # 检测 finish 动作
                    if not answer_streaming:
                        for finish_marker in ["action: finish", "action:finish"]:
                            pos = llm_lower.find(finish_marker)
                            if pos != -1:
                                remaining = llm_lower[pos:]
                                if "action input:" in remaining or "action_input:" in remaining:
                                    answer_streaming = True
                                    first_finish_pos = pos
                                    break

                    if answer_streaming:
                        if first_finish_pos == -1:
                            continue
                        if first_action_input_pos == -1:
                            for marker in ["action input:", "action_input:"]:
                                marker_pos = llm_lower.find(marker, first_finish_pos)
                                if marker_pos != -1:
                                    first_action_input_pos = marker_pos + len(marker)
                                    while first_action_input_pos < len(llm_output) and llm_output[first_action_input_pos] in " \t\n":
                                        first_action_input_pos += 1
                                    break
                        if first_action_input_pos == -1:
                            continue

                        answer_end = len(llm_output)
                        for stop_marker in ["\nthought:", "\naction:", "\nobservation:"]:
                            stop_pos = llm_lower.find(stop_marker, first_action_input_pos)
                            if stop_pos != -1 and stop_pos < answer_end:
                                answer_end = stop_pos

                        if not answer_output_started:
                            answer_output_pos = first_action_input_pos
                            answer_output_started = True

                        if answer_end > answer_output_pos:
                            new_content = llm_output[answer_output_pos:answer_end]
                            if new_content:
                                yield {"type": "answer_chunk", "data": {"content": new_content}}
                            answer_output_pos = answer_end
                        continue

                    # 流式输出思考部分
                    action_pos = -1
                    for marker in ["\naction:", "\nAction:", "\nACTION:"]:
                        pos = llm_lower.find(marker.lower())
                        if pos != -1:
                            action_pos = pos
                            break

                    if not thought_output_started:
                        thought_start = 0
                        for prefix in ["thought:", "Thought:", "THOUGHT:", "思考:"]:
                            prefix_lower = prefix.lower()
                            if llm_lower.strip().startswith(prefix_lower):
                                prefix_pos = llm_lower.find(prefix_lower)
                                thought_start = prefix_pos + len(prefix)
                                while thought_start < len(llm_output) and llm_output[thought_start] in " \t":
                                    thought_start += 1
                                break
                        if thought_start > 0 or len(llm_output) > 20:
                            thought_output_started = True
                            thought_output_pos = thought_start

                    if thought_output_started:
                        end_pos = action_pos if action_pos != -1 else len(llm_output)
                        if end_pos > thought_output_pos:
                            new_content = llm_output[thought_output_pos:end_pos]
                            if new_content:
                                yield {"type": "thought_chunk", "data": {"content": new_content, "phase": "thinking"}}
                            thought_output_pos = end_pos

                # 思考结束换行
                if not answer_streaming:
                    yield {"type": "thought_chunk", "data": {"content": "\n\n", "phase": "thinking"}}

                # Token 统计
                token_counter = state.get("token_counter")
                if token_counter:
                    chunk_to_use = usage_chunk if usage_chunk else last_chunk
                    if chunk_to_use:
                        token_counter.update_from_stream_final(chunk_to_use)

                # 解析 Action
                parsed = self.action_parser.parse(llm_output)

                if not parsed.is_valid:
                    logger.warning(f"⚠️ 无效 Action: {parsed.error_message}")
                    entry = ScratchpadEntry(
                        thought=parsed.thought or "（解析失败）",
                        action=parsed.action or "unknown",
                        action_input=parsed.action_input or "",
                        observation=f"[ERROR] {parsed.error_message}",
                    )
                    scratchpad.add_entry(entry)
                    continue

                # 检查 finish
                if self.action_parser.is_finish_action(parsed):
                    final_answer = self.action_parser.extract_final_answer(parsed)
                    logger.info(f"✅ ReAct 完成，迭代次数: {iteration}")
                    break

                # Hook 前处理
                action = parsed.action
                action_input = parsed.action_input

                if self.hook_manager:
                    action, action_input, skip_message = await self.hook_manager.run_pre_hooks(action, action_input, state)
                    if skip_message:
                        logger.info(f"⏭️ Hook 跳过: {skip_message}")
                        entry = ScratchpadEntry(
                            thought=parsed.thought, action=action,
                            action_input=action_input, observation=f"[SKIPPED] {skip_message}",
                        )
                        scratchpad.add_entry(entry)
                        continue

                # Inject state ref into load_skill tool so it can write back runtime info
                if action == "load_skill":
                    self._load_skill_tool.set_state_ref(state)

                # Sync active_skill_runtime from state to run_command tool
                self._run_command_tool.active_skill_runtime = state.get("active_skill_runtime")

                # 执行工具
                observation = await self._execute_tool(action, action_input, state)

                # Hook 后处理
                if self.hook_manager:
                    observation = await self.hook_manager.run_post_hooks(action, action_input, observation, state)

                # 对 load_skill 的 observation 做截断 — 完整内容已通过 LLM prompt 传递，
                # scratchpad 中只需保留确认信息，避免撑爆 token 预算
                scratchpad_observation = observation
                if action == "load_skill" and len(observation) > 500:
                    # 保留前 400 字符（skill 名称 + 开头指令）+ 截断提示
                    scratchpad_observation = (
                        observation[:400]
                        + f"\n\n... [skill 完整指令已加载到上下文，共 {len(observation)} 字符，此处截断以节省 token]"
                    )

                # 记录到 scratchpad
                entry = ScratchpadEntry(
                    thought=parsed.thought, action=action,
                    action_input=action_input, observation=scratchpad_observation,
                )
                scratchpad.add_entry(entry)

            # 达到最大迭代次数
            if not final_answer:
                logger.warning(f"⚠️ 达到最大迭代次数 {self.config.max_iterations}，强制结束")
                final_answer = self._generate_forced_answer(scratchpad, user_query)
                for chunk in self._chunk_text(final_answer, chunk_size=50):
                    yield {"type": "answer_chunk", "data": {"content": chunk}}

            # 保存会话
            if session_id:
                if not state.get("_user_message_saved"):
                    await asyncio.to_thread(
                        self.session_manager.add_user_message, session_id=session_id, content=user_query,
                    )
                await asyncio.to_thread(
                    self.session_manager.add_assistant_message, session_id=session_id, content=final_answer,
                )

                # 压缩检查：会话 token 超过阈值时触发压缩
                try:
                    max_ctx = state.get("max_context_tokens")
                    if max_ctx:
                        from config import get_settings
                        threshold = int(max_ctx * get_settings().compression_threshold_ratio)
                        needs_compress = await asyncio.to_thread(
                            self.session_manager.should_compress, session_id, threshold,
                        )
                        if needs_compress:
                            logger.info(f"🗜️ 触发会话压缩: {session_id}")
                            await asyncio.to_thread(
                                self.session_manager.trigger_compression, session_id, self.llm,
                            )
                except Exception as e:
                    logger.warning(f"Compression check/trigger failed (non-fatal): {e}")

            result = {
                "final_answer": final_answer,
                "react_iteration": iteration,
                "messages": state.get("messages", []) + [AIMessage(content=final_answer)],
            }
            yield {"type": "node_complete", "data": result}

        except Exception as e:
            logger.error(f"Error in react_agent_node_stream: {str(e)}", exc_info=True)
            yield {"type": "node_error", "node": "react_agent", "error": str(e)}

    # ==================================================================
    # 辅助方法
    # ==================================================================

    def _build_document_info(self, state: AgentState) -> str:
        """构建文档信息描述"""
        document_ids = state.get("document_ids", [])
        document_names = state.get("document_names", {}) or {}
        direct_content = state.get("direct_content")

        if not document_ids and not direct_content:
            return "无关联文档"

        parts = []
        if document_ids:
            parts.append(f"关联文档数量: {len(document_ids)}")
            for doc_id in document_ids:
                name = document_names.get(doc_id, doc_id)
                parts.append(f"  - {name} (ID: {doc_id})")
            parts.append("提示: 使用 read_document_outline 查看文档结构，使用 read_document 阅读内容，使用 recall 语义检索。")

        if direct_content:
            parts.append(f"\n直接内容模式（文档内容已注入上下文，共 {len(direct_content)} 字符）:")
            parts.append(direct_content)

        return "\n".join(parts)

    async def _execute_tool(self, action: str, action_input: str, state: AgentState) -> str:
        """执行工具调用"""
        try:
            if action == "recall":
                return await self._execute_recall(action_input, state)
            elif action == "web_search":
                return await self._execute_web_search(action_input)
            elif action == "read_document":
                return await asyncio.wait_for(
                    self._read_doc_tool._arun(action_input), timeout=self.config.tool_timeout,
                )
            elif action == "read_document_outline":
                return await asyncio.wait_for(
                    self._read_outline_tool._arun(action_input), timeout=self.config.tool_timeout,
                )
            elif action == "write_note":
                return await asyncio.wait_for(
                    self._write_note_tool._arun(action_input), timeout=self.config.tool_timeout,
                )
            elif action == "read_note":
                return await asyncio.wait_for(
                    self._read_note_tool._arun(action_input), timeout=self.config.tool_timeout,
                )
            elif action == "load_skill":
                return await asyncio.wait_for(
                    self._load_skill_tool._arun(action_input), timeout=self.config.tool_timeout,
                )
            elif action == "read_skill_resource":
                return await asyncio.wait_for(
                    self._read_skill_resource_tool._arun(action_input), timeout=self.config.tool_timeout,
                )
            elif action == "run_command":
                return await asyncio.wait_for(
                    self._run_command_tool._arun(action_input), timeout=130,
                )
            elif action == "write_file":
                return await asyncio.wait_for(
                    self._write_file_tool._arun(action_input), timeout=30,
                )
            elif action == "append_to_file":
                return await asyncio.wait_for(
                    self._append_to_file_tool._arun(action_input), timeout=30,
                )
            elif action == "upload_to_storage":
                return await asyncio.wait_for(
                    self._upload_to_storage_tool._arun(action_input), timeout=70,
                )
            else:
                # MCP 工具
                registry = get_tool_registry()
                if registry.has_tool(action):
                    return await self._execute_mcp_tool(action, action_input)
                return f"[ERROR] Unknown tool: {action}"
        except asyncio.TimeoutError:
            return f"[ERROR] Tool execution timed out after {self.config.tool_timeout}s"
        except Exception as e:
            logger.error(f"Tool execution error: {str(e)}", exc_info=True)
            return f"[ERROR] Tool execution failed: {str(e)}"

    async def _execute_mcp_tool(self, tool_name: str, tool_input: str) -> str:
        """执行 MCP 工具调用"""
        try:
            registry = get_tool_registry()
            tool = registry.get_tool(tool_name)
            if tool is None:
                return f"[ERROR] MCP tool '{tool_name}' not found"

            import json
            try:
                if tool_input.strip().startswith("{"):
                    kwargs = json.loads(tool_input)
                else:
                    kwargs = {"query": tool_input}
            except json.JSONDecodeError:
                kwargs = {"query": tool_input}

            logger.info(f"🔧 执行 MCP 工具: {tool_name}, 参数: {kwargs}")
            result = await asyncio.wait_for(tool._arun(**kwargs), timeout=self.config.tool_timeout)
            if not result or result.strip() == "":
                return f"MCP tool '{tool_name}' returned no results."
            return result
        except asyncio.TimeoutError:
            return f"[ERROR] MCP tool '{tool_name}' timed out"
        except Exception as e:
            logger.error(f"MCP tool '{tool_name}' error: {str(e)}", exc_info=True)
            return f"[ERROR] MCP tool '{tool_name}' failed: {str(e)}"

    async def _execute_recall(self, query: str, state: AgentState) -> str:
        """执行文档召回"""
        try:
            cache_key = query.strip().lower()
            cached = self._recall_cache.get(cache_key)
            if cached:
                logger.info(f"📋 召回缓存命中: {query[:50]}...")
                return cached
            result = await asyncio.wait_for(
                self.recall_tool._arun(query), timeout=self.config.tool_timeout,
            )
            self._recall_cache.put(cache_key, result)
            return result
        except asyncio.TimeoutError:
            return "[ERROR] Recall timed out"
        except Exception as e:
            logger.error(f"Recall error: {str(e)}", exc_info=True)
            return f"[ERROR] Recall failed: {str(e)}"

    async def _execute_web_search(self, query: str) -> str:
        """执行网络搜索"""
        if not self.web_search_tool:
            return "[ERROR] Web search is not enabled."
        try:
            result = await asyncio.wait_for(
                self.web_search_tool._arun(query), timeout=self.config.tool_timeout,
            )
            return result
        except asyncio.TimeoutError:
            return "[ERROR] Web search timed out"
        except Exception as e:
            logger.error(f"Web search error: {str(e)}", exc_info=True)
            return f"[ERROR] Web search failed: {str(e)}"

    def _generate_forced_answer(self, scratchpad: Scratchpad, user_query: str) -> str:
        """基于已收集信息强制生成答案"""
        info_parts = []
        for entry in scratchpad.entries:
            if entry.observation and not entry.observation.startswith("[ERROR]") and not entry.observation.startswith("[SKIPPED]"):
                info_parts.append(entry.observation[:500])
        if info_parts:
            return f"根据已收集的信息回答：\n\n{''.join(info_parts[:3])}"
        return f"抱歉，我未能找到足够的信息来回答您的问题：{user_query}"

    @staticmethod
    def _chunk_text(text: str, chunk_size: int = 50) -> list:
        """将文本分块"""
        return [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]
