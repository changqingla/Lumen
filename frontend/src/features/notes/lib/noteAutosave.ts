export interface NoteDraft {
  noteId: string;
  title: string;
  content: string;
}

export const NOTE_SAVE_KEEPALIVE_BYTE_LIMIT = 60 * 1024;

export const createNoteDraft = (
  noteId: string,
  title: string,
  content: string,
): NoteDraft => ({ noteId, title, content });

export const areNoteDraftsEqual = (
  left: NoteDraft | null | undefined,
  right: NoteDraft | null | undefined,
): boolean => Boolean(
  left
  && right
  && left.noteId === right.noteId
  && left.title === right.title
  && left.content === right.content,
);

export const canSaveWithKeepalive = (
  draft: NoteDraft,
  byteLimit = NOTE_SAVE_KEEPALIVE_BYTE_LIMIT,
): boolean => {
  const body = JSON.stringify({ title: draft.title, content: draft.content });
  return new TextEncoder().encode(body).byteLength <= byteLimit;
};

export const shouldReplayActiveDraft = ({
  activeDraft,
  persistedDraft,
  completedNoteId,
  hasQueuedSave,
  saveSucceeded,
}: {
  activeDraft: NoteDraft | null;
  persistedDraft: NoteDraft | undefined;
  completedNoteId: string;
  hasQueuedSave: boolean;
  saveSucceeded: boolean;
}): boolean => Boolean(
  saveSucceeded
  && !hasQueuedSave
  && activeDraft?.noteId === completedNoteId
  && !areNoteDraftsEqual(activeDraft, persistedDraft)
);

export const mergeSavedDraft = <T extends {
  id: string;
  title: string;
  content: string;
  updatedAt: string;
}>(
  note: T,
  draft: NoteDraft,
  updatedAt: string,
): T => {
  if (note.id !== draft.noteId) {
    return note;
  }

  return {
    ...note,
    title: draft.title,
    content: draft.content,
    updatedAt,
  };
};
