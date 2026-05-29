import type { ChatTaskModeDecisionEvent, ChatTaskSnapshotEvent } from '@/shared/api/client';
import type { ChatUIMode } from '@/shared/contracts/chat-ui-mode';
import type {
  RAGTaskDeltaEvent,
  RAGTaskModeDecisionEvent,
  RAGTaskModeDecisionState,
  RAGTaskSnapshotEvent,
  RAGTaskState,
} from '@/features/chat/api/rag';

type TaskStatus = 'pending' | 'in_progress' | 'blocked' | 'completed' | 'cancelled';

interface TaskSnapshotTask {
  taskId: string;
  title: string;
  description?: string;
  type: 'epic' | 'task' | 'bug' | string;
  status: TaskStatus;
  parentId?: string | null;
  dependencies: string[];
  metadata?: Record<string, unknown>;
  createdAt?: string;
  updatedAt?: string;
  version: number;
  lastEventId: number;
}

export interface TaskSnapshotState {
  tasks: TaskSnapshotTask[];
  version: number;
  eventId?: string;
  updatedAt?: string;
  source?: string;
  lastChangedTaskId?: string;
  lastOperation?: 'created' | 'updated' | string;
}

type TaskActivationLevel = 'none' | 'latent' | 'suggested' | 'required';

export interface TaskModeDecisionState {
  uiMode: ChatUIMode;
  activationLevel: TaskActivationLevel;
  reasons: string[];
  mustEnterGovernedPlan: boolean;
  requiresApproval: boolean;
  suggestedSystemMode?: string | null;
  queryExcerpt?: string;
  complexityScore?: number;
  riskScore?: number;
  verificationScore?: number;
  version: number;
  eventId?: string;
  updatedAt?: string;
  source?: string;
}

const toNonNegativeInteger = (value: unknown): number | undefined => {
  if (typeof value === 'number' && Number.isFinite(value) && value >= 0) {
    return Math.floor(value);
  }
  if (typeof value === 'string') {
    const parsed = Number(value);
    if (Number.isFinite(parsed) && parsed >= 0) {
      return Math.floor(parsed);
    }
  }
  return undefined;
};

const normalizeTaskStatus = (rawStatus: unknown): TaskStatus | null => {
  const status = typeof rawStatus === 'string' ? rawStatus.trim().toLowerCase() : '';
  if (
    status === 'pending'
    || status === 'in_progress'
    || status === 'blocked'
    || status === 'completed'
    || status === 'cancelled'
  ) {
    return status;
  }
  return null;
};

const normalizeStringList = (value: unknown): string[] => {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .map((item) => (typeof item === 'string' ? item.trim() : ''))
    .filter((item): item is string => Boolean(item));
};

const normalizeChatUIMode = (rawMode: unknown): ChatUIMode => (
  rawMode === 'plan' ? 'plan' : 'normal'
);

const normalizeActivationLevel = (rawLevel: unknown): TaskActivationLevel => {
  const level = typeof rawLevel === 'string' ? rawLevel.trim().toLowerCase() : '';
  if (
    level === 'none'
    || level === 'latent'
    || level === 'suggested'
    || level === 'required'
  ) {
    return level;
  }
  return 'none';
};

const normalizeTaskState = (
  raw: RAGTaskState | Record<string, unknown> | null | undefined,
): TaskSnapshotTask | null => {
  if (!raw || typeof raw !== 'object') {
    return null;
  }

  const taskId = typeof raw.task_id === 'string' ? raw.task_id.trim() : '';
  const title = typeof raw.title === 'string' ? raw.title.trim() : '';
  const status = normalizeTaskStatus(raw.status);
  if (!taskId || !title || !status) {
    return null;
  }

  const dependencies = Array.isArray(raw.dependencies)
    ? raw.dependencies
      .map((item) => (typeof item === 'string' ? item.trim() : ''))
      .filter((item): item is string => Boolean(item))
    : [];
  const parentId = typeof raw.parent_id === 'string'
    ? (raw.parent_id.trim() || null)
    : (raw.parent_id === null ? null : undefined);
  const type = typeof raw.type === 'string' && raw.type.trim()
    ? raw.type.trim().toLowerCase()
    : 'task';
  const normalized: TaskSnapshotTask = {
    taskId,
    title,
    type,
    status,
    dependencies,
    version: toNonNegativeInteger(raw.version) ?? 0,
    lastEventId: toNonNegativeInteger(raw.last_event_id) ?? 0,
  };

  if (typeof raw.description === 'string' && raw.description.trim()) {
    normalized.description = raw.description.trim();
  }
  if (parentId !== undefined) {
    normalized.parentId = parentId;
  }
  if (raw.metadata && typeof raw.metadata === 'object' && !Array.isArray(raw.metadata)) {
    normalized.metadata = raw.metadata as Record<string, unknown>;
  }
  if (typeof raw.created_at === 'string' && raw.created_at.trim()) {
    normalized.createdAt = raw.created_at.trim();
  }
  if (typeof raw.updated_at === 'string' && raw.updated_at.trim()) {
    normalized.updatedAt = raw.updated_at.trim();
  }

  return normalized;
};

const normalizeTaskStateList = (rawTasks: unknown): TaskSnapshotTask[] => {
  if (!Array.isArray(rawTasks)) {
    return [];
  }
  return rawTasks
    .map((task) => normalizeTaskState(task as RAGTaskState))
    .filter((task): task is TaskSnapshotTask => Boolean(task));
};

const normalizeTaskModeDecisionState = (
  raw: RAGTaskModeDecisionState | null | undefined,
): TaskModeDecisionState | null => {
  if (!raw || typeof raw !== 'object') {
    return null;
  }

  const normalized: TaskModeDecisionState = {
    uiMode: normalizeChatUIMode(raw.ui_mode),
    activationLevel: normalizeActivationLevel(raw.activation_level),
    reasons: normalizeStringList(raw.reasons),
    mustEnterGovernedPlan: Boolean(raw.must_enter_governed_plan),
    requiresApproval: Boolean(raw.requires_approval),
    version: toNonNegativeInteger(raw.version) ?? 0,
  };

  if (typeof raw.suggested_system_mode === 'string') {
    normalized.suggestedSystemMode = raw.suggested_system_mode.trim() || null;
  } else if (raw.suggested_system_mode === null) {
    normalized.suggestedSystemMode = null;
  }
  if (typeof raw.query_excerpt === 'string' && raw.query_excerpt.trim()) {
    normalized.queryExcerpt = raw.query_excerpt.trim();
  }
  if (typeof raw.updated_at === 'string' && raw.updated_at.trim()) {
    normalized.updatedAt = raw.updated_at.trim();
  }
  const complexityScore = toNonNegativeInteger(raw.complexity_score);
  if (complexityScore !== undefined) {
    normalized.complexityScore = complexityScore;
  }
  const riskScore = toNonNegativeInteger(raw.risk_score);
  if (riskScore !== undefined) {
    normalized.riskScore = riskScore;
  }
  const verificationScore = toNonNegativeInteger(raw.verification_score);
  if (verificationScore !== undefined) {
    normalized.verificationScore = verificationScore;
  }

  return normalized;
};

export const applyTaskSnapshotEvent = (
  prev: TaskSnapshotState | null,
  event: RAGTaskSnapshotEvent,
): TaskSnapshotState | null => {
  const payload = event.payload;
  if (!payload || typeof payload !== 'object') {
    return prev;
  }

  const tasks = normalizeTaskStateList(payload.tasks);
  if (tasks.length === 0) {
    return {
      tasks: [],
      version: typeof event.version === 'number' ? event.version : (prev?.version || 0),
      eventId: typeof event.event_id === 'string' ? event.event_id : prev?.eventId,
      updatedAt: typeof event.timestamp === 'string' ? event.timestamp : prev?.updatedAt,
      source: typeof event.source === 'string' ? event.source : prev?.source,
    };
  }

  return {
    tasks,
    version: typeof event.version === 'number'
      ? event.version
      : Math.max(...tasks.map((task) => task.version || 0), prev?.version || 0),
    eventId: typeof event.event_id === 'string' ? event.event_id : prev?.eventId,
    updatedAt: typeof event.timestamp === 'string' ? event.timestamp : prev?.updatedAt,
    source: typeof event.source === 'string' ? event.source : prev?.source,
    lastChangedTaskId: prev?.lastChangedTaskId,
    lastOperation: prev?.lastOperation,
  };
};

export const applyTaskDeltaEvent = (
  prev: TaskSnapshotState | null,
  event: RAGTaskDeltaEvent,
): TaskSnapshotState | null => {
  const payload = event.payload;
  if (!payload || typeof payload !== 'object') {
    return prev;
  }

  const normalizedTask = normalizeTaskState(payload.task);
  if (!normalizedTask) {
    return prev;
  }

  const previousTasks = prev?.tasks || [];
  const existingIndex = previousTasks.findIndex((task) => task.taskId === normalizedTask.taskId);
  const nextTasks = [...previousTasks];
  if (existingIndex >= 0) {
    nextTasks[existingIndex] = normalizedTask;
  } else {
    nextTasks.push(normalizedTask);
  }

  nextTasks.sort((left, right) => {
    const leftTime = left.createdAt || '';
    const rightTime = right.createdAt || '';
    if (leftTime !== rightTime) {
      return leftTime.localeCompare(rightTime);
    }
    return left.taskId.localeCompare(right.taskId);
  });

  return {
    tasks: nextTasks,
    version: typeof event.version === 'number'
      ? event.version
      : Math.max(normalizedTask.version || 0, prev?.version || 0),
    eventId: typeof event.event_id === 'string' ? event.event_id : prev?.eventId,
    updatedAt: typeof event.timestamp === 'string' ? event.timestamp : (normalizedTask.updatedAt || prev?.updatedAt),
    source: typeof event.source === 'string' ? event.source : prev?.source,
    lastChangedTaskId: normalizedTask.taskId,
    lastOperation: typeof payload.operation === 'string' ? payload.operation : prev?.lastOperation,
  };
};

export const applyTaskModeDecisionEvent = (
  prev: TaskModeDecisionState | null,
  event: RAGTaskModeDecisionEvent,
): TaskModeDecisionState | null => {
  const payload = event.payload;
  if (!payload || typeof payload !== 'object') {
    return prev;
  }

  const nextState = normalizeTaskModeDecisionState(payload.state);
  if (!nextState) {
    return prev;
  }

  return {
    ...nextState,
    version: typeof event.version === 'number' ? event.version : (nextState.version || prev?.version || 0),
    eventId: typeof event.event_id === 'string' ? event.event_id : prev?.eventId,
    updatedAt: typeof event.timestamp === 'string' ? event.timestamp : (nextState.updatedAt || prev?.updatedAt),
    source: typeof event.source === 'string' ? event.source : prev?.source,
  };
};

export const buildTaskSnapshotStateFromHistory = (
  rawEvent: ChatTaskSnapshotEvent | null | undefined,
): TaskSnapshotState | null => {
  if (!rawEvent || typeof rawEvent !== 'object') {
    return null;
  }
  return applyTaskSnapshotEvent(null, rawEvent as unknown as RAGTaskSnapshotEvent);
};

export const buildTaskModeDecisionStateFromHistory = (
  rawEvent: ChatTaskModeDecisionEvent | null | undefined,
): TaskModeDecisionState | null => {
  if (!rawEvent || typeof rawEvent !== 'object') {
    return null;
  }
  return applyTaskModeDecisionEvent(null, rawEvent as unknown as RAGTaskModeDecisionEvent);
};
