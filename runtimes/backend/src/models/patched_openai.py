"""Compatibility fixes for OpenAI-compatible chat models."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Iterator
from typing import Any

from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatGenerationChunk
from langchain_openai import ChatOpenAI
from langchain_openai.chat_models import base as openai_base

logger = logging.getLogger(__name__)


class PatchedChatOpenAI(ChatOpenAI):
    """ChatOpenAI with Responses API streaming compatibility fixes.

    Some OpenAI-compatible gateways emit a valid Responses API stream but set
    ``response.output`` to ``null`` on the final ``response.completed`` event.
    ``langchain-openai`` expects that field to be a list and raises
    ``TypeError: 'NoneType' object is not iterable`` while converting the final
    chunk. The text/tool deltas have already been streamed by that point, so an
    empty output list is the safest equivalent for the final metadata chunk.
    """

    @staticmethod
    def _patch_null_completed_output(chunk: Any) -> Any:
        if getattr(chunk, "type", None) not in {"response.completed", "response.incomplete"}:
            return chunk

        response = getattr(chunk, "response", None)
        if response is None or getattr(response, "output", None) is not None:
            return chunk

        try:
            patched_response = response.model_copy(update={"output": []})
            return chunk.model_copy(update={"response": patched_response})
        except Exception as exc:
            logger.debug(
                "Unable to copy Responses API final chunk with empty output (%s)",
                type(exc).__name__,
            )
            try:
                response.output = []
            except Exception as mutation_exc:
                logger.debug(
                    "Unable to mutate Responses API final chunk output (%s)",
                    type(mutation_exc).__name__,
                )
            return chunk

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        if self._use_responses_api({**kwargs, **self.model_kwargs}):
            async for chunk in self._astream_responses_compat(
                messages,
                stop=stop,
                run_manager=run_manager,
                **kwargs,
            ):
                yield chunk
        else:
            async for chunk in super()._astream(
                messages,
                stop=stop,
                run_manager=run_manager,
                **kwargs,
            ):
                yield chunk

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        if self._use_responses_api({**kwargs, **self.model_kwargs}):
            yield from self._stream_responses_compat(
                messages,
                stop=stop,
                run_manager=run_manager,
                **kwargs,
            )
        else:
            yield from super()._stream(
                messages,
                stop=stop,
                run_manager=run_manager,
                **kwargs,
            )

    def _stream_responses_compat(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        self._ensure_sync_client_available()
        kwargs["stream"] = True
        payload = self._get_request_payload(messages, stop=stop, **kwargs)
        if self.include_response_headers:
            raw_context_manager = self.root_client.with_raw_response.responses.create(**payload)
            context_manager = raw_context_manager.parse()
            headers = {"headers": dict(raw_context_manager.headers)}
        else:
            context_manager = self.root_client.responses.create(**payload)
            headers = {}
        original_schema_obj = kwargs.get("response_format")

        with context_manager as response:
            is_first_chunk = True
            current_index = -1
            current_output_index = -1
            current_sub_index = -1
            has_reasoning = False
            for chunk in response:
                chunk = self._patch_null_completed_output(chunk)
                metadata = headers if is_first_chunk else {}
                (
                    current_index,
                    current_output_index,
                    current_sub_index,
                    generation_chunk,
                ) = openai_base._convert_responses_chunk_to_generation_chunk(
                    chunk,
                    current_index,
                    current_output_index,
                    current_sub_index,
                    schema=original_schema_obj,
                    metadata=metadata,
                    has_reasoning=has_reasoning,
                    output_version=self.output_version,
                )
                if generation_chunk:
                    if run_manager:
                        run_manager.on_llm_new_token(
                            generation_chunk.text,
                            chunk=generation_chunk,
                        )
                    is_first_chunk = False
                    if "reasoning" in generation_chunk.message.additional_kwargs:
                        has_reasoning = True
                    yield generation_chunk

    async def _astream_responses_compat(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        kwargs["stream"] = True
        payload = self._get_request_payload(messages, stop=stop, **kwargs)
        if self.include_response_headers:
            raw_context_manager = await self.root_async_client.with_raw_response.responses.create(**payload)
            context_manager = raw_context_manager.parse()
            headers = {"headers": dict(raw_context_manager.headers)}
        else:
            context_manager = await self.root_async_client.responses.create(**payload)
            headers = {}
        original_schema_obj = kwargs.get("response_format")

        async with context_manager as response:
            is_first_chunk = True
            current_index = -1
            current_output_index = -1
            current_sub_index = -1
            has_reasoning = False
            async for chunk in response:
                chunk = self._patch_null_completed_output(chunk)
                metadata = headers if is_first_chunk else {}
                (
                    current_index,
                    current_output_index,
                    current_sub_index,
                    generation_chunk,
                ) = openai_base._convert_responses_chunk_to_generation_chunk(
                    chunk,
                    current_index,
                    current_output_index,
                    current_sub_index,
                    schema=original_schema_obj,
                    metadata=metadata,
                    has_reasoning=has_reasoning,
                    output_version=self.output_version,
                )
                if generation_chunk:
                    if run_manager:
                        await run_manager.on_llm_new_token(
                            generation_chunk.text,
                            chunk=generation_chunk,
                        )
                    is_first_chunk = False
                    if "reasoning" in generation_chunk.message.additional_kwargs:
                        has_reasoning = True
                    yield generation_chunk
