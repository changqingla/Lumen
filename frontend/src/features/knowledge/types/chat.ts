export interface KnowledgeQuotaExceededModalState {
  isOpen: boolean;
  userLevel: string;
  usedTokens: number;
  quotaLimit: number;
  resetDate: string;
}
