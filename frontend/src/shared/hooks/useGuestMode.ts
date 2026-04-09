import { useMemo, useSyncExternalStore } from 'react';

import {
  disableGuestMode,
  enableGuestMode,
  getGuestModeState,
  getGuestUsedMessageCount,
  markGuestMessageUsed,
  requestGuestLoginPrompt,
  subscribeGuestModeState,
} from '@/shared/lib/guest-mode';

const GUEST_MESSAGE_LIMIT = 3;

export function useGuestMode() {
  const state = useSyncExternalStore(subscribeGuestModeState, getGuestModeState, getGuestModeState);

  return useMemo(() => ({
    isGuestMode: state.enabled,
    guestMessageCount: getGuestUsedMessageCount(),
    hasUsedGuestMessage: state.usedMessageCount > 0,
    hasReachedGuestMessageLimit: state.usedMessageCount >= GUEST_MESSAGE_LIMIT,
    startGuestMode: enableGuestMode,
    stopGuestMode: disableGuestMode,
    consumeGuestMessage: markGuestMessageUsed,
    promptLogin: requestGuestLoginPrompt,
  }), [state.enabled, state.usedMessageCount]);
}
