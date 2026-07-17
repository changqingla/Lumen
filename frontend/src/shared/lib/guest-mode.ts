const GUEST_MODE_STORAGE_KEY = 'lumen_guest_mode';
const GUEST_MODE_EVENT = 'lumen:guest-mode-change';
const GUEST_LOGIN_PROMPT_EVENT = 'lumen:guest-login-prompt';

export interface GuestModeState {
  enabled: boolean;
  usedMessageCount: number;
  guestToken: string;
}

export interface GuestLoginPromptDetail {
  title?: string;
  message?: string;
  confirmText?: string;
}

const DEFAULT_GUEST_MODE_STATE: GuestModeState = {
  enabled: false,
  usedMessageCount: 0,
  guestToken: '',
};

let cachedGuestModeState: GuestModeState = DEFAULT_GUEST_MODE_STATE;

const areGuestModeStatesEqual = (left: GuestModeState, right: GuestModeState) => (
  left.enabled === right.enabled
  && left.usedMessageCount === right.usedMessageCount
  && left.guestToken === right.guestToken
);

const readStoredGuestModeState = (): GuestModeState => {
  if (typeof window === 'undefined') {
    return DEFAULT_GUEST_MODE_STATE;
  }

  try {
    const raw = window.localStorage.getItem(GUEST_MODE_STORAGE_KEY);
    if (!raw) {
      return DEFAULT_GUEST_MODE_STATE;
    }

    const parsed = JSON.parse(raw) as Partial<GuestModeState> & { hasUsedMessage?: boolean };
    const parsedCount = typeof parsed.usedMessageCount === 'number'
      ? parsed.usedMessageCount
      : (parsed.hasUsedMessage ? 1 : 0);
    const guestToken = typeof parsed.guestToken === 'string' ? parsed.guestToken.trim() : '';
    return {
      // Legacy client-generated guest IDs are intentionally not migrated into
      // credentials. The user must obtain a server-signed guest session.
      enabled: Boolean(parsed.enabled && guestToken),
      usedMessageCount: guestToken && Number.isFinite(parsedCount) ? Math.max(0, Math.floor(parsedCount)) : 0,
      guestToken,
    };
  } catch {
    return DEFAULT_GUEST_MODE_STATE;
  }
};

const syncCachedGuestModeState = (): GuestModeState => {
  const nextState = readStoredGuestModeState();
  if (areGuestModeStatesEqual(cachedGuestModeState, nextState)) {
    return cachedGuestModeState;
  }
  cachedGuestModeState = nextState;
  return cachedGuestModeState;
};

const writeGuestModeState = (state: GuestModeState) => {
  if (typeof window === 'undefined') {
    return;
  }

  window.localStorage.setItem(GUEST_MODE_STORAGE_KEY, JSON.stringify(state));
  cachedGuestModeState = state;
  window.dispatchEvent(new Event(GUEST_MODE_EVENT));
};

export const getGuestModeState = (): GuestModeState => syncCachedGuestModeState();

export const isGuestModeEnabled = (): boolean => syncCachedGuestModeState().enabled;

export const getGuestUsedMessageCount = (): number => syncCachedGuestModeState().usedMessageCount;

export const getGuestModeGuestToken = (): string => syncCachedGuestModeState().guestToken;

export const enableGuestMode = (guestToken: string) => {
  const normalizedToken = guestToken.trim();
  if (!normalizedToken) {
    throw new Error('Guest session token is required');
  }
  const current = syncCachedGuestModeState();
  writeGuestModeState({
    enabled: true,
    usedMessageCount: current.guestToken === normalizedToken ? current.usedMessageCount : 0,
    guestToken: normalizedToken,
  });
};

export const disableGuestMode = () => {
  if (typeof window === 'undefined') {
    return;
  }

  const current = syncCachedGuestModeState();
  writeGuestModeState({
    enabled: false,
    usedMessageCount: current.usedMessageCount,
    guestToken: current.guestToken,
  });
};

export const markGuestMessageUsed = () => {
  const current = syncCachedGuestModeState();
  if (!current.enabled || !current.guestToken) {
    return;
  }
  writeGuestModeState({
    ...current,
    usedMessageCount: current.usedMessageCount + 1,
  });
};

export const subscribeGuestModeState = (listener: () => void) => {
  if (typeof window === 'undefined') {
    return () => {};
  }

  const handleStorage = (event: StorageEvent) => {
    if (event.key !== GUEST_MODE_STORAGE_KEY) {
      return;
    }
    syncCachedGuestModeState();
    listener();
  };

  const handleGuestModeChange = () => {
    syncCachedGuestModeState();
    listener();
  };

  window.addEventListener(GUEST_MODE_EVENT, handleGuestModeChange);
  window.addEventListener('storage', handleStorage);
  return () => {
    window.removeEventListener(GUEST_MODE_EVENT, handleGuestModeChange);
    window.removeEventListener('storage', handleStorage);
  };
};

export const requestGuestLoginPrompt = (detail?: GuestLoginPromptDetail) => {
  if (typeof window === 'undefined') {
    return;
  }

  window.dispatchEvent(new CustomEvent<GuestLoginPromptDetail>(GUEST_LOGIN_PROMPT_EVENT, {
    detail,
  }));
};

export const subscribeGuestLoginPrompt = (
  listener: (detail: GuestLoginPromptDetail) => void,
) => {
  if (typeof window === 'undefined') {
    return () => {};
  }

  const handler = (event: Event) => {
    const customEvent = event as CustomEvent<GuestLoginPromptDetail>;
    listener(customEvent.detail || {});
  };

  window.addEventListener(GUEST_LOGIN_PROMPT_EVENT, handler);
  return () => {
    window.removeEventListener(GUEST_LOGIN_PROMPT_EVENT, handler);
  };
};
