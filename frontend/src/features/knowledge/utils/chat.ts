import type { KnowledgeChatSession, QuotaExceededLikeError } from '@/features/knowledge/types/detail';
import type { KnowledgeQuotaExceededModalState } from '@/features/knowledge/types/chat';

export const defaultKnowledgeQuotaExceededModalState = (): KnowledgeQuotaExceededModalState => ({
  isOpen: false,
  userLevel: 'basic',
  usedTokens: 0,
  quotaLimit: 0,
  resetDate: '',
});

export const isKnowledgeChatSessionForKb = (
  session: KnowledgeChatSession | undefined,
  kbId: string,
): boolean =>
  Boolean(
    session
    && session.config?.sourceType === 'knowledge'
    && session.config.kbIds?.includes(kbId),
  );

export const resolveKnowledgeQuotaExceededModalState = (
  error: string | Error,
): KnowledgeQuotaExceededModalState | null => {
  if (typeof error === 'object' && (error as QuotaExceededLikeError).code === 'QUOTA_EXCEEDED') {
    const details = (error as QuotaExceededLikeError).details || {};
    return {
      isOpen: true,
      userLevel: details.user_level || 'basic',
      usedTokens: details.used_tokens || 0,
      quotaLimit: details.quota_limit || 0,
      resetDate: details.reset_date || '',
    };
  }

  const errorString = String(error);
  if (!errorString.includes('QUOTA_EXCEEDED') && !errorString.includes('配额')) {
    return null;
  }

  try {
    const match = errorString.match(/\{.*\}/);
    if (match) {
      const details = JSON.parse(match[0]) as Record<string, unknown>;
      return {
        isOpen: true,
        userLevel: String(details.user_level || 'basic'),
        usedTokens: Number(details.used_tokens || 0),
        quotaLimit: Number(details.quota_limit || 0),
        resetDate: String(details.reset_date || ''),
      };
    }
  } catch {
    // Ignore parse errors and fall back to default quota modal content.
  }

  return {
    isOpen: true,
    userLevel: 'basic',
    usedTokens: 0,
    quotaLimit: 0,
    resetDate: '',
  };
};
