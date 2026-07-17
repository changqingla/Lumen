import assert from 'node:assert/strict';
import test from 'node:test';

import { buildWholeKnowledgeBaseSessionConfig } from '../src/features/knowledge/utils/chat.ts';
import {
  areNoteDraftsEqual,
  canSaveWithKeepalive,
  createNoteDraft,
  mergeSavedDraft,
  shouldReplayActiveDraft,
} from '../src/features/notes/lib/noteAutosave.ts';

test('whole knowledge-base config delegates full ready-document resolution to the backend', () => {
  const config = buildWholeKnowledgeBaseSessionConfig('kb-1', 'normal');

  assert.deepEqual(config.kbIds, ['kb-1']);
  assert.deepEqual(config.docIds, []);
  assert.equal(config.sourceType, 'knowledge');
  assert.equal(config.isKBLocked, true);
});

test('draft comparison includes note identity and exact editor contents', () => {
  const draft = createNoteDraft('note-1', '标题', '正文');

  assert.equal(areNoteDraftsEqual(draft, { ...draft }), true);
  assert.equal(areNoteDraftsEqual(draft, { ...draft, noteId: 'note-2' }), false);
  assert.equal(areNoteDraftsEqual(draft, { ...draft, content: '新正文' }), false);
});

test('a completed save only updates the matching note', () => {
  const note = {
    id: 'note-2',
    title: '原标题',
    content: '原正文',
    updatedAt: '2026-01-01T00:00:00Z',
  };
  const draft = createNoteDraft('note-1', '新标题', '新正文');

  assert.equal(mergeSavedDraft(note, draft, '2026-01-02T00:00:00Z'), note);
  assert.deepEqual(
    mergeSavedDraft({ ...note, id: 'note-1' }, draft, '2026-01-02T00:00:00Z'),
    {
      id: 'note-1',
      title: '新标题',
      content: '新正文',
      updatedAt: '2026-01-02T00:00:00Z',
    },
  );
});

test('keepalive eligibility is based on UTF-8 request bytes', () => {
  const draft = createNoteDraft('note-1', '标题', '正文');

  assert.equal(canSaveWithKeepalive(draft, 100), true);
  assert.equal(canSaveWithKeepalive(draft, 10), false);
});

test('an in-flight save replays an undo back to the prior persisted draft', () => {
  const original = createNoteDraft('note-1', 'Original', 'P');
  const completed = createNoteDraft('note-1', 'Changed', 'A');

  assert.equal(shouldReplayActiveDraft({
    activeDraft: original,
    persistedDraft: completed,
    completedNoteId: 'note-1',
    hasQueuedSave: false,
    saveSucceeded: true,
  }), true);
  assert.equal(shouldReplayActiveDraft({
    activeDraft: original,
    persistedDraft: completed,
    completedNoteId: 'note-1',
    hasQueuedSave: true,
    saveSucceeded: true,
  }), false);
});
