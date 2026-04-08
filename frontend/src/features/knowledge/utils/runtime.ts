const KB_SUBSCRIPTION_CHANGED_KEY = 'kb_subscription_changed';

const getKnowledgeSessionStorageKey = (kbId: string): string => `kb_session_${kbId}`;

export const saveKnowledgeSessionId = (kbId: string, sessionId: string): void => {
  try {
    localStorage.setItem(getKnowledgeSessionStorageKey(kbId), sessionId);
  } catch (error) {
    console.error('Failed to save session ID to localStorage:', error);
  }
};

export const readKnowledgeSessionId = (kbId: string): string | null => {
  try {
    return localStorage.getItem(getKnowledgeSessionStorageKey(kbId));
  } catch (error) {
    console.error('Failed to read session ID from localStorage:', error);
    return null;
  }
};

export const clearKnowledgeSessionId = (kbId: string): void => {
  try {
    localStorage.removeItem(getKnowledgeSessionStorageKey(kbId));
  } catch (error) {
    console.error('Failed to remove session ID from localStorage:', error);
  }
};

export const notifyKnowledgeSubscriptionChanged = (): void => {
  localStorage.setItem(KB_SUBSCRIPTION_CHANGED_KEY, Date.now().toString());
  window.dispatchEvent(new Event(KB_SUBSCRIPTION_CHANGED_KEY));
};

export const isKnowledgePdfFile = (filename: string): boolean =>
  filename.toLowerCase().endsWith('.pdf');
