"""在 LLM 调用前向会话注入图片细节的中间件。"""

from typing import Annotated, NotRequired, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.runtime import Runtime

from src.agents.thread_state import ViewedImageData, merge_viewed_images


class ViewImageMiddlewareState(AgentState):
    """与 `ThreadState` 模式兼容。"""

    viewed_images: Annotated[NotRequired[dict[str, ViewedImageData] | None], merge_viewed_images]


class ViewImageMiddleware(AgentMiddleware[ViewImageMiddlewareState]):
    """负责在模型调用前补充图片上下文。

    该中间件会：
    1. 在每次 LLM 调用前执行
    2. 检查最近一条 assistant 消息是否包含 `view_image` 工具调用
    3. 验证该消息中的工具调用是否都已完成（存在对应 ToolMessage）
    4. 条件满足时构造包含已查看图片细节（含 base64 数据）的 human 消息
    5. 将消息写入状态，使 LLM 能直接“看到”并分析图片

    这样模型可自动接收并分析通过 `view_image` 读取的图片，
    无需用户再次显式要求“描述这张图”。
    """

    state_schema = ViewImageMiddlewareState

    def _get_last_assistant_message(self, messages: list) -> AIMessage | None:
        """获取最后一条 AIMessage。

        参数：
            messages: 消息列表。

        返回：
            最后一条 AIMessage；若不存在则返回 None。
        """
        for msg in reversed(messages):
            if isinstance(msg, AIMessage):
                return msg
        return None

    def _has_view_image_tool(self, message: AIMessage) -> bool:
        """判断 assistant 消息是否包含 `view_image` 工具调用。

        参数：
            message: 待检查的 assistant 消息。

        返回：
            若包含 `view_image` 工具调用则为 True。
        """
        if not hasattr(message, "tool_calls") or not message.tool_calls:
            return False

        return any(tool_call.get("name") == "view_image" for tool_call in message.tool_calls)

    def _all_tools_completed(self, messages: list, assistant_msg: AIMessage) -> bool:
        """检查目标消息中的工具调用是否全部完成。

        参数：
            messages: 全量消息列表。
            assistant_msg: 包含工具调用的 assistant 消息。

        返回：
            若所有工具调用均有对应 ToolMessage，则返回 True。
        """
        if not hasattr(assistant_msg, "tool_calls") or not assistant_msg.tool_calls:
            return False

        # 收集 assistant 消息中的全部 tool_call_id
        tool_call_ids = {tool_call.get("id") for tool_call in assistant_msg.tool_calls if tool_call.get("id")}

        # 定位该 assistant 消息在列表中的位置
        try:
            assistant_idx = messages.index(assistant_msg)
        except ValueError:
            return False

        # 收集其后的全部 ToolMessage 对应 ID
        completed_tool_ids = set()
        for msg in messages[assistant_idx + 1 :]:
            if isinstance(msg, ToolMessage) and msg.tool_call_id:
                completed_tool_ids.add(msg.tool_call_id)

        # 判断是否所有调用都已完成
        return tool_call_ids.issubset(completed_tool_ids)

    def _create_image_details_message(self, state: ViewImageMiddlewareState) -> list[str | dict]:
        """构建图片细节消息内容块。

        参数：
            state: 当前状态，需包含 `viewed_images`。

        返回：
            可用于 HumanMessage 的内容块列表（文本 + 图片）。
        """
        viewed_images = state.get("viewed_images", {})
        if not viewed_images:
            return ["No images have been viewed."]

        # 组合“图片信息”消息体
        content_blocks: list[str | dict] = [{"type": "text", "text": "Here are the images you've viewed:"}]

        for image_path, image_data in viewed_images.items():
            mime_type = image_data.get("mime_type", "unknown")
            base64_data = image_data.get("base64", "")

            # 添加文本描述
            content_blocks.append({"type": "text", "text": f"\n- **{image_path}** ({mime_type})"})

            # 添加实际图片数据，供 LLM 视觉解析
            if base64_data:
                content_blocks.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime_type};base64,{base64_data}"},
                    }
                )

        return content_blocks

    def _should_inject_image_message(self, state: ViewImageMiddlewareState) -> bool:
        """判断当前回合是否需要注入图片细节消息。

        参数：
            state: 当前状态。

        返回：
            需要注入时返回 True。
        """
        messages = state.get("messages", [])
        if not messages:
            return False

        # 获取最后一条 assistant 消息
        last_assistant_msg = self._get_last_assistant_message(messages)
        if not last_assistant_msg:
            return False

        # 检查是否包含 view_image 工具调用
        if not self._has_view_image_tool(last_assistant_msg):
            return False

        # 检查相关工具调用是否均已完成
        if not self._all_tools_completed(messages, last_assistant_msg):
            return False

        # 检查是否已注入过图片细节消息
        # 在最后一条 assistant 消息后查找携带图片说明的 human 消息
        assistant_idx = messages.index(last_assistant_msg)
        for msg in messages[assistant_idx + 1 :]:
            if isinstance(msg, HumanMessage):
                content_str = str(msg.content)
                if "Here are the images you've viewed" in content_str or "Here are the details of the images you've viewed" in content_str:
                    # 已注入过则不重复添加
                    return False

        return True

    def _inject_image_message(self, state: ViewImageMiddlewareState) -> dict | None:
        """执行图片细节消息注入。

        参数：
            state: 当前状态。

        返回：
            需要注入时返回包含新增 human 消息的状态更新，否则返回 None。
        """
        if not self._should_inject_image_message(state):
            return None

        # 构建包含文本与图片内容的细节消息
        image_content = self._create_image_details_message(state)

        # 创建混合内容（文本 + 图片）的 human 消息
        human_msg = HumanMessage(content=image_content)

        print("[ViewImageMiddleware] Injecting image details message with images before LLM call")

        # 返回包含新增消息的状态更新
        return {"messages": [human_msg]}

    @override
    def before_model(self, state: ViewImageMiddlewareState, runtime: Runtime) -> dict | None:
        """同步钩子：在模型调用前按需注入图片细节消息。

        会检查上一轮 `view_image` 工具调用是否全部完成；
        若完成则注入一条含图片细节的 human 消息，供模型分析。

        参数：
            state: 当前状态。
            runtime: 运行时上下文（接口要求，当前未使用）。

        返回：
            需要注入时返回状态更新，否则返回 None。
        """
        return self._inject_image_message(state)

    @override
    async def abefore_model(self, state: ViewImageMiddlewareState, runtime: Runtime) -> dict | None:
        """异步钩子：在模型调用前按需注入图片细节消息。

        逻辑与 `before_model` 一致，仅用于异步调用链。

        参数：
            state: 当前状态。
            runtime: 运行时上下文（接口要求，当前未使用）。

        返回：
            需要注入时返回状态更新，否则返回 None。
        """
        return self._inject_image_message(state)
