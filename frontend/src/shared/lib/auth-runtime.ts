export const AUTH_SESSION_RESET_EVENT = 'lumen:auth-session-reset';

let hasAttachedStorageListener = false;

const emitAuthSessionReset = () => {
  if (typeof window === 'undefined') {
    return;
  }
  window.dispatchEvent(new Event(AUTH_SESSION_RESET_EVENT));
};

const handleStorageChange = (event: StorageEvent) => {
  if (event.key !== 'auth_token') {
    return;
  }
  emitAuthSessionReset();
};

export const ensureAuthRuntimeListeners = () => {
  if (typeof window === 'undefined' || hasAttachedStorageListener) {
    return;
  }
  window.addEventListener('storage', handleStorageChange);
  hasAttachedStorageListener = true;
};

export const dispatchAuthSessionReset = () => {
  ensureAuthRuntimeListeners();
  emitAuthSessionReset();
};

export const subscribeAuthSessionReset = (listener: () => void) => {
  if (typeof window === 'undefined') {
    return () => {};
  }
  ensureAuthRuntimeListeners();
  window.addEventListener(AUTH_SESSION_RESET_EVENT, listener);
  return () => {
    window.removeEventListener(AUTH_SESSION_RESET_EVENT, listener);
  };
};

export const readAuthToken = () => (
  typeof window === 'undefined' ? '' : (localStorage.getItem('auth_token') || '')
);

ensureAuthRuntimeListeners();
