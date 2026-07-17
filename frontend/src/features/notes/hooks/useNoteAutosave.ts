import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import {
  areNoteDraftsEqual,
  canSaveWithKeepalive,
  createNoteDraft,
  shouldReplayActiveDraft,
  type NoteDraft,
} from '@/features/notes/lib/noteAutosave';

type NoteSaveState = 'saved' | 'pending' | 'saving' | 'error';

interface NoteSaveResult {
  updatedAt?: string;
}

interface QueuedNoteSave {
  draft: NoteDraft;
  promise: Promise<boolean>;
}

interface UseNoteAutosaveOptions {
  enabled: boolean;
  selectedNote: { id: string; title: string; content: string } | null;
  title: string;
  content: string;
  delayMs?: number;
  saveNote: (
    draft: NoteDraft,
    options: { keepalive: boolean },
  ) => Promise<NoteSaveResult>;
  onSaved: (draft: NoteDraft, updatedAt: string) => void;
  onError: (error: unknown) => void;
}

interface UseNoteAutosaveResult {
  saveState: NoteSaveState;
  flushPendingSave: () => Promise<boolean>;
  forgetNote: (noteId: string) => void;
}

export const useNoteAutosave = ({
  enabled,
  selectedNote,
  title,
  content,
  delayMs = 1000,
  saveNote,
  onSaved,
  onError,
}: UseNoteAutosaveOptions): UseNoteAutosaveResult => {
  const [saveStates, setSaveStates] = useState<Record<string, NoteSaveState>>({});
  const mountedRef = useRef(true);
  const timerRef = useRef<number | null>(null);
  const activeDraftRef = useRef<NoteDraft | null>(null);
  const previousSelectedNoteIdRef = useRef<string | null>(null);
  const persistedDraftsRef = useRef(new Map<string, NoteDraft>());
  const queuesRef = useRef(new Map<string, QueuedNoteSave[]>());
  const tailsRef = useRef(new Map<string, Promise<boolean>>());
  const enqueueSaveRef = useRef<(draft: NoteDraft) => Promise<boolean>>(
    async () => false,
  );
  const flushRef = useRef<() => Promise<boolean>>(async () => true);
  const saveNoteRef = useRef(saveNote);
  const onSavedRef = useRef(onSaved);
  const onErrorRef = useRef(onError);

  saveNoteRef.current = saveNote;
  onSavedRef.current = onSaved;
  onErrorRef.current = onError;

  const selectedNoteId = selectedNote?.id || null;
  const currentDraft = useMemo(() => (
    selectedNoteId ? createNoteDraft(selectedNoteId, title, content) : null
  ), [content, selectedNoteId, title]);
  activeDraftRef.current = currentDraft;

  useEffect(() => {
    if (selectedNote && previousSelectedNoteIdRef.current !== selectedNoteId) {
      persistedDraftsRef.current.set(
        selectedNote.id,
        createNoteDraft(selectedNote.id, selectedNote.title, selectedNote.content),
      );
    }
    previousSelectedNoteIdRef.current = selectedNoteId;
  }, [selectedNote, selectedNoteId]);

  const updateSaveState = useCallback((noteId: string, state: NoteSaveState) => {
    if (!mountedRef.current) {
      return;
    }
    setSaveStates((previous) => {
      if (previous[noteId] === state) {
        return previous;
      }
      return { ...previous, [noteId]: state };
    });
  }, []);

  const enqueueSave = useCallback((draft: NoteDraft): Promise<boolean> => {
    const persistedDraft = persistedDraftsRef.current.get(draft.noteId);
    if (areNoteDraftsEqual(draft, persistedDraft)) {
      updateSaveState(draft.noteId, 'saved');
      return Promise.resolve(true);
    }

    const existingQueue = queuesRef.current.get(draft.noteId) || [];
    const duplicate = existingQueue.find((entry) => areNoteDraftsEqual(entry.draft, draft));
    if (duplicate) {
      return duplicate.promise;
    }

    const previousTail = tailsRef.current.get(draft.noteId) || Promise.resolve(true);
    let didSave = false;
    const operation = previousTail
      .catch(() => false)
      .then(async () => {
        updateSaveState(draft.noteId, 'saving');
        try {
          const result = await saveNoteRef.current(draft, {
            keepalive: canSaveWithKeepalive(draft),
          });
          const updatedAt = result?.updatedAt || new Date().toISOString();
          persistedDraftsRef.current.set(draft.noteId, draft);
          didSave = true;
          if (mountedRef.current) {
            onSavedRef.current(draft, updatedAt);
          }
          return true;
        } catch (error: unknown) {
          if (mountedRef.current) {
            onErrorRef.current(error);
          }
          return false;
        }
      })
      .finally(() => {
        const queue = queuesRef.current.get(draft.noteId) || [];
        const remainingQueue = queue.filter((entry) => entry.promise !== operation);
        if (remainingQueue.length > 0) {
          queuesRef.current.set(draft.noteId, remainingQueue);
        } else {
          queuesRef.current.delete(draft.noteId);
        }
        if (tailsRef.current.get(draft.noteId) === operation) {
          tailsRef.current.delete(draft.noteId);
        }

        const activeDraft = activeDraftRef.current;
        const savedDraft = persistedDraftsRef.current.get(draft.noteId);
        if (remainingQueue.length > 0) {
          updateSaveState(draft.noteId, 'pending');
        } else if (activeDraft && shouldReplayActiveDraft({
          activeDraft,
          persistedDraft: savedDraft,
          completedNoteId: draft.noteId,
          hasQueuedSave: false,
          saveSucceeded: didSave,
        })) {
          updateSaveState(draft.noteId, 'pending');
          void enqueueSaveRef.current(activeDraft);
        } else if (
          activeDraft?.noteId === draft.noteId
          && !areNoteDraftsEqual(activeDraft, savedDraft)
        ) {
          updateSaveState(draft.noteId, didSave ? 'pending' : 'error');
        } else {
          updateSaveState(draft.noteId, 'saved');
        }
      });

    const queuedSave = { draft, promise: operation };
    queuesRef.current.set(draft.noteId, [...existingQueue, queuedSave]);
    tailsRef.current.set(draft.noteId, operation);
    return operation;
  }, [updateSaveState]);
  enqueueSaveRef.current = enqueueSave;

  const clearTimer = useCallback(() => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const flushPendingSave = useCallback(async (): Promise<boolean> => {
    clearTimer();
    if (!enabled) {
      return true;
    }

    const draft = activeDraftRef.current;
    if (!draft) {
      return true;
    }

    const persistedDraft = persistedDraftsRef.current.get(draft.noteId);
    if (areNoteDraftsEqual(draft, persistedDraft)) {
      const inFlightSave = tailsRef.current.get(draft.noteId);
      if (!inFlightSave) {
        return true;
      }

      await inFlightSave;
      if (areNoteDraftsEqual(
        draft,
        persistedDraftsRef.current.get(draft.noteId),
      )) {
        return true;
      }
    }

    updateSaveState(draft.noteId, 'pending');
    return enqueueSave(draft);
  }, [clearTimer, enabled, enqueueSave, updateSaveState]);
  flushRef.current = flushPendingSave;

  const forgetNote = useCallback((noteId: string) => {
    clearTimer();
    persistedDraftsRef.current.delete(noteId);
    queuesRef.current.delete(noteId);
    tailsRef.current.delete(noteId);
    if (mountedRef.current) {
      setSaveStates((previous) => {
        const next = { ...previous };
        delete next[noteId];
        return next;
      });
    }
  }, [clearTimer]);

  useEffect(() => {
    clearTimer();
    if (!enabled || !currentDraft) {
      return;
    }

    const persistedDraft = persistedDraftsRef.current.get(currentDraft.noteId);
    if (areNoteDraftsEqual(currentDraft, persistedDraft)) {
      updateSaveState(
        currentDraft.noteId,
        tailsRef.current.has(currentDraft.noteId) ? 'pending' : 'saved',
      );
      return;
    }

    updateSaveState(currentDraft.noteId, 'pending');
    timerRef.current = window.setTimeout(() => {
      timerRef.current = null;
      void enqueueSave(currentDraft);
    }, delayMs);

    return clearTimer;
  }, [
    clearTimer,
    currentDraft,
    delayMs,
    enabled,
    enqueueSave,
    updateSaveState,
  ]);

  useEffect(() => {
    mountedRef.current = true;
    const hasPendingSave = () => {
      const activeDraft = activeDraftRef.current;
      if (!activeDraft) {
        return tailsRef.current.size > 0;
      }
      return (
        tailsRef.current.size > 0
        || !areNoteDraftsEqual(
          activeDraft,
          persistedDraftsRef.current.get(activeDraft.noteId),
        )
      );
    };
    const handlePageHide = () => {
      void flushRef.current();
    };
    const handleBeforeUnload = (event: BeforeUnloadEvent) => {
      if (!hasPendingSave()) {
        return;
      }
      void flushRef.current();
      event.preventDefault();
      event.returnValue = '';
    };

    window.addEventListener('pagehide', handlePageHide);
    window.addEventListener('beforeunload', handleBeforeUnload);
    return () => {
      window.removeEventListener('pagehide', handlePageHide);
      window.removeEventListener('beforeunload', handleBeforeUnload);
      clearTimer();
      mountedRef.current = false;
      void flushRef.current();
    };
  }, [clearTimer]);

  return {
    saveState: selectedNoteId ? (saveStates[selectedNoteId] || 'saved') : 'saved',
    flushPendingSave,
    forgetNote,
  };
};
