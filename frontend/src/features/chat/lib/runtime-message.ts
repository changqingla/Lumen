import type { RAGStreamError } from '@/features/chat/api/rag';
import type {
  ChatActiveRun,
  ChatAttachment,
  ChatArtifact,
  ChatHistoryMessage,
  ChatInterruption,
  ChatTaskModeDecisionEvent,
  ChatTaskSnapshotEvent,
  ChatToolTrace,
} from '@/shared/api/client';
import {
  buildHistoryAssistantTupleMessages,
  buildToolTracesFromTupleMessages,
  type AssistantTupleMessage,
  upsertTupleContentMessage,
  upsertTupleToolResultMessage,
} from './assistant-flow.ts';

export interface RuntimeChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  imageDataUrls?: string[];
  attachments?: ChatAttachment[];
  thinking?: string;
  documentSummaries?: Array<{
    doc_id: string;
    doc_name: string;
    summary: string;
    from_cache: boolean;
  }>;
  artifacts?: ChatArtifact[];
  toolTraces?: ChatToolTrace[];
  assistantTupleMessages?: AssistantTupleMessage[];
  wasTruncated?: boolean;
  truncatedAt?: string;
  interruption?: ChatInterruption | null;
  lastEventAt?: number;
}

export interface RuntimeActiveRunState {
  runId: string;
  sessionId: string;
  status: string;
  startedAt?: string;
  updatedAt?: string;
  assistantMessage: RuntimeChatMessage;
  taskSnapshotEvent?: ChatTaskSnapshotEvent | null;
  taskModeDecisionEvent?: ChatTaskModeDecisionEvent | null;
}

const buildEmptyLiveAssistantMessage = (
  messageId: string,
): RuntimeChatMessage => ({
  id: messageId,
  role: 'assistant',
  content: '',
  imageDataUrls: [],
  attachments: [],
  thinking: '',
  artifacts: [],
  toolTraces: [],
  assistantTupleMessages: [],
  interruption: null,
  lastEventAt: Date.now(),
});

export const ensureDescriptiveStreamError = (error: RAGStreamError): RAGStreamError => {
  const fallbackMessage = '运行中断：服务端未返回可读错误信息，请查看后端日志。';
  if (error instanceof Error) {
    const message = (error.message || '').trim();
    if (message) {
      return error;
    }
    const normalized = new Error(fallbackMessage) as Error & { code?: string; details?: unknown };
    if ('code' in error) {
      normalized.code = String((error as Error & { code?: unknown }).code || '').trim() || undefined;
    }
    if ('details' in error) {
      normalized.details = (error as Error & { details?: unknown }).details;
    }
    return normalized;
  }

  const text = String(error || '').trim();
  return text || fallbackMessage;
};

export const normalizeInterruptionReason = (error: RAGStreamError): string => {
  const raw = (error instanceof Error ? error.message : String(error || '')).trim();
  if (!raw) {
    return '运行环境异常';
  }

  return raw
    .replace(/\s+/gu, ' ')
    .replace(/请调整任务范围或稍后重试。?$/u, '')
    .replace(/请重试。?$/u, '')
    .trim();
};

export const buildInterruptionPayload = (error: RAGStreamError): ChatInterruption => ({
  reason: normalizeInterruptionReason(error),
  interruptedAt: new Date().toISOString(),
  retryable: true,
});

const INTERRUPTED_TOOL_STATUS = 'interrupted';

const buildInterruptedToolResultContent = (reason: string): string => JSON.stringify({
  error: reason,
  success: false,
  status: INTERRUPTED_TOOL_STATUS,
});

export const finalizeDanglingAssistantTools = (
  message: RuntimeChatMessage,
  reason: string,
): void => {
  const normalizedTupleMessages = normalizeAssistantTupleMessages(message.assistantTupleMessages) || [];
  const completedToolCallIds = new Set(
    normalizedTupleMessages
      .filter((item) => item.type === 'tool')
      .map((item) => (item.tool_call_id || '').trim())
      .filter((item) => Boolean(item)),
  );

  let nextTupleMessages = normalizedTupleMessages;
  normalizedTupleMessages.forEach((tupleMessage) => {
    if (tupleMessage.type !== 'ai') {
      return;
    }
    (tupleMessage.tool_calls || []).forEach((toolCall) => {
      const toolCallId = (toolCall.id || '').trim();
      if (!toolCallId || completedToolCallIds.has(toolCallId)) {
        return;
      }
      nextTupleMessages = upsertTupleToolResultMessage(nextTupleMessages, {
        toolCallId,
        toolName: toolCall.name,
        content: buildInterruptedToolResultContent(reason),
        status: INTERRUPTED_TOOL_STATUS,
      });
      completedToolCallIds.add(toolCallId);
    });
  });

  if (nextTupleMessages.length > 0) {
    message.assistantTupleMessages = nextTupleMessages;
    message.toolTraces = buildToolTracesFromTupleMessages(
      nextTupleMessages,
      message.toolTraces || [],
    ).map((trace) => {
      const normalizedStatus = (trace.status || '').trim().toLowerCase();
      if (
        normalizedStatus
        && normalizedStatus !== 'running'
        && normalizedStatus !== INTERRUPTED_TOOL_STATUS
      ) {
        return trace;
      }
      return {
        ...trace,
        status: INTERRUPTED_TOOL_STATUS,
        success: false,
        error: trace.error || reason,
      };
    });
    return;
  }

  message.toolTraces = (message.toolTraces || []).map((trace) => {
    const normalizedStatus = (trace.status || '').trim().toLowerCase();
    if (
      normalizedStatus
      && normalizedStatus !== 'running'
      && normalizedStatus !== INTERRUPTED_TOOL_STATUS
    ) {
      return trace;
    }
    return {
      ...trace,
      status: INTERRUPTED_TOOL_STATUS,
      success: false,
      error: trace.error || reason,
    };
  });
};

export const hasAssistantPayload = (message: RuntimeChatMessage): boolean => (
  Boolean(message.content)
  || Boolean(message.thinking)
  || Boolean((message.artifacts || []).length > 0)
  || Boolean((message.toolTraces || []).length > 0)
  || Boolean((message.assistantTupleMessages || []).length > 0)
  || Boolean(message.interruption?.reason)
);

export const hasClarificationPayload = (message: RuntimeChatMessage): boolean => (
  Boolean((message.toolTraces || []).some((trace) => trace.name === 'ask_clarification'))
  || Boolean((message.assistantTupleMessages || []).some((tupleMessage) => (
    tupleMessage.name === 'ask_clarification'
    || Boolean((tupleMessage.tool_calls || []).some((toolCall) => (
      toolCall.name === 'ask_clarification'
    )))
  )))
);

export const applyFinalAnswerToAssistantMessage = (
  assistantMessage: RuntimeChatMessage,
  answer: string,
  options?: {
    syncTupleContent?: boolean;
  },
): boolean => {
  const normalizedAnswer = answer || '';
  if (!normalizedAnswer) {
    return false;
  }

  let didChange = false;
  if (assistantMessage.content !== normalizedAnswer) {
    assistantMessage.content = normalizedAnswer;
    didChange = true;
  }

  if (options?.syncTupleContent) {
    const nextTupleMessages = upsertTupleContentMessage(
      assistantMessage.assistantTupleMessages,
      normalizedAnswer,
    );
    if (
      stringifyAssistantTupleMessages(nextTupleMessages)
      !== stringifyAssistantTupleMessages(assistantMessage.assistantTupleMessages)
    ) {
      assistantMessage.assistantTupleMessages = nextTupleMessages;
      didChange = true;
    }
  }

  if (didChange) {
    assistantMessage.lastEventAt = Date.now();
  }

  return didChange;
};

const normalizeTupleToolCalls = (value: unknown): AssistantTupleMessage['tool_calls'] => {
  if (!Array.isArray(value)) {
    return undefined;
  }
  const normalized = value
    .map((item) => {
      if (!item || typeof item !== 'object') {
        return null;
      }
      const toolCall = item as Record<string, unknown>;
      const name = typeof toolCall.name === 'string' ? toolCall.name.trim() : '';
      if (!name) {
        return null;
      }
      const result: { id?: string; name: string; args?: unknown } = { name };
      if (typeof toolCall.id === 'string' && toolCall.id.trim()) {
        result.id = toolCall.id.trim();
      }
      if (Object.prototype.hasOwnProperty.call(toolCall, 'args')) {
        result.args = toolCall.args;
      }
      return result;
    })
    .filter((item): item is NonNullable<typeof item> => Boolean(item));
  return normalized.length > 0 ? normalized : undefined;
};

export const normalizeAssistantTupleMessage = (
  raw: unknown,
): AssistantTupleMessage | null => {
  if (!raw || typeof raw !== 'object') {
    return null;
  }

  const value = raw as Record<string, unknown>;
  const type = value.type === 'ai' || value.type === 'tool' ? value.type : null;
  if (!type) {
    return null;
  }

  const id = typeof value.id === 'string' ? value.id.trim() : '';
  if (!id) {
    return null;
  }

  const normalized: AssistantTupleMessage = {
    type,
    id,
  };

  if (typeof value.content === 'string') {
    normalized.content = value.content;
  }

  const normalizedToolCalls = normalizeTupleToolCalls(value.tool_calls);
  if (normalizedToolCalls) {
    normalized.tool_calls = normalizedToolCalls;
  }

  if (typeof value.tool_call_id === 'string' && value.tool_call_id.trim()) {
    normalized.tool_call_id = value.tool_call_id.trim();
  }
  if (typeof value.name === 'string' && value.name.trim()) {
    normalized.name = value.name.trim();
  }
  if (typeof value.status === 'string' && value.status.trim()) {
    normalized.status = value.status.trim();
  }

  if (type === 'ai') {
    // Whitespace-only chunks carry markdown structure and must not be discarded.
    const hasContent = typeof normalized.content === 'string' && normalized.content.length > 0;
    const hasToolCalls = Boolean(normalized.tool_calls && normalized.tool_calls.length > 0);
    if (!hasContent && !hasToolCalls) {
      return null;
    }
    return normalized;
  }

  if (!normalized.tool_call_id || !normalized.name) {
    return null;
  }
  return normalized;
};

export const normalizeAssistantTupleMessages = (
  rawMessages: unknown,
): AssistantTupleMessage[] | undefined => {
  if (!Array.isArray(rawMessages)) {
    return undefined;
  }
  const normalized = rawMessages
    .map((item) => normalizeAssistantTupleMessage(item))
    .filter((item): item is AssistantTupleMessage => Boolean(item));
  return normalized.length > 0 ? normalized : undefined;
};

const stringifyAssistantTupleMessages = (
  tupleMessages: AssistantTupleMessage[] | undefined,
): string => {
  if (!tupleMessages || tupleMessages.length === 0) {
    return '';
  }

  return tupleMessages.map((message) => {
    const toolCalls = (message.tool_calls || []).map((toolCall) => ({
      id: toolCall.id || '',
      name: toolCall.name,
      args: toolCall.args,
    }));
    return JSON.stringify({
      type: message.type,
      id: message.id,
      content: message.content || '',
      tool_calls: toolCalls,
      tool_call_id: message.tool_call_id || '',
      name: message.name || '',
      status: message.status || '',
    });
  }).join('|');
};

const stringifyAssistantTupleMessagesForEquivalence = (
  tupleMessages: AssistantTupleMessage[] | undefined,
): string => {
  if (!tupleMessages || tupleMessages.length === 0) {
    return '';
  }

  return tupleMessages.map((message) => {
    const toolCalls = (message.tool_calls || []).map((toolCall, index) => ({
      id: toolCall.id || `${toolCall.name}:${index}`,
      name: toolCall.name,
      args: toolCall.args,
    }));
    return JSON.stringify({
      type: message.type,
      content: message.content || '',
      tool_calls: toolCalls,
      tool_call_id: message.tool_call_id || '',
      name: message.name || '',
      status: message.status || '',
    });
  }).join('|');
};

export const stringifyTupleToolResult = (trace: ChatToolTrace): string => {
  const payload: Record<string, unknown> = {};
  if (Object.prototype.hasOwnProperty.call(trace, 'result')) {
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
  return JSON.stringify(payload);
};

const stringifyImageDataUrls = (imageDataUrls: string[] | undefined): string => (
  (imageDataUrls || []).join('|')
);

const stringifyArtifacts = (artifacts: ChatArtifact[] | undefined): string => (
  (artifacts || [])
    .map((artifact) => artifact.object_path)
    .sort()
    .join('|')
);

const stringifyAttachments = (attachments: ChatAttachment[] | undefined): string => (
  (attachments || [])
    .map((attachment) => (
      `${attachment.attachment_id}|${attachment.workspace_path}|${attachment.object_path}|`
      + `${attachment.parse_status || ''}|${JSON.stringify(attachment.metadata || {})}`
    ))
    .sort()
    .join('|')
);

const hasPendingAttachmentState = (attachments: ChatAttachment[] | undefined): boolean => (
  (attachments || []).some((attachment) => {
    if (attachment.parse_status === 'pending') {
      return true;
    }
    const metadata = attachment.metadata;
    if (!metadata || typeof metadata !== 'object' || Array.isArray(metadata)) {
      return false;
    }
    const kbProjection = (metadata as Record<string, unknown>).kb_projection;
    if (!kbProjection || typeof kbProjection !== 'object' || Array.isArray(kbProjection)) {
      return false;
    }
    const status = String((kbProjection as Record<string, unknown>).status || '').trim().toLowerCase();
    return status === 'pending';
  })
);

export const hasPendingAttachmentsInMessages = (
  messages: RuntimeChatMessage[],
): boolean => messages.some((message) => hasPendingAttachmentState(message.attachments));

const stringifyToolTraces = (toolTraces: ChatToolTrace[] | undefined): string => (
  (toolTraces || [])
    .map((trace) => (
      `${trace.call_id || ''}|${trace.iteration || ''}|${trace.name}|`
      + `${trace.status || ''}|${typeof trace.success === 'boolean' ? String(trace.success) : ''}|`
      + `${trace.error || ''}|${typeof trace.duration_ms === 'number' ? trace.duration_ms : ''}`
    ))
    .sort()
    .join('|')
);

const stringifyInterruption = (
  interruption: ChatInterruption | null | undefined,
): string => (
  interruption
    ? `${interruption.reason}|${interruption.interruptedAt || ''}|${String(interruption.retryable ?? true)}`
    : ''
);

const areRuntimeMessagesEquivalent = (
  left: RuntimeChatMessage,
  right: RuntimeChatMessage,
): boolean => {
  if (left.role !== right.role || left.content !== right.content) {
    return false;
  }
  if ((left.thinking || '') !== (right.thinking || '')) {
    return false;
  }
  if (stringifyImageDataUrls(left.imageDataUrls) !== stringifyImageDataUrls(right.imageDataUrls)) {
    return false;
  }
  if (stringifyAttachments(left.attachments) !== stringifyAttachments(right.attachments)) {
    return false;
  }
  if (stringifyArtifacts(left.artifacts) !== stringifyArtifacts(right.artifacts)) {
    return false;
  }
  if (stringifyToolTraces(left.toolTraces) !== stringifyToolTraces(right.toolTraces)) {
    return false;
  }
  if (
    stringifyAssistantTupleMessagesForEquivalence(left.assistantTupleMessages)
    !== stringifyAssistantTupleMessagesForEquivalence(right.assistantTupleMessages)
  ) {
    return false;
  }
  return stringifyInterruption(left.interruption) === stringifyInterruption(right.interruption);
};

const getMessageRichnessScore = (message: RuntimeChatMessage): number => {
  const tupleText = stringifyAssistantTupleMessages(message.assistantTupleMessages);
  const toolTraceText = stringifyToolTraces(message.toolTraces);
  return (
    (message.content || '').length * 10
    + (message.thinking || '').length * 2
    + tupleText.length * 4
    + toolTraceText.length * 2
    + (message.artifacts || []).length * 100
    + (message.attachments || []).length * 40
    + (message.documentSummaries || []).length * 40
    + (message.interruption ? 30 : 0)
    + (message.wasTruncated ? 10 : 0)
  );
};

const preferRicherMessage = (
  loadedMessage: RuntimeChatMessage,
  liveMessage: RuntimeChatMessage,
): RuntimeChatMessage => {
  const loadedScore = getMessageRichnessScore(loadedMessage);
  const liveScore = getMessageRichnessScore(liveMessage);
  if (liveScore !== loadedScore) {
    return liveScore > loadedScore ? liveMessage : loadedMessage;
  }
  return (liveMessage.lastEventAt || 0) >= (loadedMessage.lastEventAt || 0)
    ? liveMessage
    : loadedMessage;
};

export const mergeLoadedAndLiveMessages = (
  loadedMessages: RuntimeChatMessage[],
  liveMessages: RuntimeChatMessage[],
): RuntimeChatMessage[] => {
  const nextMessages = [...loadedMessages];
  liveMessages.forEach((message) => {
    const sameIdIndex = nextMessages.findIndex((candidate) => candidate.id === message.id);
    if (sameIdIndex !== -1) {
      nextMessages[sameIdIndex] = preferRicherMessage(nextMessages[sameIdIndex], message);
      return;
    }
    if (!nextMessages.some((candidate) => areRuntimeMessagesEquivalent(candidate, message))) {
      nextMessages.push(message);
    }
  });
  return nextMessages;
};

export const upsertMessageById = (
  messages: RuntimeChatMessage[],
  message: RuntimeChatMessage,
): RuntimeChatMessage[] => {
  const index = messages.findIndex((item) => item.id === message.id);
  if (index === -1) {
    return [...messages, message];
  }
  const next = [...messages];
  next[index] = message;
  return next;
};

export const buildResumeStreamState = (assistantMessage: RuntimeChatMessage): {
  aiContentByMessageId?: Record<string, string>;
  aiThinkingByMessageId?: Record<string, string>;
  emittedToolCallKeys?: string[];
  toolResultContentByCallId?: Record<string, string>;
} => {
  const aiContentByMessageId: Record<string, string> = {};
  const aiThinkingByMessageId: Record<string, string> = {};
  const toolResultContentByCallId: Record<string, string> = {};
  const emittedToolCallKeys = new Set<string>();

  if (assistantMessage.id && assistantMessage.content) {
    aiContentByMessageId[assistantMessage.id] = assistantMessage.content;
  }
  if (assistantMessage.id && assistantMessage.thinking) {
    aiThinkingByMessageId[assistantMessage.id] = assistantMessage.thinking;
  }

  (assistantMessage.assistantTupleMessages || []).forEach((tupleMessage) => {
    if (tupleMessage.type === 'ai') {
      (tupleMessage.tool_calls || []).forEach((toolCall) => {
        const toolCallId = (toolCall.id || '').trim();
        if (toolCallId) {
          emittedToolCallKeys.add(toolCallId);
        }
      });
      return;
    }

    const toolCallId = (tupleMessage.tool_call_id || '').trim();
    if (toolCallId) {
      toolResultContentByCallId[toolCallId] = tupleMessage.content || '';
    }
  });

  return {
    aiContentByMessageId,
    aiThinkingByMessageId,
    emittedToolCallKeys: [...emittedToolCallKeys],
    toolResultContentByCallId,
  };
};

export const buildRuntimeMessageFromHistory = (
  msg: ChatHistoryMessage,
): RuntimeChatMessage => {
  const role = msg.role as 'user' | 'assistant';
  const normalizedTupleMessages = normalizeAssistantTupleMessages(msg.assistantTupleMessages);
  const normalizedToolTraces = normalizedTupleMessages && normalizedTupleMessages.length > 0
    ? buildToolTracesFromTupleMessages(normalizedTupleMessages, msg.toolTraces || [])
    : (msg.toolTraces || []);
  const resolvedTupleMessages = normalizedTupleMessages
    || buildHistoryAssistantTupleMessages(role, msg.content, normalizedToolTraces);

  const message: RuntimeChatMessage = {
    id: msg.id,
    role,
    content: msg.content,
    imageDataUrls: msg.imageDataUrls || [],
    attachments: msg.attachments || [],
    thinking: msg.thinking || '',
    documentSummaries: msg.documentSummaries,
    artifacts: msg.artifacts || [],
    toolTraces: normalizedToolTraces,
    assistantTupleMessages: resolvedTupleMessages,
    wasTruncated: msg.wasTruncated,
    truncatedAt: msg.truncatedAt,
    interruption: msg.interruption || null,
  };

  if (message.role === 'assistant') {
    if (message.wasTruncated) {
      finalizeDanglingAssistantTools(message, '生成已被用户停止，工具调用未完成。');
    } else if (message.interruption?.reason) {
      finalizeDanglingAssistantTools(message, `运行中断：${message.interruption.reason}`);
    }
  }

  return message;
};

export const buildRecoveredActiveRunAssistantMessage = (
  loadedMessages: RuntimeChatMessage[],
  runId: string,
): RuntimeChatMessage => {
  const lastLoadedMessage = loadedMessages[loadedMessages.length - 1];
  if (lastLoadedMessage?.role === 'assistant') {
    return {
      ...lastLoadedMessage,
      lastEventAt: Date.now(),
    };
  }

  return buildEmptyLiveAssistantMessage(`live-run:${runId}`);
};

export const normalizeActiveRunState = (
  raw: ChatActiveRun | null | undefined,
): RuntimeActiveRunState | null => {
  if (!raw || !raw.runId || !raw.sessionId || !raw.assistantMessage) {
    return null;
  }

  const assistantMessage = buildRuntimeMessageFromHistory(raw.assistantMessage);
  if (assistantMessage.role !== 'assistant') {
    return null;
  }

  return {
    runId: raw.runId,
    sessionId: raw.sessionId,
    status: raw.status,
    startedAt: raw.startedAt,
    updatedAt: raw.updatedAt,
    assistantMessage,
    taskSnapshotEvent: raw.taskSnapshotEvent || null,
    taskModeDecisionEvent: raw.taskModeDecisionEvent || null,
  };
};

export const isLiveActiveRun = (
  activeRun: RuntimeActiveRunState | null | undefined,
): activeRun is RuntimeActiveRunState => Boolean(
  activeRun && (activeRun.status === 'running' || activeRun.status === 'pending'),
);
