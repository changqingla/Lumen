import assert from 'node:assert/strict';
import test from 'node:test';

import {
  buildResumeStreamState,
  finalizeDanglingAssistantTools,
  hasPendingAttachmentsInMessages,
  mergeLoadedAndLiveMessages,
  normalizeAssistantTupleMessage,
  type RuntimeChatMessage,
} from '../src/features/chat/lib/runtime-message.ts';

const assistantMessage = (
  overrides: Partial<RuntimeChatMessage> = {},
): RuntimeChatMessage => ({
  id: 'assistant-1',
  role: 'assistant',
  content: '',
  ...overrides,
});

test('tuple normalization preserves markdown whitespace and rejects incomplete tool results', () => {
  assert.deepEqual(
    normalizeAssistantTupleMessage({
      type: 'ai',
      id: ' whitespace ',
      content: '\n\n',
    }),
    {
      type: 'ai',
      id: 'whitespace',
      content: '\n\n',
    },
  );

  assert.equal(normalizeAssistantTupleMessage({
    type: 'tool',
    id: 'tool-1',
    tool_call_id: 'call-1',
    content: 'result without a tool name',
  }), null);
});

test('history and live messages deduplicate even when transient tuple ids differ', () => {
  const loaded = assistantMessage({
    id: 'persisted-id',
    content: 'same answer',
    assistantTupleMessages: [{
      type: 'ai',
      id: 'persisted-tuple-id',
      content: 'same answer',
    }],
  });
  const live = assistantMessage({
    id: 'temporary-id',
    content: 'same answer',
    assistantTupleMessages: [{
      type: 'ai',
      id: 'live-tuple-id',
      content: 'same answer',
    }],
  });

  assert.deepEqual(mergeLoadedAndLiveMessages([loaded], [live]), [loaded]);
});

test('same-id reconciliation keeps the richer version of a streamed message', () => {
  const loaded = assistantMessage({ id: 'shared-id', content: 'short' });
  const live = assistantMessage({
    id: 'shared-id',
    content: 'short answer with the final detail',
    artifacts: [{ object_path: 'reports/final.md' }],
  });

  assert.deepEqual(mergeLoadedAndLiveMessages([loaded], [live]), [live]);
});

test('interruption finalization closes only dangling tool calls', () => {
  const message = assistantMessage({
    assistantTupleMessages: [
      {
        type: 'ai',
        id: 'ai-pending',
        tool_calls: [{ id: 'call-pending', name: 'search', args: { query: 'Lumen' } }],
      },
      {
        type: 'ai',
        id: 'ai-complete',
        tool_calls: [{ id: 'call-complete', name: 'read_file' }],
      },
      {
        type: 'tool',
        id: 'tool-complete',
        tool_call_id: 'call-complete',
        name: 'read_file',
        content: JSON.stringify({ result: 'done', success: true, status: 'success' }),
        status: 'success',
      },
    ],
  });

  finalizeDanglingAssistantTools(message, 'transport closed');

  const pendingResult = message.assistantTupleMessages?.find(
    (item) => item.type === 'tool' && item.tool_call_id === 'call-pending',
  );
  const completedResult = message.assistantTupleMessages?.find(
    (item) => item.type === 'tool' && item.tool_call_id === 'call-complete',
  );
  const pendingTrace = message.toolTraces?.find((trace) => trace.call_id === 'call-pending');
  const completedTrace = message.toolTraces?.find((trace) => trace.call_id === 'call-complete');

  assert.equal(pendingResult?.status, 'interrupted');
  assert.deepEqual(JSON.parse(pendingResult?.content || '{}'), {
    error: 'transport closed',
    success: false,
    status: 'interrupted',
  });
  assert.equal(completedResult?.status, 'success');
  assert.equal(pendingTrace?.status, 'interrupted');
  assert.equal(pendingTrace?.success, false);
  assert.equal(completedTrace?.status, 'success');
  assert.equal(completedTrace?.success, true);
});

test('resume state records emitted content, thinking, tool calls, and tool results', () => {
  const state = buildResumeStreamState(assistantMessage({
    id: 'assistant-resume',
    content: 'partial answer',
    thinking: 'partial reasoning',
    assistantTupleMessages: [
      {
        type: 'ai',
        id: 'tool-call',
        tool_calls: [{ id: 'call-1', name: 'search' }],
      },
      {
        type: 'tool',
        id: 'tool-result',
        tool_call_id: 'call-1',
        name: 'search',
        content: 'partial result',
      },
    ],
  }));

  assert.deepEqual(state, {
    aiContentByMessageId: { 'assistant-resume': 'partial answer' },
    aiThinkingByMessageId: { 'assistant-resume': 'partial reasoning' },
    emittedToolCallKeys: ['call-1'],
    toolResultContentByCallId: { 'call-1': 'partial result' },
  });
});

test('attachment polling includes pending knowledge-base projections', () => {
  const pendingProjection = assistantMessage({
    attachments: [{
      attachment_id: 'attachment-1',
      name: 'paper.pdf',
      object_path: 'uploads/paper.pdf',
      workspace_path: '/mnt/user-data/uploads/paper.pdf',
      parse_status: 'ready',
      metadata: {
        kb_projection: {
          status: ' PENDING ',
        },
      },
    }],
  });
  const ready = assistantMessage({
    attachments: [{
      attachment_id: 'attachment-2',
      name: 'notes.md',
      object_path: 'uploads/notes.md',
      workspace_path: '/mnt/user-data/uploads/notes.md',
      parse_status: 'ready',
    }],
  });

  assert.equal(hasPendingAttachmentsInMessages([ready]), false);
  assert.equal(hasPendingAttachmentsInMessages([ready, pendingProjection]), true);
});
