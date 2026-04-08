"""修补版 ChatDeepSeek：在多轮对话中保留 reasoning_content。

该模块提供了 ChatDeepSeek 的修补实现，用于在消息回传 API 时
正确处理 reasoning_content。原实现会将 reasoning_content 存在
additional_kwargs 中，但后续请求未带回该字段；对于在思考模式下
要求每条 assistant 消息都包含 reasoning_content 的 API，会因此报错。
"""

from typing import Any

from langchain_core.language_models import LanguageModelInput
from langchain_core.messages import AIMessage
from langchain_deepseek import ChatDeepSeek


class PatchedChatDeepSeek(ChatDeepSeek):
    """可正确保留 reasoning_content 的 ChatDeepSeek。

    对启用 thinking/reasoning 的模型，API 要求多轮会话中所有 assistant
    消息都携带 reasoning_content。本实现会将 additional_kwargs 中的
    reasoning_content 注入请求 payload，满足该要求。
    """

    def _get_request_payload(
        self,
        input_: LanguageModelInput,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> dict:
        """获取包含 reasoning_content 的请求 payload。

        通过覆写父类方法，将 additional_kwargs 中的 reasoning_content
        注入到 payload 的 assistant 消息中。
        """
        # 转换前先拿到原始消息列表
        original_messages = self._convert_input(input_).to_messages()

        # 调用父类方法生成基础 payload
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)

        # 将 payload 消息与原始消息对齐，恢复 reasoning_content
        payload_messages = payload.get("messages", [])

        # 正常情况下两者顺序一致，可按位置一一匹配
        if len(payload_messages) == len(original_messages):
            for payload_msg, orig_msg in zip(payload_messages, original_messages):
                if payload_msg.get("role") == "assistant" and isinstance(orig_msg, AIMessage):
                    reasoning_content = orig_msg.additional_kwargs.get("reasoning_content")
                    if reasoning_content is not None:
                        payload_msg["reasoning_content"] = reasoning_content
        else:
            # 回退方案：按 assistant 消息序号匹配
            ai_messages = [m for m in original_messages if isinstance(m, AIMessage)]
            assistant_payloads = [(i, m) for i, m in enumerate(payload_messages) if m.get("role") == "assistant"]

            for (idx, payload_msg), ai_msg in zip(assistant_payloads, ai_messages):
                reasoning_content = ai_msg.additional_kwargs.get("reasoning_content")
                if reasoning_content is not None:
                    payload_messages[idx]["reasoning_content"] = reasoning_content

        return payload
