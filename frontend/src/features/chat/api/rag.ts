import {
  api,
  type ChatActiveRun,
  type ChatAttachment,
  type ChatRuntimePrepareResponse,
} from '@/shared/api/client';
import { subscribeAuthSessionReset } from '@/shared/lib/auth-runtime';
import type { ChatUIMode } from '@/shared/contracts/chat-ui-mode';

interface StructuredStreamError {
  code?: string;
  message?: string;
  details?: unknown;
  [key: string]: unknown;
}

interface StructuredClientError extends Error {
  code?: string;
  details?: unknown;
}

export type RAGStreamError = string | Error;
export const SESSION_ACTIVE_RUN_CONFLICT_CODE = 'SESSION_ACTIVE_RUN_CONFLICT';
const SESSION_ACTIVE_RUN_CONFLICT_MESSAGE = '当前会话已有进行中的任务，请等待完成或先停止当前任务。';

export interface RAGStreamArtifact {
  object_path: string;
  name?: string;
  path?: string;
  size_bytes?: number;
  mime_type?: string;
  session_id?: string;
}

export interface RAGStreamToolTrace {
  phase?: 'tool_start' | 'tool_result' | 'tool_error';
  call_id?: string;
  name: string;
  iteration?: number;
  args?: unknown;
  result?: unknown;
  success?: boolean;
  error?: string;
  status?: string;
  duration_ms?: number;
}

export interface RAGStreamTupleToolCall {
  id?: string;
  name: string;
  args?: unknown;
}

export interface RAGStreamTupleMessage {
  type: 'ai' | 'tool';
  id: string;
  content?: string;
  tool_calls?: RAGStreamTupleToolCall[];
  tool_call_id?: string;
  name?: string;
  status?: string;
}

export interface RAGTaskState {
  task_id: string;
  title: string;
  description?: string;
  type?: 'epic' | 'task' | 'bug' | string;
  status?: 'pending' | 'ready' | 'in_progress' | 'blocked' | 'completed' | 'failed' | 'cancelled' | string;
  parent_id?: string | null;
  dependencies?: string[];
  metadata?: Record<string, unknown>;
  created_at?: string;
  updated_at?: string;
  version?: number;
  last_event_id?: number;
  [key: string]: unknown;
}

export interface RAGTaskSnapshotEvent {
  event?: 'task_snapshot';
  session_id?: string;
  event_id?: string;
  version?: number;
  timestamp?: string;
  source?: string;
  payload?: {
    tasks?: RAGTaskState[];
    [key: string]: unknown;
  };
  [key: string]: unknown;
}

export interface RAGTaskDeltaEvent {
  event?: 'task_delta';
  session_id?: string;
  event_id?: string;
  version?: number;
  timestamp?: string;
  source?: string;
  payload?: {
    operation?: 'created' | 'updated' | string;
    task_id?: string;
    task?: RAGTaskState;
    changed_fields?: Record<string, unknown>;
    [key: string]: unknown;
  };
  [key: string]: unknown;
}

export interface RAGTaskModeDecisionState {
  ui_mode: 'normal' | 'plan';
  activation_level?: 'none' | 'latent' | 'suggested' | 'required' | string;
  reasons?: string[];
  must_enter_governed_plan?: boolean;
  requires_approval?: boolean;
  suggested_system_mode?: string | null;
  query_excerpt?: string;
  complexity_score?: number;
  risk_score?: number;
  verification_score?: number;
  updated_at?: string;
  version?: number;
  last_event_id?: number;
  [key: string]: unknown;
}

export interface RAGTaskModeDecisionEvent {
  event?: 'task_mode_decision';
  session_id?: string;
  event_id?: string;
  version?: number;
  timestamp?: string;
  source?: string;
  payload?: {
    state?: RAGTaskModeDecisionState;
    [key: string]: unknown;
  };
  [key: string]: unknown;
}

interface ChatRequest {
  kb_id?: string;
  doc_ids?: string[];
  model_name?: string;
  message: string;
  image_data_urls?: string[];
  attachments?: ChatAttachment[];
  session_id: string;
  assistant_message_id?: string;
  ui_mode: ChatUIMode;
  reasoning_effort?: string;
}

interface StreamChatOptions extends ChatRequest {
  onToken: (token: string) => void;
  onThinking: (thinking: string) => void;
  onError: (error: RAGStreamError) => void;
  onDone: () => void;
  onArtifact?: (artifact: RAGStreamArtifact) => void;
  onToolTrace?: (trace: RAGStreamToolTrace) => void;
  onMessageTuple?: (messageTuple: RAGStreamTupleMessage) => void;
  onTaskSnapshot?: (snapshot: RAGTaskSnapshotEvent) => void;
  onTaskDelta?: (delta: RAGTaskDeltaEvent) => void;
  onTaskModeDecision?: (snapshot: RAGTaskModeDecisionEvent) => void;
  onResumeSnapshot?: (snapshot: ChatActiveRun) => void;
  onFinalAnswer?: (data: {
    answer: string;
    session_id: string;
  }) => void;
  signal?: AbortSignal;
}

interface PreparedRuntimeState {
  prepared: ChatRuntimePrepareResponse;
  activeRunId?: string;
}

interface InsightValuesPayload {
  title?: string | null;
  messages?: unknown[];
  artifacts?: unknown[];
  [key: string]: unknown;
}

interface RuntimeStreamMessage {
  type: 'ai' | 'tool' | 'human' | 'system' | 'unknown';
  id: string;
  content: string;
  thinking?: string;
  tool_calls?: RAGStreamTupleToolCall[];
  tool_call_id?: string;
  name?: string;
  status?: string;
}

interface RuntimeCustomTaskEvent {
  type: 'task_started' | 'task_running' | 'task_completed' | 'task_failed' | 'task_timed_out';
  task_id?: string;
  description?: string;
  message?: unknown;
  result?: unknown;
  error?: string;
  [key: string]: unknown;
}

export interface RAGActiveRunInfo {
  runId: string;
  status: string;
  startedAt?: string;
  updatedAt?: string;
}

interface StreamEventHandlers {
  sessionId: string;
  assistantMessageId?: string;
  resumeState?: {
    aiContentByMessageId?: Record<string, string>;
    aiThinkingByMessageId?: Record<string, string>;
    emittedToolCallKeys?: string[];
    toolResultContentByCallId?: Record<string, string>;
  };
  onToken: (token: string) => void;
  onThinking: (thinking: string) => void;
  onError: (error: RAGStreamError) => void;
  onDone: () => void;
  onArtifact?: (artifact: RAGStreamArtifact) => void;
  onToolTrace?: (trace: RAGStreamToolTrace) => void;
  onMessageTuple?: (messageTuple: RAGStreamTupleMessage) => void;
  onTaskSnapshot?: (snapshot: RAGTaskSnapshotEvent) => void;
  onTaskDelta?: (delta: RAGTaskDeltaEvent) => void;
  onTaskModeDecision?: (snapshot: RAGTaskModeDecisionEvent) => void;
  onResumeSnapshot?: (snapshot: ChatActiveRun) => void;
  onFinalAnswer?: (data: {
    answer: string;
    session_id: string;
  }) => void;
}

const preparedRuntimeBySession = new Map<string, PreparedRuntimeState>();
let hasAttachedAuthResetListener = false;

export const clearPreparedRuntimeCache = (): void => {
  preparedRuntimeBySession.clear();
};

const attachAuthSessionResetListener = () => {
  if (hasAttachedAuthResetListener) {
    return;
  }
  hasAttachedAuthResetListener = true;
  subscribeAuthSessionReset(() => {
    clearPreparedRuntimeCache();
  });
};

const cloneJson = <T>(value: T): T => JSON.parse(JSON.stringify(value)) as T;

attachAuthSessionResetListener();

const normalizeBaseUrl = (value: string): string => value.replace(/\/+$/u, '');

const joinUrl = (baseUrl: string, path: string): string => {
  const normalizedBase = normalizeBaseUrl(baseUrl);
  const normalizedPath = path.startsWith('/') ? path : `/${path}`;
  return `${normalizedBase}${normalizedPath}`;
};

const normalizeVirtualPath = (value: string): string => (
  String(value || '').trim().replace(/^\/+/u, '')
);

const buildInsightContent = (message: string, imageDataUrls?: string[]): string | Array<Record<string, unknown>> => {
  const normalizedImages = (imageDataUrls || [])
    .map((item) => String(item || '').trim())
    .filter((item) => Boolean(item));

  if (normalizedImages.length === 0) {
    return message;
  }

  const blocks: Array<Record<string, unknown>> = [];
  if (message.trim()) {
    blocks.push({
      type: 'text',
      text: message,
    });
  }

  normalizedImages.forEach((imageUrl) => {
    blocks.push({
      type: 'image_url',
      image_url: {
        url: imageUrl,
      },
    });
  });

  return blocks;
};

const asRecord = (value: unknown): Record<string, unknown> | null => (
  value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null
);

const extractRuntimeUploadPayload = (attachment: ChatAttachment): Record<string, unknown> | null => {
  const metadata = asRecord(attachment.metadata);
  const runtimeUpload = asRecord(metadata?.runtime_upload);
  if (!runtimeUpload) {
    return null;
  }

  const filename = typeof runtimeUpload.filename === 'string'
    ? runtimeUpload.filename.trim()
    : attachment.name.trim();
  if (!filename) {
    return null;
  }

  const sizeCandidate = runtimeUpload.size;
  const numericSize = typeof sizeCandidate === 'number'
    ? sizeCandidate
    : Number(sizeCandidate || 0);

  return {
    filename,
    size: Number.isFinite(numericSize) ? numericSize : (attachment.size_bytes || 0),
    path: typeof runtimeUpload.virtual_path === 'string' && runtimeUpload.virtual_path.trim()
      ? runtimeUpload.virtual_path.trim()
      : `/mnt/user-data/uploads/${filename}`,
  };
};

const buildRuntimeUploadKwargs = (attachments?: ChatAttachment[]): Record<string, unknown> | undefined => {
  const files = (attachments || [])
    .map(extractRuntimeUploadPayload)
    .filter((item): item is Record<string, unknown> => Boolean(item));

  if (files.length === 0) {
    return undefined;
  }

  return { files };
};

const extractRuntimeContentParts = (
  value: unknown,
): { content: string; thinking: string } => {
  if (typeof value === 'string') {
    return {
      content: value,
      thinking: '',
    };
  }

  if (Array.isArray(value)) {
    return value.reduce<{ content: string; thinking: string }>((acc, item) => {
      if (typeof item === 'string') {
        acc.content += item;
        return acc;
      }

      const record = asRecord(item);
      if (!record) {
        return acc;
      }

      const blockType = typeof record.type === 'string'
        ? record.type.trim().toLowerCase()
        : '';
      const textCandidate = typeof record.text === 'string'
        ? record.text
        : (typeof record.content === 'string' ? record.content : '');
      const thinkingCandidate = typeof record.thinking === 'string'
        ? record.thinking
        : (typeof record.reasoning === 'string' ? record.reasoning : '');

      if (
        blockType === 'thinking'
        || blockType === 'reasoning'
        || blockType === 'reasoning_text'
      ) {
        acc.thinking += thinkingCandidate || textCandidate;
        return acc;
      }

      acc.content += textCandidate;
      return acc;
    }, { content: '', thinking: '' });
  }

  const record = asRecord(value);
  if (!record) {
    return { content: '', thinking: '' };
  }

  const blockType = typeof record.type === 'string'
    ? record.type.trim().toLowerCase()
    : '';
  const content = typeof record.text === 'string'
    ? record.text
    : (typeof record.content === 'string' ? record.content : '');
  const thinking = typeof record.thinking === 'string'
    ? record.thinking
    : (typeof record.reasoning === 'string' ? record.reasoning : '');

  if (
    blockType === 'thinking'
    || blockType === 'reasoning'
    || blockType === 'reasoning_text'
  ) {
    return {
      content: '',
      thinking: thinking || content,
    };
  }

  return {
    content,
    thinking,
  };
};

const normalizeTupleToolCalls = (value: unknown): RAGStreamTupleToolCall[] | undefined => {
  if (!Array.isArray(value)) {
    return undefined;
  }

  const normalized = value
    .map((item) => {
      const record = asRecord(item);
      if (!record) {
        return null;
      }
      const name = typeof record.name === 'string' ? record.name.trim() : '';
      if (!name) {
        return null;
      }

      const toolCall: RAGStreamTupleToolCall = { name };
      if (typeof record.id === 'string' && record.id.trim()) {
        toolCall.id = record.id.trim();
      }
      if (Object.prototype.hasOwnProperty.call(record, 'args')) {
        toolCall.args = record.args;
      }
      return toolCall;
    })
    .filter((item): item is RAGStreamTupleToolCall => Boolean(item));

  return normalized.length > 0 ? normalized : undefined;
};

const extractPresentFileArtifacts = (
  toolCall: RAGStreamTupleToolCall,
  sessionId: string,
): RAGStreamArtifact[] => {
  if (toolCall.name !== 'present_files') {
    return [];
  }

  const args = asRecord(toolCall.args);
  const rawPaths = Array.isArray(args?.filepaths) ? args.filepaths : [];
  if (rawPaths.length === 0) {
    return [];
  }

  return rawPaths
    .map((item) => (typeof item === 'string' ? normalizeVirtualPath(item) : ''))
    .filter((item): item is string => Boolean(item))
    .map((item) => buildArtifactPayload(item, sessionId));
};

const normalizeRuntimeMessageType = (value: unknown): RuntimeStreamMessage['type'] => {
  const normalized = typeof value === 'string' ? value.trim().toLowerCase() : '';
  if (normalized === 'ai' || normalized === 'aimessage' || normalized === 'aimessagechunk') {
    return 'ai';
  }
  if (normalized === 'tool' || normalized === 'toolmessage' || normalized === 'toolmessagechunk') {
    return 'tool';
  }
  if (normalized === 'human' || normalized === 'humanmessage' || normalized === 'humanmessagechunk' || normalized === 'user') {
    return 'human';
  }
  if (normalized === 'system' || normalized === 'systemmessage' || normalized === 'systemmessagechunk') {
    return 'system';
  }
  return 'unknown';
};

const isRuntimeMessageEventTuple = (value: unknown): value is [unknown, Record<string, unknown>] => {
  if (!Array.isArray(value) || value.length !== 2) {
    return false;
  }
  const first = asRecord(value[0]);
  const second = asRecord(value[1]);
  if (!first || !second) {
    return false;
  }
  const firstType = typeof first.type === 'string' ? first.type.trim() : '';
  if (!firstType.toLowerCase().includes('message')) {
    return false;
  }
  return (
    Object.prototype.hasOwnProperty.call(second, 'langgraph_node')
    || Object.prototype.hasOwnProperty.call(second, 'run_id')
    || Object.prototype.hasOwnProperty.call(second, 'thread_id')
  );
};

const normalizeRuntimeStreamMessage = (value: unknown): RuntimeStreamMessage | null => {
  const record = asRecord(value);
  if (!record) {
    return null;
  }

  const type = normalizeRuntimeMessageType(record.type);
  const id = typeof record.id === 'string' ? record.id.trim() : '';
  if (!id) {
    return null;
  }

  const normalized: RuntimeStreamMessage = {
    type,
    id,
    content: '',
  };
  const extractedContent = extractRuntimeContentParts(record.content);
  normalized.content = extractedContent.content;
  if (extractedContent.thinking) {
    normalized.thinking = extractedContent.thinking;
  }

  const toolCalls = normalizeTupleToolCalls(record.tool_calls);
  if (toolCalls) {
    normalized.tool_calls = toolCalls;
  }

  if (typeof record.tool_call_id === 'string' && record.tool_call_id.trim()) {
    normalized.tool_call_id = record.tool_call_id.trim();
  }
  if (typeof record.name === 'string' && record.name.trim()) {
    normalized.name = record.name.trim();
  }
  if (typeof record.status === 'string' && record.status.trim()) {
    normalized.status = record.status.trim();
  }

  if (normalized.type === 'tool' && (!normalized.tool_call_id || !normalized.name)) {
    return null;
  }

  return normalized;
};

const normalizeRuntimeStreamMessages = (value: unknown): RuntimeStreamMessage[] => {
  if (isRuntimeMessageEventTuple(value)) {
    const single = normalizeRuntimeStreamMessage(value[0]);
    return single ? [single] : [];
  }

  if (Array.isArray(value)) {
    return value
      .map((item) => normalizeRuntimeStreamMessage(item))
      .filter((item): item is RuntimeStreamMessage => Boolean(item));
  }

  const single = normalizeRuntimeStreamMessage(value);
  return single ? [single] : [];
};

const normalizeRuntimeCustomTaskEvent = (value: unknown): RuntimeCustomTaskEvent | null => {
  const record = asRecord(value);
  if (!record) {
    return null;
  }

  const type = typeof record.type === 'string' ? record.type.trim() : '';
  if (
    type !== 'task_started'
    && type !== 'task_running'
    && type !== 'task_completed'
    && type !== 'task_failed'
    && type !== 'task_timed_out'
  ) {
    return null;
  }

  return record as RuntimeCustomTaskEvent;
};

const extractRunId = (payload: unknown): string | undefined => {
  if (!payload || typeof payload !== 'object') {
    return undefined;
  }

  const record = payload as Record<string, unknown>;
  const directCandidates = [
    record.run_id,
    record.runId,
    record.id,
  ];
  for (const candidate of directCandidates) {
    if (typeof candidate === 'string' && candidate.trim()) {
      return candidate.trim();
    }
  }

  const nestedRun = record.run;
  if (nestedRun && typeof nestedRun === 'object') {
    const nestedRecord = nestedRun as Record<string, unknown>;
    const nestedCandidates = [nestedRecord.run_id, nestedRecord.runId, nestedRecord.id];
    for (const candidate of nestedCandidates) {
      if (typeof candidate === 'string' && candidate.trim()) {
        return candidate.trim();
      }
    }
  }

  return undefined;
};

const toStructuredError = (raw: unknown, fallbackMessage: string): Error => {
  if (raw instanceof Error) {
    return raw;
  }

  if (typeof raw === 'string') {
    return new Error(raw || fallbackMessage);
  }

  if (raw && typeof raw === 'object') {
    const data = raw as StructuredStreamError;
    const error = new Error(String(data.message || fallbackMessage)) as StructuredClientError;
    if (data.code) {
      error.code = data.code;
    }
    if (Object.prototype.hasOwnProperty.call(data, 'details')) {
      error.details = data.details;
    }
    return error;
  }

  return new Error(fallbackMessage);
};

const buildSessionActiveRunConflictError = (
  activeRun: RAGActiveRunInfo,
): StructuredClientError => {
  const error = new Error(SESSION_ACTIVE_RUN_CONFLICT_MESSAGE) as StructuredClientError;
  error.code = SESSION_ACTIVE_RUN_CONFLICT_CODE;
  error.details = {
    runId: activeRun.runId,
    status: activeRun.status,
    startedAt: activeRun.startedAt,
    updatedAt: activeRun.updatedAt,
  };
  return error;
};

const buildArtifactPayload = (
  objectPath: string,
  sessionId: string,
): RAGStreamArtifact => {
  const normalizedPath = normalizeVirtualPath(objectPath);
  const filename = normalizedPath.split('/').filter(Boolean).pop() || 'artifact';
  const extension = filename.includes('.') ? filename.split('.').pop()?.toLowerCase() || '' : '';

  let mimeType: string | undefined;
  if (extension === 'md' || extension === 'markdown') {
    mimeType = 'text/markdown';
  } else if (extension === 'txt') {
    mimeType = 'text/plain';
  } else if (extension === 'json') {
    mimeType = 'application/json';
  } else if (extension === 'pdf') {
    mimeType = 'application/pdf';
  }

  return {
    object_path: normalizedPath,
    path: normalizedPath,
    name: filename,
    mime_type: mimeType,
    session_id: sessionId,
  };
};

class RAGAPIClient {
  private setPreparedRuntimeActiveRunId(sessionId: string, runId?: string): void {
    const runtimeState = preparedRuntimeBySession.get(sessionId);
    if (!runtimeState) {
      return;
    }
    runtimeState.activeRunId = runId || undefined;
  }

  private clearOwnedActiveRunId(sessionId: string, ownedRunId?: string): void {
    if (!ownedRunId) {
      return;
    }
    const runtimeState = preparedRuntimeBySession.get(sessionId);
    if (runtimeState?.activeRunId === ownedRunId) {
      this.setPreparedRuntimeActiveRunId(sessionId, undefined);
    }
  }

  rememberPreparedRuntime(sessionId: string, prepared: ChatRuntimePrepareResponse): void {
    const existing = preparedRuntimeBySession.get(sessionId);
    preparedRuntimeBySession.set(sessionId, {
      prepared,
      activeRunId: existing?.activeRunId,
    });
  }

  private async ensurePreparedRuntime(
    sessionId: string,
    request: ChatRequest,
  ): Promise<PreparedRuntimeState> {
    const prepared = await api.prepareChatRuntime(sessionId, {
      model_name: request.model_name,
      plan_mode: request.ui_mode === 'plan',
      sync_workspace_assets: true,
      persist_session_config: true,
    });

    const runtimeState: PreparedRuntimeState = {
      prepared,
      activeRunId: preparedRuntimeBySession.get(sessionId)?.activeRunId,
    };
    preparedRuntimeBySession.set(sessionId, runtimeState);
    return runtimeState;
  }

  private buildRunRequest(
    prepared: ChatRuntimePrepareResponse,
    request: ChatRequest,
  ): Record<string, unknown> {
    const runRequest = cloneJson(prepared.run_request_template || {});
    const existingContext = (
      runRequest.context && typeof runRequest.context === 'object'
        ? runRequest.context as Record<string, unknown>
        : {}
    );
    const existingInput = (
      runRequest.input && typeof runRequest.input === 'object'
        ? runRequest.input as Record<string, unknown>
        : {}
    );

    const messages = Array.isArray(existingInput.messages)
      ? [...existingInput.messages]
      : [];

    const userMessage: Record<string, unknown> = {
      role: 'user',
      content: buildInsightContent(request.message, request.image_data_urls),
    };
    const additionalKwargs = buildRuntimeUploadKwargs(request.attachments);
    if (additionalKwargs) {
      userMessage.additional_kwargs = additionalKwargs;
    }

    messages.push(userMessage);

    runRequest.context = {
      ...existingContext,
      thread_id: prepared.thread_id,
      // `prepareChatRuntime()` already resolves the runtime-usable model name.
      // Reusing the prepared value avoids sending a UI-only model alias that
      // the LangGraph runtime does not recognize.
      model_name: existingContext.model_name || request.model_name,
      is_plan_mode: request.ui_mode === 'plan',
      reasoning_effort: request.reasoning_effort,
      kb_id: request.kb_id,
      doc_ids: request.doc_ids || [],
    };
    runRequest.input = {
      ...existingInput,
      messages,
    };

    return runRequest;
  }

  private emitSyntheticResumeSnapshot(
    runtimeState: PreparedRuntimeState,
    options: {
      sessionId: string;
      assistantMessageId?: string;
      onResumeSnapshot?: (snapshot: ChatActiveRun) => void;
    },
    runId: string,
  ) {
    if (!options.onResumeSnapshot) {
      return;
    }

    const assistantMessageId = String(options.assistantMessageId || '').trim();
    if (!assistantMessageId) {
      return;
    }

    options.onResumeSnapshot({
      runId,
      sessionId: options.sessionId,
      status: 'running',
      startedAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
      assistantMessage: {
        id: assistantMessageId,
        role: 'assistant',
        content: '',
        thinking: '',
        artifacts: [],
        toolTraces: [],
        assistantTupleMessages: [],
      },
      taskSnapshotEvent: null,
      taskModeDecisionEvent: null,
    });

    runtimeState.activeRunId = runId;
  }

  private async consumeStreamResponse(
    runtimeState: PreparedRuntimeState,
    handlers: StreamEventHandlers,
    response: Response,
    streamOwnership?: { runId?: string },
  ): Promise<void> {
    const {
      onToken,
      onThinking,
      onError,
      onDone,
      onArtifact,
      onToolTrace,
      onMessageTuple,
      onTaskSnapshot,
      onTaskDelta,
      onTaskModeDecision,
      onResumeSnapshot,
      onFinalAnswer,
    } = handlers;

    if (!response.ok) {
      let errorPayload: unknown = null;
      try {
        errorPayload = await response.json();
      } catch {
        try {
          errorPayload = await response.text();
        } catch {
          errorPayload = null;
        }
      }
      throw toStructuredError(errorPayload, `HTTP ${response.status}: ${response.statusText}`);
    }

    if (!response.body) {
      throw new Error('Response body is null');
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    const seenArtifactPaths = new Set<string>();
    const emittedToolCallKeys = new Set<string>(handlers.resumeState?.emittedToolCallKeys || []);
    const lastAiContentByMessageId = new Map<string, string>(
      Object.entries(handlers.resumeState?.aiContentByMessageId || {}),
    );
    const lastAiThinkingByMessageId = new Map<string, string>(
      Object.entries(handlers.resumeState?.aiThinkingByMessageId || {}),
    );
    const lastToolResultContentByCallId = new Map<string, string>(
      Object.entries(handlers.resumeState?.toolResultContentByCallId || {}),
    );
    const taskLabelByTaskId = new Map<string, string>();
    let buffer = '';
    let eventName = 'message';
    let dataLines: string[] = [];

    const emitRuntimeMessages = (
      payload: unknown,
      options?: { fromValuesSnapshot?: boolean },
    ) => {
      const fromValuesSnapshot = options?.fromValuesSnapshot ?? false;
      const normalizedMessages = normalizeRuntimeStreamMessages(payload);
      if (normalizedMessages.length === 0) {
        return;
      }

      const scopedMessages = fromValuesSnapshot
        ? (() => {
          let lastHumanIndex = -1;
          normalizedMessages.forEach((message, index) => {
            if (message.type === 'human') {
              lastHumanIndex = index;
            }
          });
          return lastHumanIndex >= 0
            ? normalizedMessages.slice(lastHumanIndex + 1)
            : normalizedMessages;
        })()
        : normalizedMessages;

      scopedMessages.forEach((message) => {
        if (message.type === 'ai') {
          (message.tool_calls || []).forEach((toolCall, toolIndex) => {
            extractPresentFileArtifacts(toolCall, handlers.sessionId).forEach((artifact) => {
              if (seenArtifactPaths.has(artifact.object_path)) {
                return;
              }
              seenArtifactPaths.add(artifact.object_path);
              onArtifact?.(artifact);
            });

            const toolCallKey = (toolCall.id || '').trim() || `${message.id}:${toolCall.name}:${toolIndex}`;
            if (emittedToolCallKeys.has(toolCallKey)) {
              return;
            }
            emittedToolCallKeys.add(toolCallKey);
            onToolTrace?.({
              name: toolCall.name,
              call_id: (toolCall.id || '').trim() || toolCallKey,
              status: 'running',
              args: toolCall.args,
            });
            onMessageTuple?.({
              type: 'ai',
              id: message.id,
              content: '',
              tool_calls: [toolCall],
            });
          });

          const nextThinking = message.thinking || '';
          const previousThinking = lastAiThinkingByMessageId.get(message.id) || '';
          if (nextThinking !== previousThinking) {
            const thinkingDelta = nextThinking.startsWith(previousThinking)
              ? nextThinking.slice(previousThinking.length)
              : nextThinking;
            lastAiThinkingByMessageId.set(message.id, nextThinking);
            if (thinkingDelta) {
              onThinking(thinkingDelta);
            }
          }

          const nextContent = message.content || '';
          const previousContent = lastAiContentByMessageId.get(message.id) || '';
          if (nextContent === previousContent) {
            return;
          }

          const delta = nextContent.startsWith(previousContent)
            ? nextContent.slice(previousContent.length)
            : nextContent;
          lastAiContentByMessageId.set(message.id, nextContent);

          if (delta) {
            onToken(delta);
          }
          return;
        }

        if (message.type === 'tool') {
          const toolCallId = (message.tool_call_id || message.id || '').trim();
          if (!toolCallId || !message.name) {
            return;
          }

          const previousContent = lastToolResultContentByCallId.get(toolCallId) || '';
          const nextContent = message.content || '';
          if (nextContent === previousContent) {
            return;
          }

          lastToolResultContentByCallId.set(toolCallId, nextContent);
          const normalizedStatus = (message.status || '').trim().toLowerCase();
          const derivedStatus = normalizedStatus || 'completed';
          const isToolSuccess = ['success', 'completed', 'done'].includes(derivedStatus);
          onToolTrace?.({
            name: message.name,
            call_id: toolCallId,
            status: derivedStatus,
            success: isToolSuccess,
            result: nextContent,
          });
          onMessageTuple?.({
            type: 'tool',
            id: message.id,
            tool_call_id: toolCallId,
            name: message.name,
            content: nextContent,
            status: derivedStatus,
          });
        }
      });
    };

    const emitRuntimeCustomEvent = (payload: unknown) => {
      const event = normalizeRuntimeCustomTaskEvent(payload);
      if (!event) {
        return;
      }

      const taskId = String(event.task_id || '').trim();
      if (!taskId || !onToolTrace) {
        return;
      }

      const describedName = String(event.description || '').trim();
      const knownName = describedName || taskLabelByTaskId.get(taskId) || '子任务';
      taskLabelByTaskId.set(taskId, knownName);

      if (event.type === 'task_started') {
        onToolTrace({
          name: knownName,
          call_id: taskId,
          status: 'running',
          args: {
            description: describedName || knownName,
          },
        });
        return;
      }

      if (event.type === 'task_running') {
        onToolTrace({
          name: knownName,
          call_id: taskId,
          status: 'running',
          result: event.message,
        });
        return;
      }

      if (event.type === 'task_completed') {
        onToolTrace({
          name: knownName,
          call_id: taskId,
          status: 'completed',
          success: true,
          result: event.result,
        });
        return;
      }

      const normalizedError = String(event.error || '').trim() || (
        event.type === 'task_timed_out' ? '任务超时' : '任务失败'
      );
      onToolTrace({
        name: knownName,
        call_id: taskId,
        status: event.type === 'task_timed_out' ? 'timed_out' : 'failed',
        success: false,
        error: normalizedError,
      });
    };

    const emitStructuredRuntimeEvent = (payload: unknown): boolean => {
      const record = asRecord(payload);
      if (!record) {
        return false;
      }

      const eventType = typeof record.event === 'string'
        ? record.event.trim().toLowerCase()
        : '';
      if (!eventType) {
        return false;
      }

      if (eventType === 'task_snapshot') {
        onTaskSnapshot?.(record as unknown as RAGTaskSnapshotEvent);
        return true;
      }
      if (eventType === 'task_delta') {
        onTaskDelta?.(record as unknown as RAGTaskDeltaEvent);
        return true;
      }
      if (eventType === 'task_mode_decision') {
        onTaskModeDecision?.(record as unknown as RAGTaskModeDecisionEvent);
        return true;
      }
      if (eventType === 'final_answer') {
        const answer = typeof record.answer === 'string' ? record.answer : '';
        const sessionId = typeof record.session_id === 'string'
          ? record.session_id
          : handlers.sessionId;
        if (answer) {
          onFinalAnswer?.({
            answer,
            session_id: sessionId,
          });
        }
        return true;
      }

      return false;
    };

    const dispatchEvent = (rawEventName: string, rawData: string) => {
      const normalizedEventName = (rawEventName || 'message').trim();
      const payloadText = rawData.trim();
      if (!normalizedEventName && !payloadText) {
        return false;
      }

      if (!payloadText && normalizedEventName !== 'end') {
        return false;
      }

      let parsed: unknown = payloadText;
      if (payloadText) {
        try {
          parsed = JSON.parse(payloadText);
        } catch {
          parsed = payloadText;
        }
      }

      const discoveredRunId = extractRunId(parsed);
      if (discoveredRunId) {
        const previousRunId = runtimeState.activeRunId;
        if (streamOwnership && streamOwnership.runId !== discoveredRunId) {
          streamOwnership.runId = discoveredRunId;
        }
        if (previousRunId !== discoveredRunId) {
          this.setPreparedRuntimeActiveRunId(handlers.sessionId, discoveredRunId);
          this.emitSyntheticResumeSnapshot(
            runtimeState,
            {
              sessionId: handlers.sessionId,
              assistantMessageId: handlers.assistantMessageId,
              onResumeSnapshot,
            },
            discoveredRunId,
          );
        }
      }

      if (normalizedEventName === 'metadata') {
        return false;
      }

      if (normalizedEventName === 'task_snapshot') {
        if (parsed && typeof parsed === 'object') {
          onTaskSnapshot?.(parsed as RAGTaskSnapshotEvent);
        }
        return false;
      }

      if (normalizedEventName === 'task_delta') {
        if (parsed && typeof parsed === 'object') {
          onTaskDelta?.(parsed as RAGTaskDeltaEvent);
        }
        return false;
      }

      if (normalizedEventName === 'task_mode_decision') {
        if (parsed && typeof parsed === 'object') {
          onTaskModeDecision?.(parsed as RAGTaskModeDecisionEvent);
        }
        return false;
      }

      if (normalizedEventName === 'final_answer') {
        if (parsed && typeof parsed === 'object') {
          const record = parsed as Record<string, unknown>;
          const answer = typeof record.answer === 'string' ? record.answer : '';
          if (answer) {
            onFinalAnswer?.({
              answer,
              session_id: typeof record.session_id === 'string' ? record.session_id : handlers.sessionId,
            });
          }
        }
        return false;
      }

      if (normalizedEventName === 'messages') {
        emitRuntimeMessages(parsed);
        return false;
      }

      if (normalizedEventName === 'custom' || normalizedEventName.startsWith('custom|')) {
        if (emitStructuredRuntimeEvent(parsed)) {
          return false;
        }
        emitRuntimeCustomEvent(parsed);
        return false;
      }

      if (normalizedEventName === 'values') {
        if (parsed && typeof parsed === 'object') {
          const values = parsed as InsightValuesPayload;
          emitRuntimeMessages(values.messages, { fromValuesSnapshot: true });
        }
        return false;
      }

      if (normalizedEventName === 'error') {
        onError(toStructuredError(parsed, 'Insight runtime error'));
        return true;
      }

      if (normalizedEventName === 'end' || payloadText === '[DONE]') {
        onDone();
        return true;
      }

      return false;
    };

    while (true) {
      const { done, value } = await reader.read();
      if (done) {
        break;
      }

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split(/\r?\n/u);
      buffer = lines.pop() || '';

      for (const line of lines) {
        if (!line.trim()) {
          const shouldStop = dispatchEvent(eventName, dataLines.join('\n'));
          eventName = 'message';
          dataLines = [];
          if (shouldStop) {
            return;
          }
          continue;
        }

        if (line.startsWith(':')) {
          continue;
        }

        if (line.startsWith('event:')) {
          eventName = line.slice(6).trim();
          continue;
        }

        if (line.startsWith('data:')) {
          dataLines.push(line.slice(5).trimStart());
        }
      }
    }

    if (dataLines.length > 0 || eventName !== 'message') {
      const shouldStop = dispatchEvent(eventName, dataLines.join('\n'));
      if (shouldStop) {
        return;
      }
    }

    onDone();
  }

  private async fetchLiveRunByStatus(
    prepared: ChatRuntimePrepareResponse,
    status: 'running' | 'pending',
  ): Promise<RAGActiveRunInfo | null> {
    const response = await fetch(
      joinUrl(
        prepared.langgraph_base_url,
        `/threads/${prepared.thread_id}/runs?limit=1&status=${status}`,
      ),
      {
        method: 'GET',
      },
    );
    if (!response.ok) {
      let errorPayload: unknown = null;
      try {
        errorPayload = await response.json();
      } catch {
        try {
          errorPayload = await response.text();
        } catch {
          errorPayload = null;
        }
      }
      throw toStructuredError(errorPayload, `HTTP ${response.status}: ${response.statusText}`);
    }

    const payload = await response.json();
    if (!Array.isArray(payload) || payload.length === 0) {
      return null;
    }

    const run = asRecord(payload[0]);
    if (!run) {
      return null;
    }

    const runId = typeof run.run_id === 'string' ? run.run_id.trim() : '';
    if (!runId) {
      return null;
    }

    const normalizedStatus = typeof run.status === 'string' ? run.status.trim() : status;
    const activeRun: RAGActiveRunInfo = {
      runId,
      status: normalizedStatus || status,
    };
    if (typeof run.created_at === 'string' && run.created_at.trim()) {
      activeRun.startedAt = run.created_at.trim();
    }
    if (typeof run.updated_at === 'string' && run.updated_at.trim()) {
      activeRun.updatedAt = run.updated_at.trim();
    }
    return activeRun;
  }

  async getLiveRun(
    sessionId: string,
    prepared?: ChatRuntimePrepareResponse,
  ): Promise<RAGActiveRunInfo | null> {
    const knownState = preparedRuntimeBySession.get(sessionId);
    const resolvedPrepared = prepared || knownState?.prepared;
    if (!resolvedPrepared) {
      return null;
    }

    this.rememberPreparedRuntime(sessionId, resolvedPrepared);
    const runningRun = await this.fetchLiveRunByStatus(resolvedPrepared, 'running');
    if (runningRun) {
      this.setPreparedRuntimeActiveRunId(sessionId, runningRun.runId);
      return runningRun;
    }
    const pendingRun = await this.fetchLiveRunByStatus(resolvedPrepared, 'pending');
    if (pendingRun) {
      this.setPreparedRuntimeActiveRunId(sessionId, pendingRun.runId);
      return pendingRun;
    }
    this.setPreparedRuntimeActiveRunId(sessionId, undefined);
    return null;
  }

  async ensureSessionReadyForNewRun(
    sessionId: string,
    options: {
      model_name?: string;
      ui_mode: ChatUIMode;
      persist_session_config?: boolean;
    },
  ): Promise<void> {
    const prepared = await api.prepareChatRuntime(sessionId, {
      model_name: options.model_name,
      plan_mode: options.ui_mode === 'plan',
      sync_workspace_assets: false,
      sync_kb_documents: false,
      persist_session_config: options.persist_session_config ?? false,
    });
    this.rememberPreparedRuntime(sessionId, prepared);

    const activeRun = await this.getLiveRun(sessionId, prepared);
    if (!activeRun) {
      this.setPreparedRuntimeActiveRunId(sessionId, undefined);
      return;
    }

    this.setPreparedRuntimeActiveRunId(sessionId, activeRun.runId);
    throw buildSessionActiveRunConflictError(activeRun);
  }

  async streamChat(options: StreamChatOptions): Promise<void> {
    const {
      signal,
      ...request
    } = options;
    const streamOwnership: { runId?: string } = {};

    try {
      const runtimeState = await this.ensurePreparedRuntime(request.session_id, request);
      const { prepared } = runtimeState;
      const runRequest = this.buildRunRequest(prepared, request);

      const response = await fetch(joinUrl(prepared.langgraph_base_url, prepared.run_stream_path), {
        method: 'POST',
        headers: {
          Accept: 'text/event-stream',
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(runRequest),
        signal,
      });

      await this.consumeStreamResponse(runtimeState, {
        ...options,
        sessionId: request.session_id,
        assistantMessageId: request.assistant_message_id,
      }, response, streamOwnership);
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') {
        options.onDone();
        return;
      }

      if (error instanceof Error) {
        options.onError(error);
        return;
      }

      options.onError(String(error));
    } finally {
      this.clearOwnedActiveRunId(request.session_id, streamOwnership.runId);
    }
  }

  async joinActiveRun(
    sessionId: string,
    runId: string,
    options: Omit<StreamEventHandlers, 'sessionId'> & { signal?: AbortSignal },
  ): Promise<void> {
    const runtimeState = preparedRuntimeBySession.get(sessionId);
    if (!runtimeState?.prepared) {
      throw new Error('Prepared Insight runtime is missing for the current session');
    }

    runtimeState.activeRunId = runId;
    const streamMode = encodeURIComponent(JSON.stringify(['messages', 'values', 'custom']));
    const response = await fetch(
      joinUrl(
        runtimeState.prepared.langgraph_base_url,
        `/threads/${runtimeState.prepared.thread_id}/runs/${runId}/stream?stream_mode=${streamMode}&cancel_on_disconnect=false`,
      ),
      {
        method: 'GET',
        headers: {
          Accept: 'text/event-stream',
        },
        signal: options.signal,
      },
    );

    try {
      await this.consumeStreamResponse(runtimeState, {
        ...options,
        sessionId,
      }, response, { runId });
    } finally {
      this.clearOwnedActiveRunId(sessionId, runId);
    }
  }

  async chat(request: ChatRequest): Promise<never> {
    void request;
    throw new Error('Non-streaming chat endpoint is not available. Use streamChat() instead.');
  }

  async cancelRun(
    sessionId: string,
    runId?: string,
  ): Promise<{ success: boolean; message?: string; error?: string }> {
    try {
      const runtimeState = preparedRuntimeBySession.get(sessionId);
      const prepared = runtimeState?.prepared;
      const activeRunId = String(runId || runtimeState?.activeRunId || '').trim();

      if (!prepared || !activeRunId) {
        return {
          success: false,
          error: 'No active Insight run found for the current session',
        };
      }

      const response = await fetch(
        joinUrl(
          prepared.langgraph_base_url,
          `/threads/${prepared.thread_id}/runs/${activeRunId}/cancel?action=interrupt&wait=0`,
        ),
        {
          method: 'POST',
        },
      );

      if (!response.ok) {
        if (response.status === 404) {
          this.clearOwnedActiveRunId(sessionId, activeRunId);
        }
        let errorPayload: unknown = null;
        try {
          errorPayload = await response.json();
        } catch {
          try {
            errorPayload = await response.text();
          } catch {
            errorPayload = null;
          }
        }
        throw toStructuredError(errorPayload, `HTTP ${response.status}: ${response.statusText}`);
      }

      this.clearOwnedActiveRunId(sessionId, activeRunId);
      return { success: true };
    } catch (error) {
      console.error('Failed to cancel stream:', error);
      return { success: false, error: String(error) };
    }
  }

  async cancelStream(sessionId: string): Promise<{ success: boolean; message?: string; error?: string }> {
    return this.cancelRun(sessionId);
  }
}

export const ragAPI = new RAGAPIClient();
