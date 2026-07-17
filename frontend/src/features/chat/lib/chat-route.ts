export interface ChatSessionIdentity {
  id: string;
}

export const normalizeChatRouteId = (chatId: string | null | undefined): string => (
  chatId?.trim() || ''
);

export const buildLegacyChatRedirect = (chatId: string | null | undefined): string => {
  const normalizedChatId = normalizeChatRouteId(chatId);
  if (!normalizedChatId) {
    return '/';
  }

  const search = new URLSearchParams({ chatId: normalizedChatId });
  return `/?${search.toString()}`;
};

export async function resolveChatSessionForRoute<T extends ChatSessionIdentity>(
  chatId: string | null | undefined,
  loadedSessions: readonly T[],
  loadSession: (normalizedChatId: string) => Promise<T>,
): Promise<T | null> {
  const normalizedChatId = normalizeChatRouteId(chatId);
  if (!normalizedChatId) {
    return null;
  }

  const loadedSession = loadedSessions.find((session) => session.id === normalizedChatId);
  if (loadedSession) {
    return loadedSession;
  }

  return loadSession(normalizedChatId);
}
