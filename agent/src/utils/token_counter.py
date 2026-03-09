"""
Token counter based on API response values.

This module provides token counting functionality that extracts actual token usage
from LLM API responses instead of using local tokenizer estimation.
"""
from typing import Optional, Any
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class ApiFormat(Enum):
    """Supported API format types."""
    OPENAI = "openai"
    ANTHROPIC = "anthropic"


class TokenCounter:
    """
    Token counter based on API response values.
    
    Extracts token usage from LLM API responses for both OpenAI and Anthropic formats.
    Supports streaming with fallback estimation for interrupted streams.
    """
    
    def __init__(self, api_format: str = "openai"):
        """
        Initialize TokenCounter.
        
        Args:
            api_format: API format type, either "openai" or "anthropic"
        
        Raises:
            ValueError: If api_format is not a valid format
        """
        try:
            self.api_format = ApiFormat(api_format)
        except ValueError:
            raise ValueError(f"Invalid api_format: {api_format}. Must be 'openai' or 'anthropic'")
        
        self.input_tokens = 0
        self.output_tokens = 0
        self._accumulated_text = ""  # Accumulated stream text for fallback estimation
        self._stream_completed = False
    
    def update_from_response(self, response: Any) -> None:
        """
        Extract token usage from a non-streaming response.
        
        Supports multiple formats:
        - LangChain AIMessage/AIMessageChunk objects (usage_metadata attribute)
        - Raw OpenAI/Anthropic API responses (usage attribute)
        - LangChain response_metadata (nested usage)
        - LangChain additional_kwargs (nested usage)
        
        Args:
            response: LLM API response object with usage or usage_metadata field
        """
        # 1. First try LangChain's usage_metadata (for AIMessage/AIMessageChunk)
        usage_metadata = getattr(response, 'usage_metadata', None)
        if usage_metadata:
            # LangChain uses 'input_tokens' and 'output_tokens' keys
            if isinstance(usage_metadata, dict):
                input_tokens = usage_metadata.get('input_tokens', 0) or 0
                output_tokens = usage_metadata.get('output_tokens', 0) or 0
            else:
                input_tokens = getattr(usage_metadata, 'input_tokens', 0) or 0
                output_tokens = getattr(usage_metadata, 'output_tokens', 0) or 0
            
            if input_tokens > 0 or output_tokens > 0:
                logger.info(f"TokenCounter: ✅ Extracted from usage_metadata: input={input_tokens}, output={output_tokens}")
                self.input_tokens += input_tokens
                self.output_tokens += output_tokens
                return
        
        # 2. Try response_metadata (some LangChain versions)
        response_metadata = getattr(response, 'response_metadata', None)
        if response_metadata and isinstance(response_metadata, dict):
            # Check for 'usage' or 'token_usage' in response_metadata
            usage_data = response_metadata.get('usage') or response_metadata.get('token_usage')
            if usage_data:
                if isinstance(usage_data, dict):
                    # OpenAI format in response_metadata
                    input_tokens = usage_data.get('prompt_tokens', 0) or usage_data.get('input_tokens', 0) or 0
                    output_tokens = usage_data.get('completion_tokens', 0) or usage_data.get('output_tokens', 0) or 0
                else:
                    input_tokens = getattr(usage_data, 'prompt_tokens', 0) or getattr(usage_data, 'input_tokens', 0) or 0
                    output_tokens = getattr(usage_data, 'completion_tokens', 0) or getattr(usage_data, 'output_tokens', 0) or 0
                
                if input_tokens > 0 or output_tokens > 0:
                    logger.info(f"TokenCounter: ✅ Extracted from response_metadata: input={input_tokens}, output={output_tokens}")
                    self.input_tokens += input_tokens
                    self.output_tokens += output_tokens
                    return
        
        # 3. Try additional_kwargs (some LangChain versions)
        additional_kwargs = getattr(response, 'additional_kwargs', None)
        if additional_kwargs and isinstance(additional_kwargs, dict):
            usage_data = additional_kwargs.get('usage')
            if usage_data:
                if isinstance(usage_data, dict):
                    input_tokens = usage_data.get('prompt_tokens', 0) or usage_data.get('input_tokens', 0) or 0
                    output_tokens = usage_data.get('completion_tokens', 0) or usage_data.get('output_tokens', 0) or 0
                else:
                    input_tokens = getattr(usage_data, 'prompt_tokens', 0) or getattr(usage_data, 'input_tokens', 0) or 0
                    output_tokens = getattr(usage_data, 'completion_tokens', 0) or getattr(usage_data, 'output_tokens', 0) or 0
                
                if input_tokens > 0 or output_tokens > 0:
                    logger.info(f"TokenCounter: ✅ Extracted from additional_kwargs: input={input_tokens}, output={output_tokens}")
                    self.input_tokens += input_tokens
                    self.output_tokens += output_tokens
                    return
        
        # 4. Fallback to raw API response format (usage attribute)
        usage = getattr(response, 'usage', None)
        if not usage:
            # No usage info available in this response
            logger.debug(f"TokenCounter: No usage info found in response")
            return
        
        if self.api_format == ApiFormat.ANTHROPIC:
            prompt_tokens = getattr(usage, 'input_tokens', 0) or 0
            completion_tokens = getattr(usage, 'output_tokens', 0) or 0
        else:  # OpenAI format
            prompt_tokens = getattr(usage, 'prompt_tokens', 0) or 0
            completion_tokens = getattr(usage, 'completion_tokens', 0) or 0
        
        if prompt_tokens > 0 or completion_tokens > 0:
            logger.info(f"TokenCounter: ✅ Extracted from usage attribute: input={prompt_tokens}, output={completion_tokens}")
            self.input_tokens += prompt_tokens
            self.output_tokens += completion_tokens
    
    def accumulate_stream_text(self, text: str) -> None:
        """
        Accumulate streaming output text for fallback estimation.
        
        Call this for each chunk of streaming output to track accumulated text
        in case the stream is interrupted and we need to estimate tokens.
        
        Args:
            text: Text chunk from streaming response
        """
        self._accumulated_text += text
    
    def update_from_stream_final(self, chunk: Any) -> None:
        """
        Extract token usage from the final streaming chunk.
        
        Call this when streaming completes normally to get actual token counts.
        
        Args:
            chunk: Final chunk from streaming response with usage field
        """
        self._stream_completed = True
        
        # Detailed debug logging to diagnose token tracking issues
        if chunk and logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"TokenCounter: Processing chunk, type={type(chunk).__name__}")
            
            # Log all attributes of the chunk for debugging
            all_attrs = [attr for attr in dir(chunk) if not attr.startswith('_')]
            logger.debug(f"TokenCounter: Chunk attributes: {all_attrs}")
            
            # Check for usage_metadata (LangChain format)
            usage_metadata = getattr(chunk, 'usage_metadata', None)
            if usage_metadata:
                logger.debug(f"TokenCounter: ✅ Found usage_metadata={usage_metadata}, type={type(usage_metadata)}")
            else:
                logger.debug(f"TokenCounter: ❌ No usage_metadata found")
            
            # Check for usage (raw API format)
            usage = getattr(chunk, 'usage', None)
            if usage:
                logger.debug(f"TokenCounter: ✅ Found usage={usage}, type={type(usage)}")
            else:
                logger.debug(f"TokenCounter: ❌ No usage found")
            
            # Check for response_metadata (another LangChain format)
            response_metadata = getattr(chunk, 'response_metadata', None)
            if response_metadata:
                logger.debug(f"TokenCounter: Found response_metadata={response_metadata}")
                # Check if usage is nested in response_metadata
                if isinstance(response_metadata, dict):
                    if 'usage' in response_metadata:
                        logger.debug(f"TokenCounter: ✅ Found usage in response_metadata: {response_metadata['usage']}")
                    if 'token_usage' in response_metadata:
                        logger.debug(f"TokenCounter: ✅ Found token_usage in response_metadata: {response_metadata['token_usage']}")
            
            # Check for additional_kwargs (some LangChain versions use this)
            additional_kwargs = getattr(chunk, 'additional_kwargs', None)
            if additional_kwargs:
                logger.debug(f"TokenCounter: Found additional_kwargs={additional_kwargs}")
                if isinstance(additional_kwargs, dict) and 'usage' in additional_kwargs:
                    logger.debug(f"TokenCounter: ✅ Found usage in additional_kwargs: {additional_kwargs['usage']}")
        
        self.update_from_response(chunk)
    
    def finalize_on_interrupt(self) -> None:
        """
        Finalize token counting when streaming is interrupted.
        
        Uses estimation (len/4) for output tokens when stream doesn't complete normally.
        Should be called when streaming is cancelled, times out, or encounters an error.
        """
        if not self._stream_completed and self._accumulated_text:
            # Estimate: characters / 4
            estimated_output = len(self._accumulated_text) // 4
            self.output_tokens += estimated_output
        self._reset_stream_state()
    
    def _reset_stream_state(self) -> None:
        """Reset streaming state for next stream."""
        self._accumulated_text = ""
        self._stream_completed = False
    
    @property
    def total_tokens(self) -> int:
        """Get total token count (input + output)."""
        return self.input_tokens + self.output_tokens
    
    def reset(self) -> None:
        """Reset all token counts and state."""
        self.input_tokens = 0
        self.output_tokens = 0
        self._reset_stream_state()
    
    @staticmethod
    def estimate_tokens(text: str) -> int:
        """
        Simple token estimation for fallback scenarios.
        
        Uses character count / 4 as a rough approximation.
        Only use when API token counts are unavailable.
        
        Args:
            text: Text to estimate tokens for
            
        Returns:
            Estimated token count
        """
        if not text:
            return 0
        return len(text) // 4
