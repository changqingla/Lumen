import { useState, useCallback, useRef, useEffect, useSyncExternalStore } from 'react';
import type { ChatUIMode } from '@/shared/contracts/chat-ui-mode';
import { ragAPI, SESSION_ACTIVE_RUN_CONFLICT_CODE } from '@/features/chat/api/rag';
import { subscribeAuthSessionReset } from '@/shared/lib/auth-runtime';
import type {
  RAGStreamArtifact,
  RAGStreamError,
  RAGStreamToolTrace,
} from '@/features/chat/api/rag';
import {
  api,
  CHAT_RUNTIME_NAME,
  type ChatActiveRun,
  type ChatAttachment,
  type ChatArtifact,
  type ChatHistoryMessage,
  type ChatInterruption,
  type ChatTaskModeDecisionEvent,
  type ChatTaskSnapshotEvent,
  type ChatToolTrace,
} from '@/shared/api/client';
import {
  appendTupleContentMessage,
  buildHistoryAssistantTupleMessages,
  buildToolTracesFromTupleMessages,
  getToolTraceStableKey,
  type AssistantTupleMessage,
  upsertTupleContentMessage,
  upsertTupleToolCallMessage,
  upsertTupleToolResultMessage,
} from '@/features/chat/lib/assistant-flow';
import {
  applyTaskDeltaEvent,
  applyTaskModeDecisionEvent,
  applyTaskSnapshotEvent,
  buildTaskModeDecisionStateFromHistory,
  buildTaskSnapshotStateFromHistory,
} from '@/features/chat/lib/task-runtime';
import type {
  TaskModeDecisionState,
  TaskSnapshotState,
} from '@/features/chat/lib/task-runtime';
import { generateUUID } from '@/shared/lib/uuid';

interface Message {
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

interface UseRAGChatOptions {
  sessionId?: string; // 已存在的会话ID
  kbId?: string;
  docIds?: string[];
  modelName?: string;
  uiMode: ChatUIMode;
  sourceType?: 'home' | 'knowledge' | 'favorites'; // 会话来源类型
  onError?: (error: RAGStreamError) => void;
  onSessionCreated?: (sessionId: string) => void; // 新会话创建时的回调
  onFirstContentToken?: (messageId: string) => void; // 首个回答 token 到达时的回调
  onStopComplete?: () => void; // 用户停止生成完成时的回调
}

interface ClearMessagesOptions {
  preserveSessionRuntime?: boolean;
}

const MAX_SESSION_RUNTIMES = 20;
const RUNTIME_EXPIRY_MS = 30 * 60 * 1000; // 30分钟过期

type StreamTerminationReason = 'none' | 'user_stop' | 'discard';

interface SessionRuntimeState {
  messages: Message[];
  taskSnapshot: TaskSnapshotState | null;
  taskModeDecision: TaskModeDecisionState | null;
  activeRun: ActiveRunState | null;
  isHydratingSessionState: boolean;
  isStreaming: boolean;
  isStopping: boolean;
  isLoading: boolean;
}

interface SessionRuntimeCallbacks {
  onError?: (error: RAGStreamError) => void;
  onSessionCreated?: (sessionId: string) => void;
  onFirstContentToken?: (messageId: string) => void;
  onStopComplete?: () => void;
}

interface SessionRuntime {
  sessionId: string;
  state: SessionRuntimeState;
  listeners: Set<() => void>;
  callbacks: SessionRuntimeCallbacks;
  currentMessage: Message | null;
  abortController: AbortController | null;
  isSending: boolean;
  streamTerminationReason: StreamTerminationReason;
  hydrationSequence: number;
  hydrationStartedAt: number;
  hydrationTimer: ReturnType<typeof setTimeout> | null;
  hasLoadedHistory: boolean;
  attachedActiveRunId: string | null;
  transportRecoverySequence: number;
  lastAccessedAt: number;
}

interface ActiveRunState {
  runId: string;
  sessionId: string;
  status: string;
  startedAt?: string;
  updatedAt?: string;
  assistantMessage: Message;
  taskSnapshotEvent?: ChatTaskSnapshotEvent | null;
  taskModeDecisionEvent?: ChatTaskModeDecisionEvent | null;
}

const buildEmptyLiveAssistantMessage = (messageId: string): Message => ({
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

const createInitialSessionRuntimeState = (): SessionRuntimeState => ({
  messages: [],
  taskSnapshot: null,
  taskModeDecision: null,
  activeRun: null,
  isHydratingSessionState: false,
  isStreaming: false,
  isStopping: false,
  isLoading: false,
});

const EMPTY_SESSION_RUNTIME_STATE = createInitialSessionRuntimeState();

const sessionRuntimes = new Map<string, SessionRuntime>();
let hasAttachedAuthResetListener = false;

const createSessionRuntime = (sessionId: string): SessionRuntime => ({
  sessionId,
  state: createInitialSessionRuntimeState(),
  listeners: new Set(),
  callbacks: {},
  currentMessage: null,
  abortController: null,
  isSending: false,
  streamTerminationReason: 'none',
  hydrationSequence: 0,
  hydrationStartedAt: 0,
  hydrationTimer: null,
  hasLoadedHistory: false,
  attachedActiveRunId: null,
  transportRecoverySequence: 0,
  lastAccessedAt: Date.now(),
});

const cleanupSessionRuntimeTimers = (runtime: SessionRuntime) => {
  if (runtime.hydrationTimer) {
    clearTimeout(runtime.hydrationTimer);
    runtime.hydrationTimer = null;
  }
};

const clearAllSessionRuntimes = (): void => {
  sessionRuntimes.forEach((runtime) => {
    cleanupSessionRuntimeTimers(runtime);
    runtime.abortController?.abort();
    runtime.abortController = null;
    runtime.listeners.clear();
    runtime.callbacks = {};
    runtime.currentMessage = null;
    runtime.isSending = false;
    runtime.streamTerminationReason = 'none';
  });
  sessionRuntimes.clear();
};

const attachAuthSessionResetListener = () => {
  if (hasAttachedAuthResetListener) {
    return;
  }
  hasAttachedAuthResetListener = true;
  subscribeAuthSessionReset(() => {
    clearAllSessionRuntimes();
  });
};

const cleanupExpiredSessionRuntimes = () => {
  const now = Date.now();
  const removableEntries = Array.from(sessionRuntimes.entries()).filter(([, runtime]) => (
    runtime.listeners.size === 0
    && !runtime.state.isStreaming
    && !runtime.isSending
    && now - runtime.lastAccessedAt > RUNTIME_EXPIRY_MS
  ));

  removableEntries.forEach(([sessionId, runtime]) => {
    cleanupSessionRuntimeTimers(runtime);
    sessionRuntimes.delete(sessionId);
  });

  if (sessionRuntimes.size <= MAX_SESSION_RUNTIMES) {
    return;
  }

  const overflowCount = sessionRuntimes.size - MAX_SESSION_RUNTIMES;
  const overflowEntries = Array.from(sessionRuntimes.entries())
    .filter(([, runtime]) => runtime.listeners.size === 0 && !runtime.state.isStreaming && !runtime.isSending)
    .sort((left, right) => left[1].lastAccessedAt - right[1].lastAccessedAt)
    .slice(0, overflowCount);

  overflowEntries.forEach(([sessionId, runtime]) => {
    cleanupSessionRuntimeTimers(runtime);
    sessionRuntimes.delete(sessionId);
  });
};

attachAuthSessionResetListener();

const getSessionRuntime = (sessionId: string): SessionRuntime => {
  cleanupExpiredSessionRuntimes();
  const existing = sessionRuntimes.get(sessionId);
  if (existing) {
    existing.lastAccessedAt = Date.now();
    return existing;
  }

  const created = createSessionRuntime(sessionId);
  sessionRuntimes.set(sessionId, created);
  return created;
};

export const initializeEmptySessionRuntime = (sessionId: string): void => {
  const runtime = getSessionRuntime(sessionId);
  cleanupSessionRuntimeTimers(runtime);
  runtime.hydrationSequence += 1;
  runtime.hydrationStartedAt = 0;
  runtime.hasLoadedHistory = true;
  runtime.currentMessage = null;
  runtime.attachedActiveRunId = null;
  runtime.transportRecoverySequence = 0;
  runtime.abortController = null;
  runtime.isSending = false;
  runtime.streamTerminationReason = 'none';
  updateSessionRuntimeState(runtime, createInitialSessionRuntimeState());
};

const getExistingSessionRuntime = (sessionId: string | null | undefined): SessionRuntime | null => {
  if (!sessionId) {
    return null;
  }
  return sessionRuntimes.get(sessionId) || null;
};

const notifySessionRuntime = (runtime: SessionRuntime) => {
  runtime.lastAccessedAt = Date.now();
  runtime.listeners.forEach((listener) => listener());
  cleanupExpiredSessionRuntimes();
};

const updateSessionRuntimeState = (
  runtime: SessionRuntime,
  updater: SessionRuntimeState | ((current: SessionRuntimeState) => SessionRuntimeState),
) => {
  runtime.state = typeof updater === 'function'
    ? updater(runtime.state)
    : updater;
  notifySessionRuntime(runtime);
};

const setSessionRuntimeCallbacks = (
  runtime: SessionRuntime,
  callbacks: SessionRuntimeCallbacks,
) => {
  runtime.callbacks = callbacks;
  runtime.lastAccessedAt = Date.now();
};

const subscribeSessionRuntime = (
  runtime: SessionRuntime,
  listener: () => void,
) => {
  runtime.listeners.add(listener);
  runtime.lastAccessedAt = Date.now();
  return () => {
    runtime.listeners.delete(listener);
    runtime.lastAccessedAt = Date.now();
    if (runtime.listeners.size === 0) {
      runtime.callbacks = {};
    }
    cleanupExpiredSessionRuntimes();
  };
};

const beginRuntimeHydration = (runtime: SessionRuntime): number => {
  runtime.hydrationSequence += 1;
  runtime.hydrationStartedAt = Date.now();
  cleanupSessionRuntimeTimers(runtime);
  updateSessionRuntimeState(runtime, (current) => ({
    ...current,
    isHydratingSessionState: true,
  }));
  return runtime.hydrationSequence;
};

const finishRuntimeHydration = (runtime: SessionRuntime, sequence: number) => {
  if (sequence !== runtime.hydrationSequence) {
    return;
  }
  const minVisibleMs = 180;
  const elapsed = Math.max(Date.now() - runtime.hydrationStartedAt, 0);
  const remaining = Math.max(minVisibleMs - elapsed, 0);
  if (remaining === 0) {
    updateSessionRuntimeState(runtime, (current) => ({
      ...current,
      isHydratingSessionState: false,
    }));
    return;
  }
  runtime.hydrationTimer = setTimeout(() => {
    if (sequence !== runtime.hydrationSequence) {
      return;
    }
    runtime.hydrationTimer = null;
    updateSessionRuntimeState(runtime, (current) => ({
      ...current,
      isHydratingSessionState: false,
    }));
  }, remaining);
};

const emitRuntimeError = (runtime: SessionRuntime, error: RAGStreamError) => {
  if (runtime.listeners.size === 0) {
    return;
  }
  runtime.callbacks.onError?.(error);
};

const isQuotaExceededStreamError = (error: RAGStreamError): boolean => (
  error instanceof Error
  && 'code' in error
  && (error as Error & { code?: string }).code === 'QUOTA_EXCEEDED'
);

const isAuthExpiredStreamError = (error: RAGStreamError): boolean => {
  const raw = error instanceof Error ? error.message : String(error || '');
  return raw.includes('当前登录已过期');
};

const isRecoverableTransportStreamError = (error: RAGStreamError): boolean => {
  if (!(error instanceof Error)) {
    return false;
  }
  if (isQuotaExceededStreamError(error) || isAuthExpiredStreamError(error)) {
    return false;
  }

  const structuredCode = 'code' in error
    ? String((error as Error & { code?: string }).code || '').trim()
    : '';
  if (structuredCode) {
    return false;
  }

  const message = (error.message || '').trim().toLowerCase();
  if (!message) {
    return false;
  }

  return (
    message.startsWith('http ')
    || message.includes('failed to fetch')
    || message.includes('networkerror')
    || message.includes('network error')
    || message.includes('load failed')
    || message.includes('the network connection was lost')
    || message.includes('fetch failed')
  );
};

const isSessionActiveRunConflictError = (error: unknown): boolean => (
  error instanceof Error
  && 'code' in error
  && (error as Error & { code?: string }).code === SESSION_ACTIVE_RUN_CONFLICT_CODE
);

const ensureDescriptiveStreamError = (error: RAGStreamError): RAGStreamError => {
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

const normalizeInterruptionReason = (error: RAGStreamError): string => {
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

const buildInterruptionPayload = (error: RAGStreamError): ChatInterruption => ({
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

const finalizeDanglingAssistantTools = (
  message: Message,
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
    message.toolTraces = buildToolTracesFromTupleMessages(nextTupleMessages, message.toolTraces || []).map((trace) => {
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

const hasAssistantPayload = (message: Message): boolean => (
  Boolean(message.content)
  || Boolean(message.thinking)
  || Boolean((message.artifacts || []).length > 0)
  || Boolean((message.toolTraces || []).length > 0)
  || Boolean((message.assistantTupleMessages || []).length > 0)
  || Boolean(message.interruption?.reason)
);

const hasClarificationPayload = (message: Message): boolean => (
  Boolean((message.toolTraces || []).some((trace) => trace.name === 'ask_clarification'))
  || Boolean((message.assistantTupleMessages || []).some((tupleMessage) => (
    tupleMessage.name === 'ask_clarification'
    || Boolean((tupleMessage.tool_calls || []).some((toolCall) => toolCall.name === 'ask_clarification'))
  )))
);

const applyFinalAnswerToAssistantMessage = (
  assistantMessage: Message,
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
    if (stringifyAssistantTupleMessages(nextTupleMessages) !== stringifyAssistantTupleMessages(assistantMessage.assistantTupleMessages)) {
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

const normalizeAssistantTupleMessage = (
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
    // Keep whitespace-only chunks (e.g. "\n\n"), as they carry markdown structure.
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

const normalizeAssistantTupleMessages = (
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

const getMessageRichnessScore = (message: Message): number => {
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
  loadedMessage: Message,
  liveMessage: Message,
): Message => {
  const loadedScore = getMessageRichnessScore(loadedMessage);
  const liveScore = getMessageRichnessScore(liveMessage);
  if (liveScore !== loadedScore) {
    return liveScore > loadedScore ? liveMessage : loadedMessage;
  }
  return (liveMessage.lastEventAt || 0) >= (loadedMessage.lastEventAt || 0)
    ? liveMessage
    : loadedMessage;
};

const buildResumeStreamState = (assistantMessage: Message): {
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
    if (!toolCallId) {
      return;
    }
    toolResultContentByCallId[toolCallId] = tupleMessage.content || '';
  });

  return {
    aiContentByMessageId,
    aiThinkingByMessageId,
    emittedToolCallKeys: [...emittedToolCallKeys],
    toolResultContentByCallId,
  };
};

const stringifyTupleToolResult = (trace: ChatToolTrace): string => {
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

const hasPendingAttachmentsInMessages = (messages: Message[]): boolean => (
  messages.some((message) => hasPendingAttachmentState(message.attachments))
);

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

const stringifyInterruption = (interruption: ChatInterruption | null | undefined): string => (
  interruption
    ? `${interruption.reason}|${interruption.interruptedAt || ''}|${String(interruption.retryable ?? true)}`
    : ''
);

const areMessagesEquivalent = (left: Message, right: Message): boolean => {
  if (left.role !== right.role) {
    return false;
  }
  if (left.content !== right.content) {
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
  if (stringifyInterruption(left.interruption) !== stringifyInterruption(right.interruption)) {
    return false;
  }
  return true;
};

const mergeLoadedAndLiveMessages = (
  loadedMessages: Message[],
  liveMessages: Message[],
): Message[] => {
  const nextMessages = [...loadedMessages];
  liveMessages.forEach((message) => {
    const sameIdIndex = nextMessages.findIndex((candidate) => candidate.id === message.id);
    if (sameIdIndex !== -1) {
      nextMessages[sameIdIndex] = preferRicherMessage(nextMessages[sameIdIndex], message);
      return;
    }
    if (!nextMessages.some((candidate) => areMessagesEquivalent(candidate, message))) {
      nextMessages.push(message);
    }
  });
  return nextMessages;
};

const upsertMessageById = (
  messages: Message[],
  message: Message,
): Message[] => {
  const index = messages.findIndex((item) => item.id === message.id);
  if (index === -1) {
    return [...messages, message];
  }
  const next = [...messages];
  next[index] = message;
  return next;
};

const buildRuntimeMessageFromHistory = (
  msg: ChatHistoryMessage,
): Message => {
  const role = msg.role as 'user' | 'assistant';
  const normalizedTupleMessages = normalizeAssistantTupleMessages(msg.assistantTupleMessages);
  const normalizedToolTraces = normalizedTupleMessages && normalizedTupleMessages.length > 0
    ? buildToolTracesFromTupleMessages(normalizedTupleMessages, msg.toolTraces || [])
    : (msg.toolTraces || []);
  const resolvedTupleMessages = normalizedTupleMessages
    || buildHistoryAssistantTupleMessages(
      role,
      msg.content,
      normalizedToolTraces,
    );

  const message: Message = {
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

const buildRecoveredActiveRunAssistantMessage = (
  loadedMessages: Message[],
  runId: string,
): Message => {
  const lastLoadedMessage = loadedMessages[loadedMessages.length - 1];
  if (lastLoadedMessage?.role === 'assistant') {
    return {
      ...lastLoadedMessage,
      lastEventAt: Date.now(),
    };
  }

  return buildEmptyLiveAssistantMessage(`live-run:${runId}`);
};

const normalizeActiveRunState = (
  raw: ChatActiveRun | null | undefined,
): ActiveRunState | null => {
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

const isLiveActiveRun = (activeRun: ActiveRunState | null | undefined): activeRun is ActiveRunState => (
  Boolean(activeRun && (activeRun.status === 'running' || activeRun.status === 'pending'))
);

const clearRuntimeDisplayState = (runtime: SessionRuntime) => {
  runtime.hasLoadedHistory = false;
  runtime.hydrationSequence += 1;
  runtime.hydrationStartedAt = 0;
  cleanupSessionRuntimeTimers(runtime);
  updateSessionRuntimeState(runtime, createInitialSessionRuntimeState());
};

export function useRAGChat(options: UseRAGChatOptions) {
  const {
    sessionId: externalSessionId,
    kbId,
    docIds,
    modelName,
    uiMode,
    sourceType = 'home',
    onError,
    onSessionCreated,
    onFirstContentToken,
    onStopComplete,
  } = options;
  const [optimisticSessionId, setOptimisticSessionId] = useState<string | null>(null);
  const isMountedRef = useRef(false);
  const sessionlessSendLockRef = useRef(false);
  const currentSessionId = externalSessionId || optimisticSessionId || null;
  const currentSessionIdRef = useRef<string | null>(currentSessionId);

  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    currentSessionIdRef.current = currentSessionId;
  }, [currentSessionId]);

  useEffect(() => {
    if (externalSessionId) {
      setOptimisticSessionId((current) => (current ? null : current));
    }
  }, [externalSessionId]);

  const subscribe = useCallback((listener: () => void) => {
    if (!currentSessionId) {
      return () => {};
    }
    const runtime = getSessionRuntime(currentSessionId);
    return subscribeSessionRuntime(runtime, listener);
  }, [currentSessionId]);

  const getSnapshot = useCallback((): SessionRuntimeState => {
    if (!currentSessionId) {
      return EMPTY_SESSION_RUNTIME_STATE;
    }
    return getSessionRuntime(currentSessionId).state;
  }, [currentSessionId]);

  const runtimeState = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);

  useEffect(() => {
    if (!currentSessionId) {
      return;
    }
    const runtime = getSessionRuntime(currentSessionId);
    setSessionRuntimeCallbacks(runtime, {
      onError,
      onSessionCreated,
      onFirstContentToken,
      onStopComplete,
    });
  }, [currentSessionId, onError, onSessionCreated, onFirstContentToken, onStopComplete]);

  const reportError = useCallback((runtime: SessionRuntime | null, error: RAGStreamError) => {
    if (runtime && runtime.listeners.size > 0) {
      emitRuntimeError(runtime, error);
      return;
    }
    if (isMountedRef.current) {
      onError?.(error);
    }
  }, [onError]);

  const notifyFirstContentToken = useCallback((runtime: SessionRuntime, messageId: string) => {
    if (runtime.listeners.size > 0) {
      runtime.callbacks.onFirstContentToken?.(messageId);
      return;
    }
    if (isMountedRef.current) {
      onFirstContentToken?.(messageId);
    }
  }, [onFirstContentToken]);

  const notifyStopComplete = useCallback((runtime: SessionRuntime) => {
    if (runtime.listeners.size > 0) {
      runtime.callbacks.onStopComplete?.();
      return;
    }
    if (isMountedRef.current) {
      onStopComplete?.();
    }
  }, [onStopComplete]);

  const normalizeArtifact = useCallback((artifact: RAGStreamArtifact): ChatArtifact | null => {
    const objectPath = (artifact.object_path || '').trim();
    if (!objectPath) {
      return null;
    }

    const normalized: ChatArtifact = {
      object_path: objectPath,
    };

    if (artifact.name && artifact.name.trim()) {
      normalized.name = artifact.name.trim();
    }
    if (artifact.path && artifact.path.trim()) {
      normalized.path = artifact.path.trim();
    }
    if (typeof artifact.size_bytes === 'number' && artifact.size_bytes >= 0) {
      normalized.size_bytes = artifact.size_bytes;
    }
    if (artifact.mime_type && artifact.mime_type.trim()) {
      normalized.mime_type = artifact.mime_type.trim();
    }
    if (artifact.session_id && artifact.session_id.trim()) {
      normalized.session_id = artifact.session_id.trim();
    }

    return normalized;
  }, []);

  const normalizeToolTrace = useCallback((trace: RAGStreamToolTrace): ChatToolTrace | null => {
    const name = (trace.name || '').trim();
    if (!name) {
      return null;
    }
    const normalized: ChatToolTrace = { name };
    if (trace.call_id && trace.call_id.trim()) {
      normalized.call_id = trace.call_id.trim();
    }
    if (typeof trace.iteration === 'number' && trace.iteration > 0) {
      normalized.iteration = trace.iteration;
    }
    if (typeof trace.success === 'boolean') {
      normalized.success = trace.success;
    }
    if (trace.error && trace.error.trim()) {
      normalized.error = trace.error.trim();
    }
    if (trace.status && trace.status.trim()) {
      normalized.status = trace.status.trim();
    }
    if (typeof trace.duration_ms === 'number' && trace.duration_ms >= 0) {
      normalized.duration_ms = trace.duration_ms;
    }
    if (Object.prototype.hasOwnProperty.call(trace, 'args')) {
      normalized.args = trace.args;
    }
    if (Object.prototype.hasOwnProperty.call(trace, 'result')) {
      normalized.result = trace.result;
    }
    return normalized;
  }, []);

  const mergeToolTrace = useCallback((prev: ChatToolTrace, next: ChatToolTrace): ChatToolTrace => {
    const merged: ChatToolTrace = {
      ...prev,
      ...next,
    };
    if (next.args === undefined && prev.args !== undefined) {
      merged.args = prev.args;
    }
    if (next.result === undefined && prev.result !== undefined) {
      merged.result = prev.result;
    }
    if (next.error === undefined && prev.error !== undefined) {
      merged.error = prev.error;
    }
    if (next.success === undefined && prev.success !== undefined) {
      merged.success = prev.success;
    }
    if (!next.status && prev.status) {
      merged.status = prev.status;
    }
    if (next.duration_ms === undefined && prev.duration_ms !== undefined) {
      merged.duration_ms = prev.duration_ms;
    }
    return merged;
  }, []);

  const upsertToolTrace = useCallback((existing: ChatToolTrace[], incoming: ChatToolTrace): ChatToolTrace[] => {
    const incomingKey = getToolTraceStableKey(incoming);
    const existingIndex = existing.findIndex((item) => getToolTraceStableKey(item) === incomingKey);
    if (existingIndex === -1) {
      return [...existing, incoming];
    }
    const merged = mergeToolTrace(existing[existingIndex], incoming);
    const next = [...existing];
    next[existingIndex] = merged;
    return next;
  }, [mergeToolTrace]);

  const appendMessageToRuntime = useCallback((runtime: SessionRuntime, message: Message) => {
    updateSessionRuntimeState(runtime, (current) => ({
      ...current,
      messages: [...current.messages, { ...message }],
    }));
  }, []);

  const replaceMessageInRuntime = useCallback((runtime: SessionRuntime, message: Message) => {
    updateSessionRuntimeState(runtime, (current) => {
      const index = current.messages.findIndex((item) => item.id === message.id);
      if (index === -1) {
        return current;
      }
      const nextMessages = [...current.messages];
      nextMessages[index] = { ...message };
      return {
        ...current,
        messages: nextMessages,
      };
    });
  }, []);

  const removeMessageFromRuntime = useCallback((runtime: SessionRuntime, messageId: string) => {
    updateSessionRuntimeState(runtime, (current) => {
      if (!current.messages.some((item) => item.id === messageId)) {
        return current;
      }
      return {
        ...current,
        messages: current.messages.filter((item) => item.id !== messageId),
      };
    });
  }, []);

  const resetRuntime = useCallback((runtime: SessionRuntime) => {
    runtime.currentMessage = null;
    runtime.abortController = null;
    runtime.isSending = false;
    runtime.streamTerminationReason = 'none';
    runtime.attachedActiveRunId = null;
    clearRuntimeDisplayState(runtime);
  }, []);

  const persistAssistantMessage = useCallback(async (
    sessionId: string,
    message: Message,
    options?: { keepalive?: boolean },
  ) => {
    if (message.role !== 'assistant' || !hasAssistantPayload(message)) {
      return;
    }

    const normalizedTupleMessages = normalizeAssistantTupleMessages(message.assistantTupleMessages) || [];
    const normalizedToolTraces = normalizedTupleMessages.length > 0
      ? buildToolTracesFromTupleMessages(normalizedTupleMessages, message.toolTraces || [])
      : (message.toolTraces || []);

    await api.addChatMessage(
      sessionId,
      'assistant',
      message.content,
      message.thinking,
      undefined,
      undefined,
      undefined,
      message.artifacts,
      normalizedToolTraces,
      normalizedTupleMessages,
      message.wasTruncated ? {
        wasTruncated: true,
        truncatedAt: message.truncatedAt,
      } : undefined,
      message.interruption || undefined,
      {
        messageId: message.id,
        keepalive: options?.keepalive,
      },
    );
  }, []);

  const flushCurrentStreamingAssistant = useCallback((keepalive = false) => {
    const targetSessionId = currentSessionIdRef.current;
    if (!targetSessionId) {
      return;
    }

    const runtime = getExistingSessionRuntime(targetSessionId);
    const currentMessage = runtime?.currentMessage;
    if (
      !runtime
      || !runtime.state.isStreaming
      || !currentMessage
      || currentMessage.role !== 'assistant'
      || !hasAssistantPayload(currentMessage)
    ) {
      return;
    }

    void persistAssistantMessage(runtime.sessionId, currentMessage, { keepalive }).catch((error) => {
      console.warn(`Failed to persist streaming assistant message for session ${runtime.sessionId}:`, error);
    });
  }, [persistAssistantMessage]);

  useEffect(() => {
    if (typeof window === 'undefined') {
      return undefined;
    }

    const handlePageHide = () => {
      flushCurrentStreamingAssistant(true);
    };

    window.addEventListener('pagehide', handlePageHide);
    return () => {
      window.removeEventListener('pagehide', handlePageHide);
    };
  }, [flushCurrentStreamingAssistant]);

  const loadMessages = useCallback(async (runtime: SessionRuntime) => {
    const hydrationSequence = beginRuntimeHydration(runtime);
    updateSessionRuntimeState(runtime, (current) => ({
      ...current,
      isLoading: true,
    }));

    try {
      const response = await api.getChatMessages(runtime.sessionId);
      if (hydrationSequence !== runtime.hydrationSequence) {
        return;
      }

      const loadedMessages: Message[] = response.messages.map(buildRuntimeMessageFromHistory);
      let loadedAnyRun: ActiveRunState | null = null;

      try {
        const session = await api.getChatSession(runtime.sessionId);
        const sessionConfig = session.config as unknown as Record<string, unknown> | undefined;
        const runtimeName = String(sessionConfig?.runtime || '').trim().toLowerCase();
        if (runtimeName === CHAT_RUNTIME_NAME) {
          const preparedRuntime = await api.prepareChatRuntime(runtime.sessionId, {
            model_name: modelName,
            plan_mode: uiMode === 'plan',
            sync_workspace_assets: false,
            sync_kb_documents: false,
            persist_session_config: false,
          });
          ragAPI.rememberPreparedRuntime(runtime.sessionId, preparedRuntime);
          const liveRun = await ragAPI.getLiveRun(runtime.sessionId, preparedRuntime);
          if (liveRun) {
            loadedAnyRun = normalizeActiveRunState({
              runId: liveRun.runId,
              sessionId: runtime.sessionId,
              status: liveRun.status,
              startedAt: liveRun.startedAt || new Date().toISOString(),
              updatedAt: liveRun.updatedAt || liveRun.startedAt || new Date().toISOString(),
              assistantMessage: buildRecoveredActiveRunAssistantMessage(
                loadedMessages,
                liveRun.runId,
              ),
              taskSnapshotEvent: null,
              taskModeDecisionEvent: null,
            });
          }
        }
      } catch (insightRecoveryError) {
        console.warn(
          `Failed to recover lumen thread state for session ${runtime.sessionId}:`,
          insightRecoveryError,
        );
      }

      const loadedActiveRun = isLiveActiveRun(loadedAnyRun) ? loadedAnyRun : null;
      const loadedTaskSnapshot = buildTaskSnapshotStateFromHistory(
        loadedAnyRun?.taskSnapshotEvent,
      );
      const loadedTaskModeDecision = buildTaskModeDecisionStateFromHistory(
        loadedAnyRun?.taskModeDecisionEvent,
      );
      const loadedMessagesWithActiveRun = loadedAnyRun
        ? upsertMessageById(loadedMessages, loadedAnyRun.assistantMessage)
        : loadedMessages;

      runtime.hasLoadedHistory = true;
      runtime.currentMessage = loadedActiveRun?.assistantMessage || null;
      updateSessionRuntimeState(runtime, (current) => ({
        ...current,
        messages: mergeLoadedAndLiveMessages(loadedMessagesWithActiveRun, current.messages),
        taskSnapshot: loadedTaskSnapshot || current.taskSnapshot,
        taskModeDecision: loadedTaskModeDecision || current.taskModeDecision,
        activeRun: loadedActiveRun,
        isStreaming: current.isStreaming || Boolean(loadedActiveRun),
        isStopping: false,
        isLoading: false,
      }));
    } catch (error) {
      if (hydrationSequence !== runtime.hydrationSequence) {
        return;
      }
      console.error(`Failed to load messages for session ${runtime.sessionId}:`, error);
      updateSessionRuntimeState(runtime, (current) => ({
        ...current,
        isLoading: false,
      }));
      reportError(runtime, '加载历史消息失败');
    } finally {
      finishRuntimeHydration(runtime, hydrationSequence);
    }
  }, [modelName, reportError, uiMode]);

  const ensureSessionReadyForNewRun = useCallback(async (runtime: SessionRuntime) => {
    try {
      await ragAPI.ensureSessionReadyForNewRun(runtime.sessionId, {
        model_name: modelName,
        ui_mode: uiMode,
        // Persist the runtime metadata before saving the next user message.
        // Otherwise uploaded thread files use `/mnt/user-data/uploads/...`
        // paths that the backend will still reject as non-Lumen attachments.
        persist_session_config: true,
      });
    } catch (error) {
      if (isSessionActiveRunConflictError(error)) {
        await loadMessages(runtime);
      }
      throw error;
    }
  }, [loadMessages, modelName, uiMode]);

  const recoverRuntimeAfterStreamDisruption = useCallback(async (runtime: SessionRuntime) => {
    const sequence = runtime.transportRecoverySequence + 1;
    runtime.transportRecoverySequence = sequence;

    const retryDelaysMs = [0, 400, 1200, 2500];
    for (const delayMs of retryDelaysMs) {
      if (delayMs > 0) {
        await new Promise<void>((resolve) => {
          window.setTimeout(resolve, delayMs);
        });
      }

      if (runtime.transportRecoverySequence !== sequence) {
        return;
      }

      // 已重新接回流式任务时，后续轮询没有意义。
      if (runtime.abortController || runtime.isSending || runtime.state.isStreaming) {
        return;
      }

      await loadMessages(runtime);

      if (runtime.transportRecoverySequence !== sequence) {
        return;
      }

      // 一旦恢复到 active run，页面会自动重新附着，无需继续查询历史。
      if (runtime.state.activeRun) {
        return;
      }
    }
  }, [loadMessages]);

  useEffect(() => {
    if (!currentSessionId) {
      return;
    }
    const runtime = getSessionRuntime(currentSessionId);
    if (runtime.hasLoadedHistory || runtime.state.isLoading || runtime.state.messages.length > 0) {
      return;
    }
    void loadMessages(runtime);
  }, [currentSessionId, loadMessages]);

  useEffect(() => {
    if (!currentSessionId || runtimeState.isStreaming || runtimeState.isLoading) {
      return;
    }
    if (!hasPendingAttachmentsInMessages(runtimeState.messages)) {
      return;
    }

    const runtime = getSessionRuntime(currentSessionId);
    const timer = window.setInterval(() => {
      if (runtime.isSending || runtime.state.isStreaming || runtime.state.isLoading) {
        return;
      }
      void loadMessages(runtime);
    }, 5000);

    return () => {
      window.clearInterval(timer);
    };
  }, [
    currentSessionId,
    loadMessages,
    runtimeState.isLoading,
    runtimeState.isStreaming,
    runtimeState.messages,
  ]);

  const abortActiveStream = useCallback((
    sessionId: string | null | undefined,
    reason: Exclude<StreamTerminationReason, 'none'>,
    options?: { updateState?: boolean },
  ) => {
    const runtime = getExistingSessionRuntime(sessionId);
    const shouldUpdateState = options?.updateState !== false;

    if (!runtime) {
      sessionlessSendLockRef.current = false;
      return false;
    }

    const runIdToCancel = (
      runtime.state.activeRun?.runId
      || runtime.attachedActiveRunId
      || undefined
    );

    const controller = runtime.abortController;
    if (!controller) {
      runtime.streamTerminationReason = 'none';
      runtime.isSending = false;
      runtime.attachedActiveRunId = null;
      if (shouldUpdateState) {
        updateSessionRuntimeState(runtime, (current) => ({
          ...current,
          activeRun: null,
          isStreaming: false,
          isStopping: false,
        }));
      }
      if (runtime.state.activeRun) {
        void ragAPI.cancelRun(runtime.sessionId, runIdToCancel).catch((error) => {
          console.warn(`Failed to cancel backend stream for session ${runtime.sessionId}:`, error);
        });
      }
      sessionlessSendLockRef.current = false;
      return false;
    }

    runtime.streamTerminationReason = reason;
    runtime.abortController = null;
    runtime.attachedActiveRunId = null;
    if (shouldUpdateState) {
      updateSessionRuntimeState(runtime, (current) => ({
        ...current,
        activeRun: null,
        isStreaming: false,
        isStopping: reason === 'user_stop',
      }));
    }

    void ragAPI.cancelRun(runtime.sessionId, runIdToCancel).catch((error) => {
      console.warn(`Failed to cancel backend stream for session ${runtime.sessionId}:`, error);
    });

    controller.abort();
    return true;
  }, []);

  const attachToActiveRun = useCallback(async (
    runtime: SessionRuntime,
    activeRun: ActiveRunState,
  ) => {
    const assistantMessage = runtime.state.messages.find(
      (message) => message.id === activeRun.assistantMessage.id,
    ) || activeRun.assistantMessage;
    const controller = new AbortController();
    let renderScheduled = false;
    let renderHandle: number | null = null;
    let hasTupleProtocolEvents = Boolean((assistantMessage.assistantTupleMessages || []).length > 0);
    let hasTupleContentEvents = Boolean(
      (assistantMessage.assistantTupleMessages || []).some((item) => (
        item.type === 'ai'
        && (!item.tool_calls || item.tool_calls.length === 0)
        && Boolean((item.content || '').length)
      )),
    );
    let isUsingTokenTupleContentFallback = false;
    let hasReceivedFirstContentToken = Boolean((assistantMessage.content || '').length > 0);

    const cancelScheduledRender = () => {
      if (renderHandle === null) {
        return;
      }
      if (typeof window !== 'undefined' && typeof window.cancelAnimationFrame === 'function') {
        window.cancelAnimationFrame(renderHandle);
      } else {
        clearTimeout(renderHandle);
      }
      renderHandle = null;
      renderScheduled = false;
    };

    const syncAssistantMessageToRuntime = () => {
      if (runtime.currentMessage?.id !== assistantMessage.id) {
        runtime.currentMessage = assistantMessage;
      }
      replaceMessageInRuntime(runtime, assistantMessage);
    };

    const scheduleAssistantRender = (immediate = false) => {
      if (immediate) {
        cancelScheduledRender();
        syncAssistantMessageToRuntime();
        return;
      }
      if (renderScheduled) {
        return;
      }
      renderScheduled = true;
      const flush = () => {
        renderScheduled = false;
        renderHandle = null;
        syncAssistantMessageToRuntime();
      };
      if (typeof window !== 'undefined' && typeof window.requestAnimationFrame === 'function') {
        renderHandle = window.requestAnimationFrame(flush);
      } else {
        renderHandle = window.setTimeout(flush, 16);
      }
    };

    const emitFirstContentToken = (contentChunk?: string) => {
      if (hasReceivedFirstContentToken || !contentChunk) {
        return;
      }
      hasReceivedFirstContentToken = true;
      notifyFirstContentToken(runtime, assistantMessage.id);
    };

    runtime.currentMessage = assistantMessage;
    runtime.abortController = controller;
    runtime.isSending = true;
    runtime.streamTerminationReason = 'none';
    runtime.attachedActiveRunId = activeRun.runId;
    updateSessionRuntimeState(runtime, (current) => ({
      ...current,
      messages: upsertMessageById(current.messages, assistantMessage),
      activeRun,
      isStreaming: true,
      isStopping: false,
    }));

    const finalizeStream = (clearActiveRun: boolean) => {
      cancelScheduledRender();
      if (runtime.abortController === controller) {
        runtime.abortController = null;
      }
      runtime.isSending = false;
      if (runtime.currentMessage?.id === assistantMessage.id) {
        runtime.currentMessage = null;
      }
      runtime.attachedActiveRunId = null;
      updateSessionRuntimeState(runtime, (current) => ({
        ...current,
        activeRun: clearActiveRun ? null : current.activeRun,
        isStreaming: false,
        isStopping: false,
      }));
    };

    try {
      await ragAPI.joinActiveRun(runtime.sessionId, activeRun.runId, {
        signal: controller.signal,
        resumeState: buildResumeStreamState(assistantMessage),
        onThinking: (thinking) => {
          assistantMessage.thinking = (assistantMessage.thinking || '') + thinking;
          assistantMessage.lastEventAt = Date.now();
          scheduleAssistantRender();
        },
        onToken: (token) => {
          emitFirstContentToken(token);
          assistantMessage.content += token;
          if (!hasTupleContentEvents) {
            assistantMessage.assistantTupleMessages = appendTupleContentMessage(
              assistantMessage.assistantTupleMessages,
              token,
            );
            isUsingTokenTupleContentFallback = true;
          }
          assistantMessage.lastEventAt = Date.now();
          scheduleAssistantRender(true);
        },
        onFinalAnswer: (data) => {
          emitFirstContentToken(data.answer);
          const shouldSyncTupleContent = !hasTupleContentEvents || isUsingTokenTupleContentFallback;
          if (applyFinalAnswerToAssistantMessage(assistantMessage, data.answer, {
            syncTupleContent: shouldSyncTupleContent,
          })) {
            scheduleAssistantRender(true);
          }
          if (shouldSyncTupleContent) {
            hasTupleContentEvents = true;
            isUsingTokenTupleContentFallback = true;
          }
        },
        onArtifact: (artifact) => {
          const normalizedArtifact = normalizeArtifact(artifact);
          if (!normalizedArtifact) {
            return;
          }
          const existingArtifacts = assistantMessage.artifacts || [];
          if (existingArtifacts.some((item) => item.object_path === normalizedArtifact.object_path)) {
            return;
          }
          assistantMessage.artifacts = [...existingArtifacts, normalizedArtifact];
          assistantMessage.lastEventAt = Date.now();
          scheduleAssistantRender();
        },
        onToolTrace: (trace) => {
          const normalizedTrace = normalizeToolTrace(trace);
          if (!normalizedTrace) {
            return;
          }
          assistantMessage.toolTraces = upsertToolTrace(
            assistantMessage.toolTraces || [],
            normalizedTrace,
          );
          if (!hasTupleProtocolEvents) {
            const fallbackToolCallId = (normalizedTrace.call_id || '').trim() || getToolTraceStableKey(normalizedTrace);
            const phase = (trace.phase || '').trim();
            const shouldTreatAsToolStart = (
              phase === 'tool_start'
              || normalizedTrace.status === 'running'
            );

            if (shouldTreatAsToolStart) {
              assistantMessage.assistantTupleMessages = upsertTupleToolCallMessage(
                assistantMessage.assistantTupleMessages,
                {
                  id: fallbackToolCallId,
                  name: normalizedTrace.name,
                  args: normalizedTrace.args,
                },
              );
            } else {
              assistantMessage.assistantTupleMessages = upsertTupleToolResultMessage(
                assistantMessage.assistantTupleMessages,
                {
                  toolCallId: fallbackToolCallId,
                  toolName: normalizedTrace.name,
                  content: stringifyTupleToolResult(normalizedTrace),
                  status: normalizedTrace.status,
                },
              );
            }
          }
          assistantMessage.lastEventAt = Date.now();
          scheduleAssistantRender();
        },
        onMessageTuple: (tupleMessage) => {
          hasTupleProtocolEvents = true;
          const normalizedTuple = normalizeAssistantTupleMessage(tupleMessage);
          if (!normalizedTuple) {
            return;
          }

          if (normalizedTuple.type === 'ai') {
            emitFirstContentToken(normalizedTuple.content);
            if (normalizedTuple.tool_calls && normalizedTuple.tool_calls.length > 0) {
              let nextTupleMessages = assistantMessage.assistantTupleMessages;
              normalizedTuple.tool_calls.forEach((toolCall) => {
                nextTupleMessages = upsertTupleToolCallMessage(nextTupleMessages, toolCall);
              });
              assistantMessage.assistantTupleMessages = nextTupleMessages;
            } else {
              hasTupleContentEvents = true;
              if (!isUsingTokenTupleContentFallback) {
                assistantMessage.assistantTupleMessages = appendTupleContentMessage(
                  assistantMessage.assistantTupleMessages,
                  normalizedTuple.content || '',
                );
              }
            }
          } else {
            assistantMessage.assistantTupleMessages = upsertTupleToolResultMessage(
              assistantMessage.assistantTupleMessages,
              {
                toolCallId: normalizedTuple.tool_call_id || normalizedTuple.id,
                toolName: normalizedTuple.name || 'unknown_tool',
                content: normalizedTuple.content || '',
                status: normalizedTuple.status,
              },
            );
          }

          assistantMessage.lastEventAt = Date.now();
          scheduleAssistantRender(
            normalizedTuple.type === 'ai'
            && (!normalizedTuple.tool_calls || normalizedTuple.tool_calls.length === 0)
            && Boolean((normalizedTuple.content || '').length),
          );
        },
        onTaskSnapshot: (snapshot) => {
          updateSessionRuntimeState(runtime, (current) => ({
            ...current,
            taskSnapshot: applyTaskSnapshotEvent(current.taskSnapshot, snapshot),
          }));
        },
        onTaskDelta: (delta) => {
          updateSessionRuntimeState(runtime, (current) => ({
            ...current,
            taskSnapshot: applyTaskDeltaEvent(current.taskSnapshot, delta),
          }));
        },
        onTaskModeDecision: (decision) => {
          updateSessionRuntimeState(runtime, (current) => ({
            ...current,
            taskModeDecision: applyTaskModeDecisionEvent(current.taskModeDecision, decision),
          }));
        },
        onError: (error) => {
          const normalizedError = ensureDescriptiveStreamError(error);
          const terminationReason = runtime.streamTerminationReason;
          if (terminationReason !== 'none') {
            if (terminationReason === 'user_stop') {
              finalizeDanglingAssistantTools(assistantMessage, '生成已被用户停止，工具调用未完成。');
              assistantMessage.wasTruncated = true;
              assistantMessage.truncatedAt = new Date().toISOString();
              assistantMessage.lastEventAt = Date.now();
              scheduleAssistantRender(true);
            }
            runtime.streamTerminationReason = 'none';
            finalizeStream(true);
            return;
          }

          if (isRecoverableTransportStreamError(normalizedError)) {
            runtime.streamTerminationReason = 'none';
            finalizeStream(false);
            void recoverRuntimeAfterStreamDisruption(runtime);
            return;
          }

          if (hasClarificationPayload(assistantMessage)) {
            finalizeDanglingAssistantTools(assistantMessage, '澄清请求已中断当前执行，工具调用未完成。');
            assistantMessage.interruption = null;
            assistantMessage.lastEventAt = Date.now();
            scheduleAssistantRender(true);
            finalizeStream(true);
            void loadMessages(runtime);
            return;
          }

          finalizeDanglingAssistantTools(assistantMessage, normalizeInterruptionReason(normalizedError));
          assistantMessage.interruption = buildInterruptionPayload(normalizedError);
          assistantMessage.lastEventAt = Date.now();
          scheduleAssistantRender(true);
          reportError(runtime, normalizedError);
          finalizeStream(true);
        },
        onDone: async () => {
          const terminationReason = runtime.streamTerminationReason;
          const wasTruncated = terminationReason === 'user_stop';
          runtime.streamTerminationReason = 'none';
          if (wasTruncated) {
            finalizeDanglingAssistantTools(assistantMessage, '生成已被用户停止，工具调用未完成。');
            assistantMessage.wasTruncated = true;
            assistantMessage.truncatedAt = new Date().toISOString();
            notifyStopComplete(runtime);
          }
          scheduleAssistantRender(true);
          finalizeStream(true);
          void loadMessages(runtime);
        },
      });
    } catch (error) {
      const terminationReason = runtime.streamTerminationReason;
      runtime.streamTerminationReason = 'none';
      if (terminationReason === 'none') {
        reportError(runtime, error instanceof Error ? error : String(error));
      }
      finalizeStream(false);
    }
  }, [
    normalizeArtifact,
    normalizeToolTrace,
    notifyFirstContentToken,
    notifyStopComplete,
    replaceMessageInRuntime,
    reportError,
    recoverRuntimeAfterStreamDisruption,
    upsertToolTrace,
    loadMessages,
  ]);

  useEffect(() => {
    if (!currentSessionId) {
      return;
    }
    const runtime = getExistingSessionRuntime(currentSessionId) || getSessionRuntime(currentSessionId);
    const activeRun = runtime.state.activeRun;
    if (!activeRun) {
      runtime.attachedActiveRunId = null;
      return;
    }
    if (runtime.attachedActiveRunId === activeRun.runId) {
      return;
    }
    if (runtime.abortController || runtime.isSending) {
      return;
    }
    runtime.attachedActiveRunId = activeRun.runId;
    void attachToActiveRun(runtime, activeRun);
  }, [
    attachToActiveRun,
    currentSessionId,
    runtimeState.activeRun,
  ]);

  const streamAssistantMessage = useCallback(async (params: {
    runtime: SessionRuntime;
    requestMessage: string;
    imageDataUrls?: string[];
    attachments?: ChatAttachment[];
    assistantMessage: Message;
    saveErrorContext: string;
  }) => {
    const {
      runtime,
      requestMessage,
      imageDataUrls,
      attachments,
      assistantMessage,
      saveErrorContext,
    } = params;

    let renderScheduled = false;
    let renderHandle: number | null = null;
    let hasTupleProtocolEvents = false;
    let hasTupleContentEvents = false;
    let isUsingTokenTupleContentFallback = false;
    let hasReceivedFirstContentToken = false;
    const controller = new AbortController();

    const cancelScheduledRender = () => {
      if (renderHandle === null) {
        return;
      }
      if (typeof window !== 'undefined' && typeof window.cancelAnimationFrame === 'function') {
        window.cancelAnimationFrame(renderHandle);
      } else {
        clearTimeout(renderHandle);
      }
      renderHandle = null;
      renderScheduled = false;
    };

    const syncAssistantMessageToRuntime = () => {
      if (runtime.currentMessage?.id !== assistantMessage.id) {
        return;
      }
      replaceMessageInRuntime(runtime, assistantMessage);
    };

    const scheduleAssistantRender = (immediate = false) => {
      if (immediate) {
        cancelScheduledRender();
        syncAssistantMessageToRuntime();
        return;
      }
      if (renderScheduled) {
        return;
      }
      renderScheduled = true;
      const flush = () => {
        renderScheduled = false;
        renderHandle = null;
        syncAssistantMessageToRuntime();
      };
      if (typeof window !== 'undefined' && typeof window.requestAnimationFrame === 'function') {
        renderHandle = window.requestAnimationFrame(flush);
      } else {
        renderHandle = window.setTimeout(flush, 16);
      }
    };

    const emitFirstContentToken = (contentChunk?: string) => {
      if (hasReceivedFirstContentToken || !contentChunk) {
        return;
      }
      hasReceivedFirstContentToken = true;
      notifyFirstContentToken(runtime, assistantMessage.id);
    };

    runtime.currentMessage = assistantMessage;
    runtime.abortController = controller;
    runtime.isSending = true;
    runtime.streamTerminationReason = 'none';
    runtime.attachedActiveRunId = null;
    updateSessionRuntimeState(runtime, (current) => ({
      ...current,
      activeRun: null,
      isStreaming: true,
      isStopping: false,
    }));

    const finalizeStream = (clearActiveRun = true) => {
      cancelScheduledRender();
      if (runtime.abortController === controller) {
        runtime.abortController = null;
      }
      runtime.isSending = false;
      if (runtime.currentMessage?.id === assistantMessage.id) {
        runtime.currentMessage = null;
      }
      runtime.attachedActiveRunId = null;
      updateSessionRuntimeState(runtime, (current) => ({
        ...current,
        activeRun: clearActiveRun ? null : current.activeRun,
        isStreaming: false,
        isStopping: false,
      }));
    };

    try {
      await ragAPI.streamChat({
        kb_id: kbId,
        doc_ids: docIds,
        model_name: modelName,
        message: requestMessage,
        image_data_urls: imageDataUrls,
        attachments,
        session_id: runtime.sessionId,
        assistant_message_id: assistantMessage.id,
        ui_mode: uiMode,
        signal: controller.signal,
        onResumeSnapshot: (snapshot) => {
          const normalizedRun = normalizeActiveRunState(snapshot);
          if (!normalizedRun) {
            return;
          }
          const previousAssistantMessageId = assistantMessage.id;
          assistantMessage.id = normalizedRun.assistantMessage.id;
          assistantMessage.content = normalizedRun.assistantMessage.content;
          assistantMessage.thinking = normalizedRun.assistantMessage.thinking;
          assistantMessage.artifacts = normalizedRun.assistantMessage.artifacts;
          assistantMessage.toolTraces = normalizedRun.assistantMessage.toolTraces;
          assistantMessage.assistantTupleMessages = normalizedRun.assistantMessage.assistantTupleMessages;
          assistantMessage.wasTruncated = normalizedRun.assistantMessage.wasTruncated;
          assistantMessage.truncatedAt = normalizedRun.assistantMessage.truncatedAt;
          assistantMessage.interruption = normalizedRun.assistantMessage.interruption;
          assistantMessage.lastEventAt = Date.now();
          const nextTaskSnapshot = buildTaskSnapshotStateFromHistory(normalizedRun.taskSnapshotEvent);
          const nextTaskModeDecision = buildTaskModeDecisionStateFromHistory(normalizedRun.taskModeDecisionEvent);
          updateSessionRuntimeState(runtime, (current) => ({
            ...current,
            messages: current.messages.map((message) => (
              message.id === previousAssistantMessageId
                ? { ...assistantMessage }
                : message
            )),
            activeRun: normalizedRun,
            taskSnapshot: nextTaskSnapshot || current.taskSnapshot,
            taskModeDecision: nextTaskModeDecision || current.taskModeDecision,
          }));
          scheduleAssistantRender(true);
        },
        onThinking: (thinking) => {
          assistantMessage.thinking = (assistantMessage.thinking || '') + thinking;
          assistantMessage.lastEventAt = Date.now();
          scheduleAssistantRender();
        },
        onToken: (token) => {
          emitFirstContentToken(token);
          assistantMessage.content += token;
          if (!hasTupleContentEvents) {
            assistantMessage.assistantTupleMessages = appendTupleContentMessage(
              assistantMessage.assistantTupleMessages,
              token,
            );
            isUsingTokenTupleContentFallback = true;
          }
          assistantMessage.lastEventAt = Date.now();
          scheduleAssistantRender(true);
        },
        onFinalAnswer: (data) => {
          emitFirstContentToken(data.answer);
          const shouldSyncTupleContent = !hasTupleContentEvents || isUsingTokenTupleContentFallback;
          if (applyFinalAnswerToAssistantMessage(assistantMessage, data.answer, {
            syncTupleContent: shouldSyncTupleContent,
          })) {
            scheduleAssistantRender(true);
          }
          if (shouldSyncTupleContent) {
            hasTupleContentEvents = true;
            isUsingTokenTupleContentFallback = true;
          }
        },
        onArtifact: (artifact) => {
          const normalizedArtifact = normalizeArtifact(artifact);
          if (!normalizedArtifact) {
            return;
          }
          const existingArtifacts = assistantMessage.artifacts || [];
          if (existingArtifacts.some((item) => item.object_path === normalizedArtifact.object_path)) {
            return;
          }
          assistantMessage.artifacts = [...existingArtifacts, normalizedArtifact];
          assistantMessage.lastEventAt = Date.now();
          scheduleAssistantRender();
        },
        onToolTrace: (trace) => {
          const normalizedTrace = normalizeToolTrace(trace);
          if (!normalizedTrace) {
            return;
          }
          assistantMessage.toolTraces = upsertToolTrace(
            assistantMessage.toolTraces || [],
            normalizedTrace,
          );
          if (!hasTupleProtocolEvents) {
            const fallbackToolCallId = (normalizedTrace.call_id || '').trim() || getToolTraceStableKey(normalizedTrace);
            const phase = (trace.phase || '').trim();
            const shouldTreatAsToolStart = (
              phase === 'tool_start'
              || normalizedTrace.status === 'running'
            );

            if (shouldTreatAsToolStart) {
              assistantMessage.assistantTupleMessages = upsertTupleToolCallMessage(
                assistantMessage.assistantTupleMessages,
                {
                  id: fallbackToolCallId,
                  name: normalizedTrace.name,
                  args: normalizedTrace.args,
                },
              );
            } else {
              assistantMessage.assistantTupleMessages = upsertTupleToolResultMessage(
                assistantMessage.assistantTupleMessages,
                {
                  toolCallId: fallbackToolCallId,
                  toolName: normalizedTrace.name,
                  content: stringifyTupleToolResult(normalizedTrace),
                  status: normalizedTrace.status,
                },
              );
            }
          }
          assistantMessage.lastEventAt = Date.now();
          scheduleAssistantRender();
        },
        onMessageTuple: (tupleMessage) => {
          hasTupleProtocolEvents = true;
          const normalizedTuple = normalizeAssistantTupleMessage(tupleMessage);
          if (!normalizedTuple) {
            return;
          }

          if (normalizedTuple.type === 'ai') {
            emitFirstContentToken(normalizedTuple.content);
            if (normalizedTuple.tool_calls && normalizedTuple.tool_calls.length > 0) {
              let nextTupleMessages = assistantMessage.assistantTupleMessages;
              normalizedTuple.tool_calls.forEach((toolCall) => {
                nextTupleMessages = upsertTupleToolCallMessage(nextTupleMessages, toolCall);
              });
              assistantMessage.assistantTupleMessages = nextTupleMessages;
            } else {
              hasTupleContentEvents = true;
              if (!isUsingTokenTupleContentFallback) {
                assistantMessage.assistantTupleMessages = appendTupleContentMessage(
                  assistantMessage.assistantTupleMessages,
                  normalizedTuple.content || '',
                );
              }
            }
          } else {
            assistantMessage.assistantTupleMessages = upsertTupleToolResultMessage(
              assistantMessage.assistantTupleMessages,
              {
                toolCallId: normalizedTuple.tool_call_id || normalizedTuple.id,
                toolName: normalizedTuple.name || 'unknown_tool',
                content: normalizedTuple.content || '',
                status: normalizedTuple.status,
              },
            );
          }

          assistantMessage.lastEventAt = Date.now();
          scheduleAssistantRender(
            normalizedTuple.type === 'ai'
            && (!normalizedTuple.tool_calls || normalizedTuple.tool_calls.length === 0)
            && Boolean((normalizedTuple.content || '').length),
          );
        },
        onTaskSnapshot: (snapshot) => {
          updateSessionRuntimeState(runtime, (current) => ({
            ...current,
            taskSnapshot: applyTaskSnapshotEvent(current.taskSnapshot, snapshot),
          }));
        },
        onTaskDelta: (delta) => {
          updateSessionRuntimeState(runtime, (current) => ({
            ...current,
            taskSnapshot: applyTaskDeltaEvent(current.taskSnapshot, delta),
          }));
        },
        onTaskModeDecision: (decision) => {
          updateSessionRuntimeState(runtime, (current) => ({
            ...current,
            taskModeDecision: applyTaskModeDecisionEvent(current.taskModeDecision, decision),
          }));
        },
        onError: (error) => {
          const normalizedError = ensureDescriptiveStreamError(error);
          const terminationReason = runtime.streamTerminationReason;
          if (terminationReason !== 'none') {
            if (terminationReason === 'user_stop') {
              finalizeDanglingAssistantTools(assistantMessage, '生成已被用户停止，工具调用未完成。');
              assistantMessage.wasTruncated = true;
              assistantMessage.truncatedAt = new Date().toISOString();
              assistantMessage.lastEventAt = Date.now();
              scheduleAssistantRender(true);
            }
            runtime.streamTerminationReason = 'none';
            finalizeStream();
            return;
          }

          if (isRecoverableTransportStreamError(normalizedError)) {
            runtime.streamTerminationReason = 'none';
            finalizeStream(false);
            void recoverRuntimeAfterStreamDisruption(runtime);
            return;
          }

          if (hasClarificationPayload(assistantMessage)) {
            finalizeDanglingAssistantTools(assistantMessage, '澄清请求已中断当前执行，工具调用未完成。');
            assistantMessage.interruption = null;
            assistantMessage.lastEventAt = Date.now();
            scheduleAssistantRender(true);
            if (hasAssistantPayload(assistantMessage)) {
              void persistAssistantMessage(runtime.sessionId, assistantMessage).catch((saveError) => {
                console.error(`Failed to save ${saveErrorContext}:`, saveError);
                reportError(runtime, '消息保存失败，请重试');
              });
            }
            runtime.streamTerminationReason = 'none';
            finalizeStream();
            return;
          }

          const shouldAttachInlineInterruption = (
            !isQuotaExceededStreamError(normalizedError)
            && !isAuthExpiredStreamError(normalizedError)
          );

          if (shouldAttachInlineInterruption) {
            finalizeDanglingAssistantTools(assistantMessage, normalizeInterruptionReason(normalizedError));
            assistantMessage.interruption = buildInterruptionPayload(normalizedError);
            assistantMessage.lastEventAt = Date.now();
            scheduleAssistantRender(true);
            if (hasAssistantPayload(assistantMessage)) {
              void persistAssistantMessage(runtime.sessionId, assistantMessage).catch((saveError) => {
                console.error(`Failed to save ${saveErrorContext}:`, saveError);
                reportError(runtime, '消息保存失败，请重试');
              });
            }
          } else {
            if (!hasAssistantPayload(assistantMessage)) {
              removeMessageFromRuntime(runtime, assistantMessage.id);
            }
            reportError(runtime, normalizedError);
          }

          runtime.streamTerminationReason = 'none';
          finalizeStream();
        },
        onDone: async () => {
          const terminationReason = runtime.streamTerminationReason;
          const shouldDiscardMessage = terminationReason === 'discard';
          const wasTruncated = terminationReason === 'user_stop';
          const truncatedAt = wasTruncated ? new Date().toISOString() : undefined;
          runtime.streamTerminationReason = 'none';

          if (!shouldDiscardMessage) {
            scheduleAssistantRender(true);
          }

          if (!shouldDiscardMessage && hasAssistantPayload(assistantMessage)) {
            try {
              if (wasTruncated) {
                finalizeDanglingAssistantTools(assistantMessage, '生成已被用户停止，工具调用未完成。');
              }
              assistantMessage.wasTruncated = wasTruncated;
              assistantMessage.truncatedAt = truncatedAt;
              await persistAssistantMessage(runtime.sessionId, assistantMessage);
              if (wasTruncated) {
                notifyStopComplete(runtime);
              }
            } catch (saveError) {
              console.error(`Failed to save ${saveErrorContext}:`, saveError);
              reportError(runtime, '消息保存失败，请重试');
            }
          } else if (wasTruncated) {
            notifyStopComplete(runtime);
          }

          finalizeStream();
        },
      });
    } catch (error) {
      const terminationReason = runtime.streamTerminationReason;
      runtime.streamTerminationReason = 'none';
      if (terminationReason === 'none') {
        reportError(runtime, error instanceof Error ? error : String(error));
      }
      removeMessageFromRuntime(runtime, assistantMessage.id);
      finalizeStream();
    }
  }, [
    docIds,
    kbId,
    modelName,
    normalizeArtifact,
    normalizeToolTrace,
    notifyFirstContentToken,
    notifyStopComplete,
    persistAssistantMessage,
    recoverRuntimeAfterStreamDisruption,
    removeMessageFromRuntime,
    replaceMessageInRuntime,
    reportError,
    uiMode,
    upsertToolTrace,
  ]);

  const sendMessage = useCallback(async (
    content: string,
    options?: { imageDataUrls?: string[]; attachments?: ChatAttachment[] },
  ) => {
    const trimmedContent = content.trim();
    if (!trimmedContent) {
      return;
    }

    let targetSessionId = currentSessionIdRef.current;
    if (!targetSessionId && sessionlessSendLockRef.current) {
      console.warn('已有消息正在发送中，请稍候');
      return;
    }

    let runtime = targetSessionId ? getSessionRuntime(targetSessionId) : null;
    if (runtime && (runtime.state.isStreaming || runtime.isSending)) {
      console.warn('已有消息正在发送中，请稍候');
      return;
    }

    if (!targetSessionId) {
      sessionlessSendLockRef.current = true;
    }

    let cleanupMessageId: string | null = null;
    let createdSessionId: string | null = null;

    try {
      if (!targetSessionId) {
        const sessionConfig = {
          uiMode,
          kbIds: kbId ? [kbId] : [],
          docIds: docIds || [],
          sourceType,
          isKBLocked: !!(kbId || (docIds && docIds.length > 0)),
          modelName,
        };

        const session = await api.createChatSession(trimmedContent, sessionConfig);
        initializeEmptySessionRuntime(session.id);
        targetSessionId = session.id;
        createdSessionId = session.id;
        currentSessionIdRef.current = session.id;
        runtime = getSessionRuntime(session.id);
        setSessionRuntimeCallbacks(runtime, {
          onError,
          onSessionCreated,
          onFirstContentToken,
          onStopComplete,
        });
      }

      if (!runtime || !targetSessionId) {
        throw new Error('会话初始化失败');
      }

      const userMessage: Message = {
        id: generateUUID(),
        role: 'user',
        content: trimmedContent,
        imageDataUrls: options?.imageDataUrls || [],
        attachments: options?.attachments || [],
      };

      if (createdSessionId) {
        cleanupMessageId = userMessage.id;
        appendMessageToRuntime(runtime, userMessage);
        if (isMountedRef.current) {
          setOptimisticSessionId(createdSessionId);
        }
        onSessionCreated?.(createdSessionId);
      }

      await ensureSessionReadyForNewRun(runtime);
      sessionlessSendLockRef.current = false;

      if (!createdSessionId) {
        cleanupMessageId = userMessage.id;
        appendMessageToRuntime(runtime, userMessage);
      }

      await api.addChatMessage(
        targetSessionId,
        'user',
        trimmedContent,
        undefined,
        options?.imageDataUrls || [],
        options?.attachments || [],
        undefined,
        undefined,
        undefined,
        undefined,
        undefined,
        undefined,
        {
          messageId: userMessage.id,
        },
      );

      const assistantMessage: Message = {
        id: generateUUID(),
        role: 'assistant',
        content: '',
        thinking: '',
        artifacts: [],
        toolTraces: [],
        assistantTupleMessages: [],
        interruption: null,
        lastEventAt: Date.now(),
      };
      cleanupMessageId = assistantMessage.id;
      appendMessageToRuntime(runtime, assistantMessage);

      await streamAssistantMessage({
        runtime,
        requestMessage: trimmedContent,
        imageDataUrls: options?.imageDataUrls,
        attachments: options?.attachments,
        assistantMessage,
        saveErrorContext: 'assistant message',
      });
    } catch (error) {
      sessionlessSendLockRef.current = false;
      if (runtime) {
        const shouldPreserveRecoveredActiveRun = isSessionActiveRunConflictError(error);
        if (!shouldPreserveRecoveredActiveRun && cleanupMessageId) {
          removeMessageFromRuntime(runtime, cleanupMessageId);
        }
        if (!shouldPreserveRecoveredActiveRun && runtime.currentMessage?.id === cleanupMessageId) {
          runtime.currentMessage = null;
        }
        runtime.abortController = null;
        runtime.isSending = false;
        runtime.streamTerminationReason = 'none';
        if (!shouldPreserveRecoveredActiveRun) {
          updateSessionRuntimeState(runtime, (current) => ({
            ...current,
            isStreaming: false,
            isStopping: false,
          }));
        }
      }
      reportError(runtime, error instanceof Error ? error : String(error));
    }
  }, [
    appendMessageToRuntime,
    docIds,
    ensureSessionReadyForNewRun,
    kbId,
    modelName,
    onError,
    onFirstContentToken,
    onSessionCreated,
    onStopComplete,
    removeMessageFromRuntime,
    reportError,
    sourceType,
    streamAssistantMessage,
    uiMode,
  ]);

  const regenerateLastMessage = useCallback(async () => {
    const targetSessionId = currentSessionIdRef.current;
    if (!targetSessionId) {
      console.warn('没有会话ID，无法重新生成');
      return;
    }

    const runtime = getExistingSessionRuntime(targetSessionId) || getSessionRuntime(targetSessionId);
    if (runtime.state.messages.length < 2) {
      return;
    }
    if (runtime.state.isStreaming || runtime.isSending) {
      console.warn('已有消息正在发送中，请稍候');
      return;
    }

    const lastUserMessage = runtime.state.messages
      .slice()
      .reverse()
      .find((message) => message.role === 'user');
    if (!lastUserMessage) {
      return;
    }

    let cleanupMessageId: string | null = null;

    try {
      await ensureSessionReadyForNewRun(runtime);

      try {
        await api.deleteLastAssistantMessage(targetSessionId);
      } catch (error) {
        console.error('Failed to delete old assistant message:', error);
      }

      updateSessionRuntimeState(runtime, (current) => {
        const lastAssistantIndex = current.messages.map((message) => message.role).lastIndexOf('assistant');
        if (lastAssistantIndex === -1) {
          return current;
        }
        return {
          ...current,
          messages: current.messages.filter((_, index) => index !== lastAssistantIndex),
        };
      });

      const assistantMessage: Message = {
        id: generateUUID(),
        role: 'assistant',
        content: '',
        thinking: '',
        artifacts: [],
        toolTraces: [],
        assistantTupleMessages: [],
        interruption: null,
        lastEventAt: Date.now(),
      };
      cleanupMessageId = assistantMessage.id;
      appendMessageToRuntime(runtime, assistantMessage);

      await streamAssistantMessage({
        runtime,
        requestMessage: lastUserMessage.content,
        imageDataUrls: lastUserMessage.imageDataUrls || [],
        attachments: lastUserMessage.attachments || [],
        assistantMessage,
        saveErrorContext: 'regenerated message',
      });
    } catch (error) {
      const shouldPreserveRecoveredActiveRun = isSessionActiveRunConflictError(error);
      if (!shouldPreserveRecoveredActiveRun && cleanupMessageId) {
        removeMessageFromRuntime(runtime, cleanupMessageId);
      }
      if (!shouldPreserveRecoveredActiveRun && runtime.currentMessage?.id === cleanupMessageId) {
        runtime.currentMessage = null;
      }
      runtime.abortController = null;
      runtime.isSending = false;
      runtime.streamTerminationReason = 'none';
      if (!shouldPreserveRecoveredActiveRun) {
        updateSessionRuntimeState(runtime, (current) => ({
          ...current,
          isStreaming: false,
          isStopping: false,
        }));
      }
      reportError(runtime, error instanceof Error ? error : String(error));
    }
  }, [appendMessageToRuntime, ensureSessionReadyForNewRun, removeMessageFromRuntime, reportError, streamAssistantMessage]);

  const clearMessages = useCallback((options?: ClearMessagesOptions) => {
    const preserveSessionRuntime = options?.preserveSessionRuntime === true;
    const targetSessionId = currentSessionIdRef.current;
    if (targetSessionId) {
      const runtime = getExistingSessionRuntime(targetSessionId);
      if (runtime) {
        if (preserveSessionRuntime) {
          runtime.lastAccessedAt = Date.now();
        } else if (runtime.abortController) {
          clearRuntimeDisplayState(runtime);
          abortActiveStream(targetSessionId, 'discard', { updateState: false });
        } else {
          resetRuntime(runtime);
        }
      }
    }
    sessionlessSendLockRef.current = false;
    currentSessionIdRef.current = null;
    setOptimisticSessionId(null);
  }, [abortActiveStream, resetRuntime]);

  const stopGeneration = useCallback(() => {
    abortActiveStream(currentSessionIdRef.current, 'user_stop');
  }, [abortActiveStream]);

  return {
    messages: runtimeState.messages,
    taskSnapshot: runtimeState.taskSnapshot,
    taskModeDecision: runtimeState.taskModeDecision,
    isHydratingSessionState: runtimeState.isHydratingSessionState,
    isStreaming: runtimeState.isStreaming,
    isStopping: runtimeState.isStopping,
    isLoading: runtimeState.isLoading,
    sendMessage,
    regenerateLastMessage,
    clearMessages,
    stopGeneration,
    sessionId: currentSessionId || undefined,
  };
}
