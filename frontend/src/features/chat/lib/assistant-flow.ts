import type { ChatToolTrace } from '@/shared/api/client';

export interface AssistantTupleToolCall {
  id?: string;
  name: string;
  args?: unknown;
}

export interface AssistantTupleMessage {
  type: 'ai' | 'tool';
  id: string;
  content?: string;
  tool_calls?: AssistantTupleToolCall[];
  tool_call_id?: string;
  name?: string;
  status?: string;
}

export interface AssistantFlowContentBlock {
  type: 'content';
  id: string;
  content: string;
}

export interface AssistantFlowProcessingBlock {
  type: 'processing';
  id: string;
  messages: AssistantTupleMessage[];
}

export type AssistantFlowBlock =
  | AssistantFlowContentBlock
  | AssistantFlowProcessingBlock;

const trimLeadingBoundaryBlankLines = (value: string): string => (
  value.replace(/^(?:[ \t]*\r?\n)+/, '')
);

const trimTrailingBoundaryBlankLines = (value: string): string => (
  value.replace(/(?:\r?\n[ \t]*)+$/, '')
);

export const getToolTraceStableKey = (trace: ChatToolTrace): string => {
  const callId = (trace.call_id || '').trim();
  if (callId) {
    return `call:${callId}`;
  }
  return `iter:${trace.iteration || 0}|name:${trace.name}`;
};

const stringifyToolResultPayload = (trace: ChatToolTrace): string => {
  const payload: Record<string, unknown> = {};
  if (trace.result !== undefined) {
    payload.result = trace.result;
  }
  if (trace.error) {
    payload.error = trace.error;
  }
  if (typeof trace.success === 'boolean') {
    payload.success = trace.success;
  }
  if (trace.status) {
    payload.status = trace.status;
  }
  if (typeof trace.duration_ms === 'number') {
    payload.duration_ms = trace.duration_ms;
  }
  if (Object.keys(payload).length === 0) {
    return '';
  }
  return JSON.stringify(payload);
};

const parseToolMessageContent = (content: string | undefined): Record<string, unknown> | null => {
  const raw = (content || '').trim();
  if (!raw) {
    return null;
  }
  try {
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      return parsed as Record<string, unknown>;
    }
  } catch {
    // ignore JSON parse failure and fallback to plain text
  }
  return { result: raw };
};

const normalizeTupleToolCallId = (
  toolCall: AssistantTupleToolCall,
  fallbackIndex: number,
): string => {
  const normalized = (toolCall.id || '').trim();
  if (normalized) {
    return normalized;
  }
  return `generated:${toolCall.name}:${fallbackIndex}`;
};

const resolveTraceStatus = (trace: ChatToolTrace): string | undefined => {
  if (trace.status && trace.status.trim()) {
    return trace.status.trim();
  }
  if (trace.success === true) {
    return 'success';
  }
  if (trace.success === false || (trace.error && trace.error.trim())) {
    return 'error';
  }
  if (trace.result !== undefined) {
    return 'completed';
  }
  return 'running';
};

export const buildHistoryAssistantTupleMessages = (
  role: 'user' | 'assistant',
  content: string,
  toolTraces: ChatToolTrace[],
): AssistantTupleMessage[] | undefined => {
  if (role !== 'assistant') {
    return undefined;
  }
  const tupleMessages: AssistantTupleMessage[] = [];
  if (content) {
    tupleMessages.push({
      type: 'ai',
      id: 'history-ai-content',
      content,
    });
  }

  toolTraces.forEach((trace, index) => {
    const callId = getToolTraceStableKey(trace);
    tupleMessages.push({
      type: 'ai',
      id: `history-ai-tool-${index}-${callId}`,
      content: '',
      tool_calls: [
        {
          id: callId,
          name: trace.name,
          args: trace.args,
        },
      ],
    });
    tupleMessages.push({
      type: 'tool',
      id: `history-tool-${index}-${callId}`,
      tool_call_id: callId,
      name: trace.name,
      content: stringifyToolResultPayload(trace),
      status: resolveTraceStatus(trace),
    });
  });

  return tupleMessages.length > 0 ? tupleMessages : undefined;
};

export const appendTupleContentMessage = (
  tupleMessages: AssistantTupleMessage[] | undefined,
  token: string,
): AssistantTupleMessage[] => {
  const normalized = token || '';
  if (!normalized) {
    return tupleMessages ? [...tupleMessages] : [];
  }

  const next = tupleMessages ? [...tupleMessages] : [];
  const last = next[next.length - 1];
  if (last && last.type === 'ai' && (!last.tool_calls || last.tool_calls.length === 0)) {
    next[next.length - 1] = {
      ...last,
      content: `${last.content || ''}${normalized}`,
    };
    return next;
  }

  next.push({
    type: 'ai',
    id: `ai-content-${next.length}`,
    content: normalized,
  });
  return next;
};

export const upsertTupleContentMessage = (
  tupleMessages: AssistantTupleMessage[] | undefined,
  content: string,
): AssistantTupleMessage[] => {
  const normalized = content || '';
  if (!normalized) {
    return tupleMessages ? [...tupleMessages] : [];
  }

  const next = tupleMessages ? [...tupleMessages] : [];
  let lastContentIndex = -1;
  next.forEach((item, index) => {
    if (item.type === 'ai' && (!item.tool_calls || item.tool_calls.length === 0)) {
      lastContentIndex = index;
    }
  });

  if (lastContentIndex >= 0) {
    next[lastContentIndex] = {
      ...next[lastContentIndex],
      content: normalized,
    };
    return next;
  }

  next.push({
    type: 'ai',
    id: `ai-content-${next.length}`,
    content: normalized,
  });
  return next;
};

export const upsertTupleToolCallMessage = (
  tupleMessages: AssistantTupleMessage[] | undefined,
  toolCall: AssistantTupleToolCall,
): AssistantTupleMessage[] => {
  const next = tupleMessages ? [...tupleMessages] : [];
  const callId = normalizeTupleToolCallId(toolCall, next.length);
  const existingIndex = next.findIndex((item) => (
    item.type === 'ai'
    && (item.tool_calls || []).some((candidate) => normalizeTupleToolCallId(candidate, 0) === callId)
  ));

  const normalizedToolCall: AssistantTupleToolCall = {
    ...toolCall,
    id: callId,
  };

  if (existingIndex >= 0) {
    const existing = next[existingIndex];
    next[existingIndex] = {
      ...existing,
      tool_calls: [normalizedToolCall],
    };
    return next;
  }

  next.push({
    type: 'ai',
    id: `ai-tool-${callId}`,
    content: '',
    tool_calls: [normalizedToolCall],
  });
  return next;
};

export const upsertTupleToolResultMessage = (
  tupleMessages: AssistantTupleMessage[] | undefined,
  params: {
    toolCallId: string;
    toolName: string;
    content: string;
    status?: string;
  },
): AssistantTupleMessage[] => {
  const {
    toolCallId,
    toolName,
    content,
    status,
  } = params;
  const next = tupleMessages ? [...tupleMessages] : [];
  const existingIndex = next.findIndex((item) => (
    item.type === 'tool' && (item.tool_call_id || '') === toolCallId
  ));

  const normalized: AssistantTupleMessage = {
    type: 'tool',
    id: `tool-${toolCallId}`,
    tool_call_id: toolCallId,
    name: toolName,
    content,
  };
  if (status) {
    normalized.status = status;
  }

  if (existingIndex >= 0) {
    next[existingIndex] = normalized;
    return next;
  }

  next.push(normalized);
  return next;
};

export const groupAssistantTupleMessages = (
  tupleMessages: AssistantTupleMessage[],
): AssistantFlowBlock[] => {
  if (!tupleMessages.length) {
    return [];
  }

  const blocks: AssistantFlowBlock[] = [];
  let processingBuffer: AssistantTupleMessage[] = [];

  const flushProcessing = () => {
    if (!processingBuffer.length) {
      return;
    }
    blocks.push({
      type: 'processing',
      id: `processing-${blocks.length}`,
      messages: processingBuffer,
    });
    processingBuffer = [];
  };

  tupleMessages.forEach((message) => {
    const hasToolCalls = message.type === 'ai' && (message.tool_calls || []).length > 0;
    const isToolMessage = message.type === 'tool';
    const isAiContent = (
      message.type === 'ai'
      && !hasToolCalls
      && typeof message.content === 'string'
      && message.content.length > 0
    );

    if (isAiContent) {
      flushProcessing();
      const content = message.content || '';
      const last = blocks[blocks.length - 1];
      if (last && last.type === 'content') {
        last.content = `${last.content}${content}`;
      } else {
        blocks.push({
          type: 'content',
          id: message.id,
          content,
        });
      }
      return;
    }

    if (hasToolCalls || isToolMessage) {
      processingBuffer.push(message);
    }
  });

  flushProcessing();

  return blocks.reduce<AssistantFlowBlock[]>((acc, block, index) => {
    if (block.type !== 'content') {
      acc.push(block);
      return acc;
    }

    let normalizedContent = block.content;
    const previousBlock = blocks[index - 1];
    const nextBlock = blocks[index + 1];

    if (previousBlock?.type === 'processing') {
      normalizedContent = trimLeadingBoundaryBlankLines(normalizedContent);
    }
    if (nextBlock?.type === 'processing') {
      normalizedContent = trimTrailingBoundaryBlankLines(normalizedContent);
    }

    if (!normalizedContent) {
      return acc;
    }

    acc.push({
      ...block,
      content: normalizedContent,
    });
    return acc;
  }, []);
};

export const buildProcessingBlockTraces = (
  processingMessages: AssistantTupleMessage[],
  fallbackToolTraces: ChatToolTrace[] = [],
): ChatToolTrace[] => {
  if (!processingMessages.length) {
    return [];
  }

  const fallbackByCallId = new Map<string, ChatToolTrace>();
  fallbackToolTraces.forEach((trace) => {
    const callId = (trace.call_id || '').trim();
    if (callId) {
      fallbackByCallId.set(callId, trace);
    }
  });

  const toolResultByCallId = new Map<string, AssistantTupleMessage>();
  processingMessages.forEach((message) => {
    if (message.type !== 'tool') {
      return;
    }
    const callId = (message.tool_call_id || '').trim();
    if (!callId) {
      return;
    }
    toolResultByCallId.set(callId, message);
  });

  const traces: ChatToolTrace[] = [];
  let generatedIndex = 0;

  processingMessages.forEach((message) => {
    if (message.type !== 'ai') {
      return;
    }

    (message.tool_calls || []).forEach((toolCall) => {
      const callId = normalizeTupleToolCallId(toolCall, generatedIndex);
      generatedIndex += 1;
      const fallback = fallbackByCallId.get(callId);
      const toolMessage = toolResultByCallId.get(callId);
      const parsedToolContent = parseToolMessageContent(toolMessage?.content);

      const trace: ChatToolTrace = {
        name: toolCall.name,
        call_id: callId,
        args: toolCall.args,
      };

      if (fallback?.iteration) {
        trace.iteration = fallback.iteration;
      }
      if (fallback?.duration_ms !== undefined) {
        trace.duration_ms = fallback.duration_ms;
      }

      if (parsedToolContent) {
        if (Object.prototype.hasOwnProperty.call(parsedToolContent, 'result')) {
          trace.result = parsedToolContent.result;
        }
        if (Object.prototype.hasOwnProperty.call(parsedToolContent, 'error')) {
          trace.error = String(parsedToolContent.error || '');
        }
        if (Object.prototype.hasOwnProperty.call(parsedToolContent, 'success')) {
          trace.success = Boolean(parsedToolContent.success);
        }
        if (Object.prototype.hasOwnProperty.call(parsedToolContent, 'status')) {
          trace.status = String(parsedToolContent.status || '');
        }
        if (Object.prototype.hasOwnProperty.call(parsedToolContent, 'duration_ms')) {
          const durationRaw = parsedToolContent.duration_ms;
          if (typeof durationRaw === 'number' && durationRaw >= 0) {
            trace.duration_ms = durationRaw;
          }
        }
        if (!trace.status && toolMessage?.status) {
          trace.status = toolMessage.status;
        }
        if (
          toolMessage
          && !trace.status
          && typeof trace.success !== 'boolean'
          && !trace.error
          && trace.result !== undefined
        ) {
          trace.status = 'completed';
          trace.success = true;
        }
      } else if (fallback) {
        if (fallback.result !== undefined) {
          trace.result = fallback.result;
        }
        if (fallback.error) {
          trace.error = fallback.error;
        }
        if (typeof fallback.success === 'boolean') {
          trace.success = fallback.success;
        }
        trace.status = resolveTraceStatus(fallback);
      } else {
        if (toolMessage) {
          trace.result = parsedToolContent?.result ?? (toolMessage.content || '');
          trace.status = 'completed';
          trace.success = true;
        } else {
          trace.status = 'running';
        }
      }

      if (!trace.status) {
        trace.status = resolveTraceStatus(trace);
      }

      traces.push(trace);
    });
  });

  if (!traces.length && fallbackToolTraces.length) {
    return [...fallbackToolTraces];
  }

  return traces;
};

export const buildToolTracesFromTupleMessages = (
  tupleMessages: AssistantTupleMessage[] | undefined,
  fallbackToolTraces: ChatToolTrace[] = [],
): ChatToolTrace[] => {
  if (!tupleMessages || tupleMessages.length === 0) {
    return [...fallbackToolTraces];
  }
  return buildProcessingBlockTraces(tupleMessages, fallbackToolTraces);
};
