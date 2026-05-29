import { useCallback, useEffect, useState } from 'react';

import { api, type ChatModelOption } from '@/shared/api/client';
import { readAuthToken, subscribeAuthSessionReset } from '@/shared/lib/auth-runtime';
import { isGuestModeEnabled } from '@/shared/lib/guest-mode';

interface ChatModelsResponse {
  default_model: string;
  models: ChatModelOption[];
}

interface CachedChatModelsResponse {
  authToken: string;
  response: ChatModelsResponse;
}

let cachedResponse: CachedChatModelsResponse | null = null;
let inflightRequest: Promise<ChatModelsResponse> | null = null;
let inflightAuthToken: string | null = null;

const CHAT_MODELS_UPDATED_EVENT = 'lumen:chat-models-updated';
let hasAttachedAuthResetListener = false;

const getChatModelsAuthToken = () => readAuthToken();

const getCachedResponseForCurrentUser = (): ChatModelsResponse | null => {
  const authToken = getChatModelsAuthToken();
  if (cachedResponse?.authToken === authToken) {
    return cachedResponse.response;
  }
  return null;
};

const filterModelsByVisionRequirement = (
  models: ChatModelOption[],
  requireVision: boolean,
): ChatModelOption[] => (
  requireVision
    ? models.filter((item) => item.supports_vision)
    : models
);

const loadChatModels = async (): Promise<ChatModelsResponse> => {
  if (isGuestModeEnabled() || !getChatModelsAuthToken()) {
    return {
      default_model: '',
      models: [],
    };
  }

  const authToken = getChatModelsAuthToken();
  const currentCachedResponse = getCachedResponseForCurrentUser();
  if (currentCachedResponse) {
    return currentCachedResponse;
  }

  if (!inflightRequest || inflightAuthToken !== authToken) {
    inflightAuthToken = authToken;
    inflightRequest = api.listChatModels()
      .then((response) => {
        cachedResponse = {
          authToken,
          response,
        };
        if (inflightAuthToken === authToken) {
          inflightRequest = null;
          inflightAuthToken = null;
        }
        return response;
      })
      .catch((error) => {
        if (inflightAuthToken === authToken) {
          inflightRequest = null;
          inflightAuthToken = null;
        }
        throw error;
      });
  }
  return inflightRequest;
};

const invalidateChatModelsCache = () => {
  cachedResponse = null;
  inflightRequest = null;
  inflightAuthToken = null;
};

const attachAuthSessionResetListener = () => {
  if (hasAttachedAuthResetListener) {
    return;
  }
  hasAttachedAuthResetListener = true;
  subscribeAuthSessionReset(() => {
    invalidateChatModelsCache();
  });
};

export const notifyChatModelsUpdated = () => {
  invalidateChatModelsCache();
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new Event(CHAT_MODELS_UPDATED_EVENT));
  }
};

attachAuthSessionResetListener();

export const resolvePreferredModelName = (
  models: ChatModelOption[],
  preferredModelName?: string,
  options?: {
    defaultModelName?: string;
    requireVision?: boolean;
  },
): string | undefined => {
  const requireVision = options?.requireVision === true;
  const candidates = filterModelsByVisionRequirement(models, requireVision);
  if (candidates.length === 0) {
    return undefined;
  }

  const normalizedPreferred = (preferredModelName || '').trim();
  if (normalizedPreferred && candidates.some((item) => item.name === normalizedPreferred)) {
    return normalizedPreferred;
  }

  const normalizedDefault = (options?.defaultModelName || '').trim();
  if (normalizedDefault && candidates.some((item) => item.name === normalizedDefault)) {
    return normalizedDefault;
  }

  return candidates[0]?.name;
};

export function useChatModels() {
  const initialResponse = getCachedResponseForCurrentUser();
  const [models, setModels] = useState<ChatModelOption[]>(initialResponse?.models || []);
  const [defaultModelName, setDefaultModelName] = useState<string | undefined>(initialResponse?.default_model);
  const [isLoading, setIsLoading] = useState(!initialResponse);
  const [error, setError] = useState<Error | null>(null);

  const syncModels = useCallback(async () => {
    setIsLoading(true);
    try {
      const response = await loadChatModels();
      setModels(response.models || []);
      setDefaultModelName(response.default_model || response.models?.[0]?.name);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err : new Error('加载模型列表失败'));
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;

    const load = async () => {
      try {
        const response = await loadChatModels();
        if (cancelled) {
          return;
        }
        setModels(response.models || []);
        setDefaultModelName(response.default_model || response.models?.[0]?.name);
        setError(null);
      } catch (err) {
        if (cancelled) {
          return;
        }
        setError(err instanceof Error ? err : new Error('加载模型列表失败'));
      } finally {
        if (!cancelled) {
          setIsLoading(false);
        }
      }
    };

    void load();

    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const handleModelsUpdated = () => {
      void syncModels();
    };

    window.addEventListener(CHAT_MODELS_UPDATED_EVENT, handleModelsUpdated);
    return () => {
      window.removeEventListener(CHAT_MODELS_UPDATED_EVENT, handleModelsUpdated);
    };
  }, [syncModels]);

  return {
    models,
    defaultModelName,
    isLoading,
    error,
    refreshModels: syncModels,
  };
}

export { filterModelsByVisionRequirement };
