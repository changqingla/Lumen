import type { ChatUIMode } from '@/shared/contracts/chat-ui-mode';

export type KnowledgeDocumentStatus =
  | 'ready'
  | 'processing'
  | 'uploading'
  | 'chunking'
  | 'embedding'
  | 'failed'
  | 'error'
  | 'completed'
  | 'processed'
  | string;

export interface KnowledgeBaseSummary {
  id: string;
  name: string;
  avatar?: string;
  contents?: number;
  description?: string;
  category?: string;
  isOwner?: boolean;
  isPublic?: boolean;
  isSubscribed?: boolean;
  subscribersCount?: number;
}

export type KnowledgeBaseDetail = KnowledgeBaseSummary;

export interface KnowledgeDocument {
  id: string;
  name: string;
  status: KnowledgeDocumentStatus;
  uploadedAt?: string;
  errorMessage?: string | null;
}

export interface KnowledgeChatSessionConfig {
  uiMode: ChatUIMode;
  sourceType?: string;
  kbIds?: string[];
  modelName?: string;
}

export interface KnowledgeChatSession {
  id: string;
  title: string;
  lastMessage: string;
  timestamp: string;
  createdAt: string;
  config?: KnowledgeChatSessionConfig;
}

export interface SharedOrgStatus {
  id: string;
}

export interface SharedStatusResponse {
  shared_to_orgs?: SharedOrgStatus[];
}

export interface QuotaExceededLikeErrorDetails {
  user_level?: string;
  used_tokens?: number;
  quota_limit?: number;
  reset_date?: string;
}

export interface QuotaExceededLikeError extends Error {
  code?: string;
  details?: QuotaExceededLikeErrorDetails;
}

export const PROCESSING_DOCUMENT_STATUSES: KnowledgeDocumentStatus[] = [
  'processing',
  'uploading',
  'chunking',
  'embedding',
];
