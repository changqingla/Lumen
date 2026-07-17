"""Summarization middleware that preserves provider usage accounting."""

from __future__ import annotations

import logging
from contextvars import ContextVar
from typing import Any, override

from langchain.agents.middleware import SummarizationMiddleware
from langchain_core.messages import HumanMessage, get_buffer_string

from src.usage import report_model_response_async, report_model_response_sync

logger = logging.getLogger(__name__)

_current_usage_context: ContextVar[Any] = ContextVar(
    "summary_usage_context",
    default=None,
)


class UsageSummarizationMiddleware(SummarizationMiddleware):
    """Attach the current run identity to SummarizationMiddleware's private call."""

    @override
    async def abefore_model(self, state, runtime):
        token = _current_usage_context.set(runtime.context)
        try:
            return await super().abefore_model(state, runtime)
        finally:
            _current_usage_context.reset(token)

    @override
    def before_model(self, state, runtime):
        token = _current_usage_context.set(runtime.context)
        try:
            return super().before_model(state, runtime)
        finally:
            _current_usage_context.reset(token)

    async def _acreate_summary(self, messages_to_summarize):
        if not messages_to_summarize:
            return "No previous conversation history."
        trimmed_messages = self._trim_messages_for_summary(messages_to_summarize)
        if not trimmed_messages:
            return "Previous conversation was too long to summarize."
        prompt = self.summary_prompt.format(
            messages=get_buffer_string(trimmed_messages)
        ).rstrip()
        try:
            response = await self.model.ainvoke(
                prompt,
                config={"metadata": {"lc_source": "summarization"}},
            )
        except Exception as exc:
            logger.warning(
                "Summary generation failed (%s)",
                type(exc).__name__,
            )
            return "Summary generation failed; previous conversation remains available."
        await report_model_response_async(
            context=_current_usage_context.get(),
            response=response,
            model=self.model,
            request_type="summary",
            request_messages=[HumanMessage(content=prompt)],
        )
        return response.text.strip()

    def _create_summary(self, messages_to_summarize):
        if not messages_to_summarize:
            return "No previous conversation history."
        trimmed_messages = self._trim_messages_for_summary(messages_to_summarize)
        if not trimmed_messages:
            return "Previous conversation was too long to summarize."
        prompt = self.summary_prompt.format(
            messages=get_buffer_string(trimmed_messages)
        ).rstrip()
        try:
            response = self.model.invoke(
                prompt,
                config={"metadata": {"lc_source": "summarization"}},
            )
        except Exception as exc:
            logger.warning(
                "Summary generation failed (%s)",
                type(exc).__name__,
            )
            return "Summary generation failed; previous conversation remains available."
        report_model_response_sync(
            context=_current_usage_context.get(),
            response=response,
            model=self.model,
            request_type="summary",
            request_messages=[HumanMessage(content=prompt)],
        )
        return response.text.strip()
