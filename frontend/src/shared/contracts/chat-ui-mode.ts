export type ChatUIMode = 'normal' | 'plan';

export const CHAT_UI_MODE_LABELS: Record<ChatUIMode, string> = {
  normal: '通用',
  plan: '规划',
};

export const assertChatUIMode = (value: unknown, fieldName: string = 'uiMode'): ChatUIMode => {
  const normalized = typeof value === 'string' ? value.trim().toLowerCase() : '';
  if (normalized === 'normal' || normalized === 'plan') {
    return normalized;
  }
  throw new Error(`${fieldName} 缺失或非法，必须是 normal 或 plan`);
};
