// API 客户端工具
import type { ChatUIMode } from '@/shared/contracts/chat-ui-mode';
import { dispatchAuthSessionReset } from '@/shared/lib/auth-runtime';
import { getGuestModeGuestId, isGuestModeEnabled } from '@/shared/lib/guest-mode';
import { safeLocalStorageRemove } from '@/shared/utils/localStorage';

// API 基础配置
// 开发环境使用相对路径，通过 Vite 代理
// 生产环境使用环境变量配置的完整 URL
export const API_BASE_URL = import.meta.env.VITE_API_URL || '/api';

const attachGuestHeaders = (headers: Headers, token: string | null) => {
  if (token || !isGuestModeEnabled() || headers.has('X-Guest-Id')) {
    return;
  }

  const guestId = getGuestModeGuestId();
  if (guestId) {
    headers.set('X-Guest-Id', guestId);
  }
};

// 通用请求函数
async function request<T>(
  endpoint: string,
  options: RequestInit = {}
): Promise<T> {
  const token = localStorage.getItem('auth_token');

  const headers = new Headers(options.headers || undefined);
  if (!(options.body instanceof FormData) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }

  // 添加 Authorization header（如果有 token）
  if (token && !headers.has('Authorization')) {
    headers.set('Authorization', `Bearer ${token}`);
  }
  attachGuestHeaders(headers, token);

  const response = await fetch(`${API_BASE_URL}${endpoint}`, {
    ...options,
    headers,
  });

  const responseText = await response.text();
  const contentType = response.headers.get('content-type') || '';
  const looksLikeJson = (
    contentType.toLowerCase().includes('application/json')
    || /^[[{]/u.test(responseText.trim())
  );
  const data = (() => {
    if (!responseText.trim()) {
      return {};
    }
    if (looksLikeJson) {
      try {
        return JSON.parse(responseText);
      } catch {
        return responseText;
      }
    }
    return responseText;
  })();
  
  if (!response.ok) {
    // 处理错误响应
    // 1. 处理 FastAPI Pydantic 验证错误 (422)
    if (response.status === 422 && typeof data === 'object' && data !== null && Array.isArray((data as { detail?: unknown }).detail)) {
      const detail = (data as { detail: Array<{ loc: Array<string | number>; msg: string }> }).detail;
      const firstError = detail[0];
      const field = firstError.loc[firstError.loc.length - 1];
      const msg = firstError.msg;
      throw new Error(`参数错误 (${field}): ${msg}`);
    }

    if (typeof data === 'string') {
      throw new Error(data || `请求失败 (${response.status})`);
    }

    // 2. 处理标准 API 错误格式 { detail: { error: { code, message } } }
    const error = data.detail?.error || data.error || data;
    const errorMessage = error.message || (typeof error === 'string' ? error : '请求失败');
    
    // 根据错误码提供更友好的提示
    if (error.code === 'UNAUTHORIZED') {
      // 检查是否是token过期（已登录状态下的401错误）
      const isTokenExpired = token && errorMessage.toLowerCase().includes('token');
      
      if (isTokenExpired) {
        // ✅ Token过期，安全地清除本地存储并跳转登录页
        safeLocalStorageRemove('auth_token');
        safeLocalStorageRemove('auth_user');
        safeLocalStorageRemove('userProfile');
        dispatchAuthSessionReset();
        
        // 延迟跳转，先让错误提示显示
        setTimeout(() => {
          window.location.href = '/auth';
        }, 1500);
        
        throw new Error('当前登录已过期，请重新登录');
      } else {
        // 登录时的认证错误
        throw new Error(errorMessage || '账号或密码不正确');
      }
    } else if (error.code === 'NOT_FOUND') {
      throw new Error(errorMessage || '账号未注册');
    } else if (error.code === 'CONFLICT') {
      throw new Error(errorMessage || '该邮箱已被注册');
    } else if (error.code === 'VALIDATION_ERROR') {
      throw new Error(errorMessage);
    } else {
      throw new Error(errorMessage);
    }
  }
  
  return data as T;
}

async function requestBlob(
  endpoint: string,
  options: RequestInit = {}
): Promise<{ blob: Blob; fileName?: string | null }> {
  const token = localStorage.getItem('auth_token');
  const headers = new Headers(options.headers || undefined);
  if (token && !headers.has('Authorization')) {
    headers.set('Authorization', `Bearer ${token}`);
  }
  attachGuestHeaders(headers, token);

  const response = await fetch(`${API_BASE_URL}${endpoint}`, {
    ...options,
    headers,
  });

  if (!response.ok) {
    const responseText = await response.text();
    let errorMessage = responseText || `请求失败 (${response.status})`;
    try {
      const parsed = JSON.parse(responseText);
      const error = parsed?.detail?.error || parsed?.error || parsed;
      errorMessage = error?.message || parsed?.detail || errorMessage;
    } catch {
      // Ignore JSON parse failure and keep plain text error.
    }
    throw new Error(errorMessage);
  }

  const contentDisposition = response.headers.get('content-disposition') || '';
  const utf8Match = contentDisposition.match(/filename\\*=UTF-8''([^;]+)/i);
  const plainMatch = contentDisposition.match(/filename="?([^";]+)"?/i);
  const fileName = utf8Match?.[1]
    ? decodeURIComponent(utf8Match[1])
    : (plainMatch?.[1] || null);

  return {
    blob: await response.blob(),
    fileName,
  };
}

export interface ChatArtifact {
  object_path: string;
  name?: string;
  path?: string;
  size_bytes?: number;
  mime_type?: string;
  session_id?: string;
}

export interface ChatAttachment {
  attachment_id: string;
  name: string;
  object_path: string;
  workspace_path: string;
  mime_type?: string;
  source_kind?: string;
  role?: string;
  input_mode?: string;
  size_bytes?: number;
  sha256?: string;
  created_at?: string;
  parent_attachment_id?: string;
  view_type?: string;
  available_views?: string[];
  capabilities?: string[];
  parse_status?: 'none' | 'pending' | 'ready' | 'partial' | 'failed';
  metadata?: Record<string, unknown>;
}

export const serializeChatAttachments = (attachments?: ChatAttachment[]) => (
  (attachments || []).map((attachment) => ({
    attachment_id: attachment.attachment_id,
    name: attachment.name,
    object_path: attachment.object_path,
    workspace_path: attachment.workspace_path,
    mime_type: attachment.mime_type,
    source_kind: attachment.source_kind,
    role: attachment.role,
    input_mode: attachment.input_mode,
    size_bytes: attachment.size_bytes,
    sha256: attachment.sha256,
    parent_attachment_id: attachment.parent_attachment_id,
    view_type: attachment.view_type,
    available_views: attachment.available_views,
    capabilities: attachment.capabilities,
    parse_status: attachment.parse_status,
    metadata: attachment.metadata,
  }))
);

export interface ChatToolTrace {
  name: string;
  call_id?: string;
  iteration?: number;
  args?: unknown;
  result?: unknown;
  success?: boolean;
  error?: string;
  status?: string;
  duration_ms?: number;
}

export interface ChatInterruption {
  reason: string;
  interruptedAt?: string;
  retryable?: boolean;
}

export interface ChatModelOption {
  name: string;
  display_name: string;
  description?: string | null;
  supports_vision?: boolean;
  supports_thinking?: boolean;
  supports_reasoning_effort?: boolean;
  provider_code?: string;
  provider_display_name?: string;
  provider_icon_key?: string;
  source?: 'system' | 'user';
}

export interface ModelProviderCatalogModel {
  name: string;
  display_name: string;
  description?: string | null;
  supports_vision?: boolean;
  supports_thinking?: boolean;
  supports_reasoning_effort?: boolean;
  provider_code: string;
  provider_display_name: string;
  provider_icon_key: string;
}

export interface ModelProviderCatalogItem {
  code: string;
  display_name: string;
  description: string;
  icon_key: string;
  api_key_label: string;
  base_url: string;
  credential_configured: boolean;
  api_key_masked?: string | null;
  models: ModelProviderCatalogModel[];
}

export interface UserConfiguredModel extends ChatModelOption {
  id: string;
  provider_code: string;
  provider_display_name: string;
  provider_icon_key: string;
  provider_model_name: string;
  is_enabled: boolean;
  health_status?: 'unknown' | 'healthy' | 'unhealthy' | string;
  last_health_checked_at?: string | null;
  last_health_latency_ms?: number | null;
  last_health_error?: string | null;
  source: 'user';
}

export interface ModelConfigCatalogResponse {
  providers: ModelProviderCatalogItem[];
  user_models: UserConfiguredModel[];
}

export interface ProviderRemoteModelsResponse {
  provider_code: string;
  provider_display_name: string;
  provider_icon_key: string;
  base_url: string;
  models: ModelProviderCatalogModel[];
}
export interface ChatAssistantTupleToolCall {
  id?: string;
  name: string;
  args?: unknown;
}
export interface ChatAssistantTupleMessage {
  type: 'ai' | 'tool';
  id: string;
  content?: string;
  tool_calls?: ChatAssistantTupleToolCall[];
  tool_call_id?: string;
  name?: string;
  status?: string;
}

export interface ChatHistoryMessage {
  id: string;
  role: string;
  content: string;
  thinking?: string;
  createdAt?: string;
  imageDataUrls?: string[];
  attachments?: ChatAttachment[];
  artifacts?: ChatArtifact[];
  toolTraces?: ChatToolTrace[];
  assistantTupleMessages?: ChatAssistantTupleMessage[];
  wasTruncated?: boolean;
  truncatedAt?: string;
  interruption?: ChatInterruption | null;
  documentSummaries?: Array<{
    doc_id: string;
    doc_name: string;
    summary: string;
    from_cache: boolean;
  }>;
}

export interface ChatActiveRun {
  runId: string;
  sessionId: string;
  status: string;
  startedAt: string;
  updatedAt: string;
  assistantMessage: ChatHistoryMessage;
  taskSnapshotEvent?: ChatTaskSnapshotEvent | null;
  taskModeDecisionEvent?: ChatTaskModeDecisionEvent | null;
}

export interface ChatTaskSnapshotEvent {
  session_id?: string;
  event_id?: string;
  version?: number;
  timestamp?: string;
  source?: string;
  payload?: {
    tasks?: Record<string, unknown>[];
    [key: string]: unknown;
  };
  [key: string]: unknown;
}
export interface ChatTaskModeDecisionEvent {
  session_id?: string;
  event_id?: string;
  version?: number;
  timestamp?: string;
  source?: string;
  payload?: {
    state?: Record<string, unknown>;
    [key: string]: unknown;
  };
  [key: string]: unknown;
}

export interface ChatRuntimePrepareRequest {
  model_name?: string;
  plan_mode?: boolean;
  sync_workspace_assets?: boolean;
  sync_kb_documents?: boolean;
  persist_session_config?: boolean;
}

export const CHAT_RUNTIME_NAME = 'lumen' as const;

export interface ChatRuntimePrepareResponse {
  session_id: string;
  thread_id: string;
  runtime: typeof CHAT_RUNTIME_NAME;
  assistant_id: string;
  gateway_base_url: string;
  langgraph_base_url: string;
  run_stream_path: string;
  uploads_path: string;
  uploads_list_path: string;
  artifacts_base_path: string;
  suggestions_path: string;
  run_request_template: Record<string, unknown>;
  session_config: Record<string, unknown>;
  workspace_summary: string;
  workspace_assets: Record<string, unknown>[];
  materialized_files: Record<string, unknown>[];
  kb_materialized_files: Record<string, unknown>[];
}

export interface ChatRuntimeThreadUploadFile {
  filename: string;
  size: number;
  path: string;
  virtual_path: string;
  artifact_url: string;
  extension?: string | null;
  modified?: number | null;
  markdown_file?: string | null;
  markdown_path?: string | null;
  markdown_virtual_path?: string | null;
  markdown_artifact_url?: string | null;
}

export interface ChatRuntimeThreadUploadsResponse {
  session_id: string;
  thread_id: string;
  runtime: typeof CHAT_RUNTIME_NAME;
  uploads: ChatRuntimeThreadUploadFile[];
  count: number;
  workspace_assets: ChatAttachment[];
  materialized_files: Record<string, unknown>[];
  kb_materialized_files: Record<string, unknown>[];
}

export interface ChatRuntimeThreadUploadMutationResponse {
  session_id: string;
  thread_id: string;
  runtime: typeof CHAT_RUNTIME_NAME;
  success: boolean;
  files: ChatRuntimeThreadUploadFile[];
  count: number;
  message: string;
}

export interface ChatRuntimeThreadUploadDeleteResponse {
  session_id: string;
  thread_id: string;
  runtime: typeof CHAT_RUNTIME_NAME;
  success: boolean;
  message: string;
}

export interface ChatSessionConfigEditableFields {
  kbIds?: string[];
  docIds?: string[];
  sourceType?: 'home' | 'knowledge' | 'favorites';
  isKBLocked?: boolean;
  modelName?: string;
}

export interface ChatSessionRuntimeMeta {
  runtime?: string;
  threadId?: string;
  assistantId?: string;
  deepThinking?: boolean;
}

export interface ChatSessionConfig extends ChatSessionConfigEditableFields, ChatSessionRuntimeMeta {
  uiMode: ChatUIMode;
}

export type ChatSessionConfigUpdate = Partial<ChatSessionConfigEditableFields> & {
  uiMode: ChatUIMode;
};

export interface PaginatedResponse<T> {
  total: number;
  page: number;
  pageSize: number;
  items: T[];
}

export interface OrganizationListItem {
  id: string;
  name: string;
  description: string;
  avatar: string | null;
  org_code: string;
  role: 'owner' | 'member';
  member_count: number;
  created_at: string;
  is_owner?: boolean;
  owner_id?: string;
}

export interface OrganizationMember {
  id: string;
  user_id: string;
  user_name: string;
  user_email: string;
  user_avatar: string | null;
  role: 'owner' | 'member' | string;
  joined_at: string;
}

export interface OrganizationDetailResponse extends OrganizationListItem {
  members: OrganizationMember[];
}

export interface OrganizationListResponse {
  created: OrganizationListItem[];
  joined: OrganizationListItem[];
}

export interface KnowledgeBaseListItem {
  id: string;
  name: string;
  description?: string;
  category?: string;
  avatar?: string;
  contents?: number;
  organization_name?: string;
  is_admin_recommended?: boolean;
  from_organization?: boolean;
  visibility?: 'private' | 'organization' | 'public';
  isOwner?: boolean;
  is_owner?: boolean;
  isSubscribed?: boolean;
  is_subscribed?: boolean;
  subscribersCount?: number;
  subscribers_count?: number;
  viewCount?: number;
  view_count?: number;
  creator_name?: string;
  creator_avatar?: string | null;
  ownerId?: string;
  owner_id?: string;
  createdAt?: string;
  updatedAt?: string;
  created_at?: string;
  updated_at?: string;
}

export interface AuthenticatedUser {
  id: string;
  name: string;
  email: string;
  avatar: string | null;
  user_level?: string;
  is_admin?: boolean;
  membership_expires_at?: string | null;
  is_member?: boolean;
  is_advanced_member?: boolean;
  member_expires_at?: string | null;
  [key: string]: unknown;
}

export interface KnowledgeDocumentListItem {
  id: string;
  name: string;
  status?: string;
  size?: number;
  kbId?: string;
  kbName?: string;
  created_at?: string;
  uploadedAt?: string;
  errorMessage?: string | null;
  error_message?: string | null;
  chunkCount?: number;
}

export interface FavoriteDocumentListItem extends KnowledgeDocumentListItem {
  kbId: string;
  kbName?: string;
}

export interface KnowledgeVisibilityResponse {
  success?: boolean;
  visibility?: 'private' | 'organization' | 'public';
  shared_to_orgs?: Array<{
    id: string;
    name: string;
    avatar: string | null;
  }>;
}

export interface NoteListItem {
  id: string;
  title: string;
  content: string;
  folderId?: string;
  tags: string[];
  updatedAt: string;
  createdAt: string;
}

export interface ChatSessionSummary {
  id: string;
  title: string;
  lastMessage: string;
  timestamp: string;
  createdAt: string;
  updatedAt: string;
  messageCount: number;
  config?: ChatSessionConfig;
}

export interface AdminUserSummary {
  id: string;
  name: string;
  email: string;
  user_level: string;
  is_admin: boolean;
  created_at: string;
  last_active_at: string | null;
  weekly_token_total: number;
}

// 认证相关 API
export const authAPI = {
  /**
   * 用户登录
   */
  async login(email: string, password: string) {
    return request<{ token: string; user: AuthenticatedUser }>('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ email, password }),
    });
  },
  
  /**
   * 用户注册
   */
  async register(email: string, password: string, name: string, code: string) {
    return request<{ token: string; user: AuthenticatedUser }>('/auth/register', {
      method: 'POST',
      body: JSON.stringify({ email, password, name, code }),
    });
  },
  
  /**
   * 重置密码
   */
  async resetPassword(email: string, password: string, code: string) {
    return request<{ message: string }>('/auth/reset-password', {
      method: 'POST',
      body: JSON.stringify({ email, password, code }),
    });
  },

  /**
   * 发送验证码
   */
  async sendVerificationCode(email: string, type: 'register' | 'reset' = 'register') {
    return request<{ message: string }>('/auth/send-code', {
      method: 'POST',
      body: JSON.stringify({ email, type }),
    });
  },
  
  /**
   * 获取当前用户信息
   */
  async getMe() {
    return request<{ 
      id: string; 
      name: string; 
      email: string; 
      avatar: string | null;
      user_level: string;
      is_admin: boolean;
      membership_expires_at: string | null;
    }>('/auth/me', {
      method: 'GET',
    });
  },

  /**
   * 更新用户资料
   */
  async updateProfile(data: { name?: string; avatar?: string }) {
    return request<{ 
      id: string; 
      name: string; 
      email: string; 
      avatar: string | null;
      user_level: string;
      is_admin: boolean;
      membership_expires_at: string | null;
    }>('/auth/profile', {
      method: 'PATCH',
      body: JSON.stringify(data),
    });
  },

  /**
   * 上传用户头像
   */
  async uploadAvatar(file: File) {
    const formData = new FormData();
    formData.append('file', file);

    const token = localStorage.getItem('auth_token');
    const response = await fetch(`${API_BASE_URL}/auth/upload-avatar`, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${token}`,
      },
      body: formData,
    });

    const data = await response.json();
    if (!response.ok) {
      const error = data.detail?.error || data.error || data;
      throw new Error(error.message || '上传失败');
    }

    // 后端返回 avatar_url，转换为前端期望的 url
    return { url: data.avatar_url } as { url: string };
  },

  /**
   * 检查用户名是否可用
   */
  async checkUsername(username: string) {
    return request<{ available: boolean }>('/auth/check-username', {
      method: 'POST',
      body: JSON.stringify({ username }),
    });
  },

  /**
   * 激活会员
   */
  async activate(code: string) {
    return request<{
      id: string;
      email: string;
      name: string;
      avatar: string | null;
      user_level: string;
      is_member: boolean;
      is_advanced_member: boolean;
      is_admin: boolean;
      member_expires_at: string | null;
    }>('/auth/activate', {
      method: 'POST',
      body: JSON.stringify({ code }),
    });
  },
};

// 组织相关 API
export const organizationAPI = {
  /**
   * 创建组织
   */
  async create(data: { name: string; description?: string; avatar?: string }) {
    return request<{ 
      id: string; 
      name: string; 
      description: string; 
      avatar: string | null; 
      org_code: string; 
      owner_id: string; 
      member_count: number;
      created_at: string;
    }>('/organizations', {
      method: 'POST',
      body: JSON.stringify(data),
    });
  },

  /**
   * 列出我的组织
   */
  async list() {
    return request<OrganizationListResponse>('/organizations', {
      method: 'GET',
    });
  },

  /**
   * 获取组织详情
   */
  async get(id: string) {
    return request<OrganizationDetailResponse>(`/organizations/${id}`, {
      method: 'GET',
    });
  },

  /**
   * 更新组织信息
   */
  async update(id: string, data: { name?: string; description?: string; avatar?: string }) {
    return request<OrganizationDetailResponse>(`/organizations/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    });
  },

  /**
   * 解散组织
   */
  async delete(id: string) {
    return request<{ success: boolean }>(`/organizations/${id}`, {
      method: 'DELETE',
    });
  },

  /**
   * 加入组织
   */
  async join(orgCode: string) {
    return request<{ success: boolean; org_id: string }>('/organizations/join', {
      method: 'POST',
      body: JSON.stringify({ org_code: orgCode }),
    });
  },

  /**
   * 退出组织
   */
  async leave(id: string) {
    return request<{ success: boolean }>(`/organizations/${id}/leave`, {
      method: 'DELETE',
    });
  },

  /**
   * 获取组织成员
   */
  async getMembers(id: string) {
    return request<{ members: OrganizationMember[] }>(`/organizations/${id}/members`, {
      method: 'GET',
    });
  },

  /**
   * 移除成员
   */
  async removeMember(id: string, userId: string) {
    return request<{ success: boolean }>(`/organizations/${id}/members/${userId}`, {
      method: 'DELETE',
    });
  },

  /**
   * 重新生成组织码
   */
  async regenerateCode(id: string) {
    return request<{ org_code: string }>(`/organizations/${id}/regenerate-code`, {
      method: 'POST',
    });
  },

  /**
   * 设置组织码有效期
   */
  async setCodeExpiry(id: string, expiresAt: string | null) {
    return request<{ success: boolean }>(`/organizations/${id}/code-expiry`, {
      method: 'PATCH',
      body: JSON.stringify({ expires_at: expiresAt }),
    });
  },
};

// 知识库相关 API
export const kbAPI = {
  /**
   * 列出知识库
   */
  async listKnowledgeBases(query?: string, page: number = 1, pageSize: number = 20) {
    const params = new URLSearchParams({ page: page.toString(), pageSize: pageSize.toString() });
    if (query) params.append('q', query);
    return request<PaginatedResponse<KnowledgeBaseListItem>>(
      `/kb?${params}`,
      { method: 'GET' }
    );
  },

  /**
   * 创建知识库
   */
  async createKnowledgeBase(name: string, description: string, category: string = "其它") {
    return request<{ id: string }>('/kb', {
      method: 'POST',
      body: JSON.stringify({ name, description, category }),
    });
  },

  /**
   * 获取知识库信息（支持自己的和公开的）
   */
  async getKnowledgeBaseInfo(kbId: string) {
    return request<{
      id: string;
      name: string;
      description: string;
      category: string;
      visibility: 'private' | 'organization' | 'public';
      subscribersCount: number;
      viewCount: number;
      contents: number;
      avatar: string;
      createdAt: string;
      updatedAt: string;
      ownerId?: string;
      isOwner: boolean;
      isSubscribed: boolean;
    }>(`/kb/${kbId}/info`, {
      method: 'GET',
    });
  },

  /**
   * 更新知识库
   */
  async updateKnowledgeBase(kbId: string, data: { name?: string; description?: string; category?: string; avatar?: string }) {
    return request<{ success: boolean }>(`/kb/${kbId}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    });
  },

  /**
   * 删除知识库
   */
  async deleteKnowledgeBase(kbId: string) {
    return request<{ success: boolean }>(`/kb/${kbId}`, {
      method: 'DELETE',
    });
  },

  /**
   * 获取存储配额
   */
  async getQuota() {
    return request<{ usedBytes: number; limitBytes: number }>('/kb/quota', {
      method: 'GET',
    });
  },

  /**
   * 初始化直传（获取 MinIO 预签名 URL）
   */
  async initDirectUpload(kbId: string, file: File) {
    return request<{ id: string; name: string; status: string; uploadUrl: string }>(
      `/kb/${kbId}/documents/direct-upload/init`,
      {
        method: 'POST',
        body: JSON.stringify({
          filename: file.name,
          size: file.size,
          contentType: file.type || 'application/octet-stream',
        }),
      }
    );
  },

  /**
   * 完成直传并触发后端处理
   */
  async completeDirectUpload(kbId: string, docId: string) {
    return request<{ id: string; name: string; status: string }>(
      `/kb/${kbId}/documents/direct-upload/complete`,
      {
        method: 'POST',
        body: JSON.stringify({ docId }),
      }
    );
  },

  /**
   * 使用预签名 URL 直传文件（带进度和重试）
   */
  async uploadToPresignedUrl(
    uploadUrl: string,
    file: File,
    options?: {
      onProgress?: (progress: number) => void;
      retries?: number;
      retryDelayMs?: number;
    }
  ) {
    const maxRetries = options?.retries ?? 2;
    const retryDelayMs = options?.retryDelayMs ?? 800;

    const uploadOnce = () => new Promise<void>((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open('PUT', uploadUrl, true);
      xhr.timeout = 5 * 60 * 1000;
      xhr.setRequestHeader('Content-Type', file.type || 'application/octet-stream');

      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable && options?.onProgress) {
          const progress = Math.round((event.loaded / event.total) * 100);
          options.onProgress(Math.max(0, Math.min(100, progress)));
        }
      };

      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          options?.onProgress?.(100);
          resolve();
        } else {
          reject(new Error(`直传失败 (${xhr.status})`));
        }
      };

      xhr.onerror = () => reject(new Error('直传网络错误'));
      xhr.ontimeout = () => reject(new Error('直传超时'));
      xhr.send(file);
    });

    let lastError: unknown;
    for (let attempt = 0; attempt <= maxRetries; attempt += 1) {
      try {
        await uploadOnce();
        return;
      } catch (error) {
        lastError = error;
        if (attempt === maxRetries) {
          break;
        }
        await new Promise((resolve) => setTimeout(resolve, retryDelayMs * (attempt + 1)));
      }
    }

    throw lastError instanceof Error ? lastError : new Error('直传失败');
  },

  /**
   * 列出文档
   */
  async listDocuments(kbId: string, page: number = 1, pageSize: number = 20) {
    const params = new URLSearchParams({ page: page.toString(), pageSize: pageSize.toString() });
    return request<PaginatedResponse<KnowledgeDocumentListItem>>(
      `/kb/${kbId}/documents?${params}`,
      { method: 'GET' }
    );
  },

  /**
   * 获取文档处理状态
   */
  async getDocumentStatus(kbId: string, docId: string) {
    return request<{ status: string; errorMessage: string | null; chunkCount: number }>(
      `/kb/${kbId}/documents/${docId}/status`,
      { method: 'GET' }
    );
  },

  /**
   * 删除文档
   */
  async deleteDocument(kbId: string, docId: string) {
    return request<{ success: boolean }>(`/kb/${kbId}/documents/${docId}`, {
      method: 'DELETE',
    });
  },

  /**
   * 移动文档到另一个知识库
   */
  async moveDocument(sourceKbId: string, docId: string, targetKbId: string) {
    return request<{
      id: string;
      name: string;
      sourceKbId: string;
      targetKbId: string;
      status: string;
    }>(`/kb/${sourceKbId}/documents/${docId}/move`, {
      method: 'POST',
      body: JSON.stringify({ targetKbId }),
    });
  },

  /**
   * 重试处理失败的文档
   */
  async retryDocument(kbId: string, docId: string) {
    return request<{ id: string; name: string; status: string }>(`/kb/${kbId}/documents/${docId}/retry`, {
      method: 'POST',
    });
  },

  /**
   * 在知识库中检索
   */
  async searchInKB(kbId: string, question: string, topN: number = 10) {
    return request<{ messageId: string; references: unknown[]; answer: string }>(
      `/kb/${kbId}/chat/messages`,
      {
        method: 'POST',
        body: JSON.stringify({ question, top_n: topN }),
      }
    );
  },

  /**
   * 获取文档预览 URL
   */
  async getDocumentUrl(kbId: string, docId: string) {
    return request<{ url: string; name: string }>(`/kb/${kbId}/documents/${docId}/url`, {
      method: 'GET',
    });
  },

  /**
   * 获取文档 Markdown 内容（用于非 PDF 文件预览）
   */
  async getDocumentMarkdown(kbId: string, docId: string) {
    return request<{ content: string }>(`/kb/${kbId}/documents/${docId}/markdown`, {
      method: 'GET',
    });
  },

  /**
   * 上传知识库头像
   */
  async uploadAvatar(kbId: string, file: File) {
    const formData = new FormData();
    formData.append('file', file);

    const token = localStorage.getItem('auth_token');
    const response = await fetch(`${API_BASE_URL}/kb/${kbId}/avatar`, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${token}`,
      },
      body: formData,
    });

    const data = await response.json();
    if (!response.ok) {
      const error = data.detail?.error || data.error || data;
      throw new Error(error.message || '上传失败');
    }

    return data;
  },

  // ============ 公开共享 & 订阅功能 ============

  /**
   * 切换知识库公开/私有状态
   */
  async togglePublic(kbId: string) {
    return request<{ visibility: 'private' | 'organization' | 'public'; subscribersCount: number }>(`/kb/${kbId}/toggle-public`, {
      method: 'POST',
    });
  },

  /**
   * 订阅公开知识库
   */
  async subscribe(kbId: string) {
    return request<{ subscribersCount: number }>(`/kb/${kbId}/subscribe`, {
      method: 'POST',
    });
  },

  /**
   * 取消订阅知识库
   */
  async unsubscribe(kbId: string) {
    return request<{ subscribersCount: number }>(`/kb/${kbId}/subscribe`, {
      method: 'DELETE',
    });
  },

  /**
   * 检查订阅状态
   */
  async checkSubscription(kbId: string) {
    return request<{ isSubscribed: boolean; subscribedAt: string | null }>(`/kb/${kbId}/subscription-status`, {
      method: 'GET',
    });
  },

  /**
   * 获取我的订阅列表
   */
  async listSubscriptions(page: number = 1, pageSize: number = 20) {
    const params = new URLSearchParams({ page: page.toString(), pageSize: pageSize.toString() });
    return request<PaginatedResponse<KnowledgeBaseListItem>>(
      `/kb/subscriptions/list?${params}`,
      { method: 'GET' }
    );
  },

  /**
   * 获取公开知识库列表
   */
  async listPublicKBs(category?: string, query?: string, page: number = 1, pageSize: number = 20) {
    const params = new URLSearchParams({ page: page.toString(), pageSize: pageSize.toString() });
    if (category) params.append('category', category);
    if (query) params.append('q', query);
    return request<PaginatedResponse<KnowledgeBaseListItem>>(
      `/kb/public/list?${params}`,
      { method: 'GET' }
    );
  },

  /**
   * 获取精选知识库列表（2025年度精选）
   */
  async listFeatured(page: number = 1, pageSize: number = 30) {
    const params = new URLSearchParams({ page: page.toString(), pageSize: pageSize.toString() });
    return request<PaginatedResponse<KnowledgeBaseListItem>>(
      `/kb/featured/list?${params}`,
      { method: 'GET' }
    );
  },

  /**
   * 获取分类统计
   */
  async getCategoriesStats() {
    return request<{ categories: Array<{ category: string; count: number; subscribers: number }> }>(
      '/kb/categories/stats',
      { method: 'GET' }
    );
  },

  /**
   * 获取知识广场列表（根据用户权限自动过滤）
   */
  async getPlaza(category?: string, query?: string, page: number = 1, pageSize: number = 20) {
    const params = new URLSearchParams({ page: page.toString(), pageSize: pageSize.toString() });
    if (category) params.append('category', category);
    if (query) params.append('q', query);
    return request<PaginatedResponse<KnowledgeBaseListItem>>(
      `/kb/plaza?${params}`,
      { method: 'GET' }
    );
  },

  /**
   * 设置知识库可见性
   */
  async updateVisibility(kbId: string, visibility: 'private' | 'organization' | 'public', sharedToOrgs?: string[]) {
    return request<KnowledgeVisibilityResponse>(
      `/kb/${kbId}/visibility`,
      {
        method: 'PATCH',
        body: JSON.stringify({ 
          visibility,
          shared_to_orgs: sharedToOrgs 
        }),
      }
    );
  },

  /**
   * 共享知识库到指定组织
   */
  async shareToOrgs(kbId: string, orgIds: string[]) {
    return request<KnowledgeVisibilityResponse>(
      `/kb/${kbId}/share-to-orgs`,
      {
        method: 'POST',
        body: JSON.stringify({ org_ids: orgIds }),
      }
    );
  },

  /**
   * 获取知识库共享状态
   */
  async getSharedStatus(kbId: string) {
    return request<{
      kb_id: string;
      visibility: string;
      shared_to_orgs: Array<{ id: string; name: string; avatar: string | null }>;
      is_owner: boolean;
      can_modify: boolean;
    }>(
      `/kb/${kbId}/shared-status`,
      { method: 'GET' }
    );
  },
};

// 笔记相关 API
export const noteAPI = {
  /**
   * 列出文件夹
   */
  async listFolders() {
    const response = await request<{ folders: Array<{ id: string; name: string; noteCount: number; createdAt: string }> }>(
      '/notes/folders',
      { method: 'GET' }
    );
    // 转换为前端期望的格式
    return response.folders.map(f => ({
      id: f.id,
      name: f.name,
      count: f.noteCount
    }));
  },

  /**
   * 创建文件夹
   */
  async createFolder(name: string) {
    return request<{ id: string; name: string }>('/notes/folders', {
      method: 'POST',
      body: JSON.stringify({ name }),
    });
  },

  /**
   * 重命名文件夹
   */
  async renameFolder(folderId: string, name: string) {
    return request<{ success: boolean }>(`/notes/folders/${folderId}`, {
      method: 'PATCH',
      body: JSON.stringify({ name }),
    });
  },

  /**
   * 删除文件夹
   */
  async deleteFolder(folderId: string) {
    return request<{ success: boolean }>(`/notes/folders/${folderId}`, {
      method: 'DELETE',
    });
  },

  /**
   * 列出笔记
   */
  async listNotes(folderId?: string, query?: string, page: number = 1, pageSize: number = 50) {
    const params = new URLSearchParams({ page: page.toString(), pageSize: pageSize.toString() });
    if (folderId) params.append('folderId', folderId);
    if (query) params.append('query', query);
    return request<PaginatedResponse<NoteListItem>>(
      `/notes?${params}`,
      { method: 'GET' }
    );
  },

  /**
   * 获取笔记详情
   */
  async getNote(noteId: string) {
    return request<{ id: string; title: string; content: string; folderId: string | null; createdAt: string; updatedAt: string }>(
      `/notes/${noteId}`,
      { method: 'GET' }
    );
  },

  /**
   * 创建笔记
   */
  async createNote(data: { title: string; content?: string; folder?: string; tags?: string[] }) {
    return request<NoteListItem>(
      '/notes',
      {
        method: 'POST',
        body: JSON.stringify({ 
          title: data.title, 
          content: data.content || '', 
          folder: data.folder || null,
          tags: data.tags || []
        }),
      }
    );
  },

  /**
   * 更新笔记
   */
  async updateNote(noteId: string, data: { title?: string; content?: string; folderId?: string | null }) {
    return request<NoteListItem>(`/notes/${noteId}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    });
  },

  /**
   * 删除笔记
   */
  async deleteNote(noteId: string) {
    return request<{ success: boolean }>(`/notes/${noteId}`, {
      method: 'DELETE',
    });
  },
};

// 收藏相关 API
export const favoriteAPI = {
  /**
   * 收藏知识库
   */
  async favoriteKB(kbId: string) {
    return request<{ success: boolean }>(`/favorites/kb/${kbId}`, {
      method: 'POST',
    });
  },

  /**
   * 取消收藏知识库
   */
  async unfavoriteKB(kbId: string) {
    return request<{ success: boolean }>(`/favorites/kb/${kbId}`, {
      method: 'DELETE',
    });
  },

  /**
   * 获取收藏的知识库列表
   */
  async listFavoriteKBs(page: number = 1, pageSize: number = 20) {
    const params = new URLSearchParams({ page: page.toString(), pageSize: pageSize.toString() });
    return request<PaginatedResponse<KnowledgeBaseListItem>>(
      `/favorites/kb?${params}`,
      { method: 'GET' }
    );
  },

  /**
   * 收藏文档
   */
  async favoriteDocument(docId: string, kbId: string) {
    const params = new URLSearchParams({ kbId });
    return request<{ success: boolean }>(`/favorites/document/${docId}?${params}`, {
      method: 'POST',
    });
  },

  /**
   * 取消收藏文档
   */
  async unfavoriteDocument(docId: string) {
    return request<{ success: boolean }>(`/favorites/document/${docId}`, {
      method: 'DELETE',
    });
  },

  /**
   * 获取收藏的文档列表
   */
  async listFavoriteDocuments(page: number = 1, pageSize: number = 20) {
    const params = new URLSearchParams({ page: page.toString(), pageSize: pageSize.toString() });
    return request<PaginatedResponse<FavoriteDocumentListItem>>(
      `/favorites/document?${params}`,
      { method: 'GET' }
    );
  },

  /**
   * 批量检查收藏状态
   */
  async checkFavorites(items: Array<{ type: string; id: string }>) {
    return request<{ [key: string]: boolean }>('/favorites/check', {
      method: 'POST',
      body: JSON.stringify({ items }),
    });
  },
};

// ==================== 聊天会话相关 API ====================
export const chatAPI = {

  /**
   * 获取用户的所有聊天会话
   */
  async listChatSessions(page: number = 1, pageSize: number = 50) {
    const params = new URLSearchParams({ 
      page: page.toString(), 
      pageSize: pageSize.toString() 
    });
    return request<{ 
      sessions: Array<{
        id: string;
        title: string;
        lastMessage: string;
        timestamp: string;
        createdAt: string;
        updatedAt: string;
        messageCount: number;
        config?: ChatSessionConfig;
      }>;
      page: number;
      pageSize: number;
    }>(`/chat/sessions?${params}`, { method: 'GET' });
  },

  /**
   * 创建新的聊天会话
   */
  async createChatSession(firstMessage: string, config: ChatSessionConfig) {
    return request<ChatSessionSummary>('/chat/sessions', {
      method: 'POST',
      body: JSON.stringify({ 
        first_message: firstMessage,
        config: config 
      }),
    });
  },

  async createEmptyChatSession(config: ChatSessionConfig, title?: string) {
    return request<ChatSessionSummary>('/chat/sessions/empty', {
      method: 'POST',
      body: JSON.stringify({
        config,
        title,
      }),
    });
  },

  /**
   * 获取聊天会话详情
   */
  async getChatSession(sessionId: string) {
    return request<{
      id: string;
      title: string;
      lastMessage: string;
      timestamp: string;
      createdAt: string;
      updatedAt: string;
      messageCount: number;
      config?: ChatSessionConfig;
    }>(`/chat/sessions/${sessionId}`, { method: 'GET' });
  },

  /**
   * 更新聊天会话配置（部分更新）
   */
  async updateChatSessionConfig(sessionId: string, configUpdates: ChatSessionConfigUpdate) {
    return request<Pick<ChatSessionSummary, 'id' | 'title' | 'config'>>(
      `/chat/sessions/${sessionId}/config`,
      {
      method: 'PATCH',
      body: JSON.stringify({ config: configUpdates }),
      }
    );
  },

  async listChatModels() {
    return request<{
      default_model: string;
      models: ChatModelOption[];
    }>('/rag/models', {
      method: 'GET',
    });
  },

  async getModelConfigCatalog() {
    return request<ModelConfigCatalogResponse>('/model-config', {
      method: 'GET',
    });
  },

  async previewProviderRemoteModels(providerCode: string, apiKey?: string, baseUrl?: string) {
    return request<ProviderRemoteModelsResponse>(`/model-config/providers/${providerCode}/models/preview`, {
      method: 'POST',
      body: JSON.stringify({
        api_key: apiKey,
        base_url: baseUrl,
      }),
    });
  },

  async saveModelProviderCredential(providerCode: string, apiKey?: string, baseUrl?: string) {
    return request<{
      provider_code: string;
      provider_display_name: string;
      provider_icon_key: string;
      api_key_masked: string;
      credential_configured: boolean;
    }>(`/model-config/providers/${providerCode}/credential`, {
      method: 'POST',
      body: JSON.stringify({
        api_key: apiKey,
        base_url: baseUrl,
      }),
    });
  },

  async deleteModelProviderCredential(providerCode: string) {
    return request<{
      success: true;
      provider_code: string;
      provider_display_name: string;
      removed_bindings_count: number;
    }>(`/model-config/providers/${providerCode}/credential`, {
      method: 'DELETE',
    });
  },

  async createUserModelBinding(providerCode: string, providerModelName: string, apiKey?: string, baseUrl?: string) {
    return request<UserConfiguredModel>('/model-config/models', {
      method: 'POST',
      body: JSON.stringify({
        provider_code: providerCode,
        provider_model_name: providerModelName,
        api_key: apiKey,
        base_url: baseUrl,
      }),
    });
  },

  async deleteUserModelBinding(bindingId: string) {
    return request<{ success: true }>(`/model-config/models/${bindingId}`, {
      method: 'DELETE',
    });
  },

  async updateUserModelBindingEnabled(bindingId: string, isEnabled: boolean) {
    return request<UserConfiguredModel>(`/model-config/models/${bindingId}/enabled`, {
      method: 'PATCH',
      body: JSON.stringify({
        is_enabled: isEnabled,
      }),
    });
  },

  async updateProviderBindingsEnabled(providerCode: string, isEnabled: boolean) {
    return request<UserConfiguredModel[]>(`/model-config/providers/${providerCode}/enabled`, {
      method: 'PATCH',
      body: JSON.stringify({
        is_enabled: isEnabled,
      }),
    });
  },

  async runUserModelHealthCheck(bindingId: string) {
    return request<UserConfiguredModel>(`/model-config/models/${bindingId}/health-check`, {
      method: 'POST',
    });
  },

  async clearUserModelHealthStatuses() {
    return request<{ success: true; cleared_count: number }>('/model-config/health/reset', {
      method: 'POST',
    });
  },

  /**
   * 删除聊天会话
   */
  async deleteChatSession(sessionId: string) {
    return request<{ success: boolean }>(`/chat/sessions/${sessionId}`, {
      method: 'DELETE',
    });
  },

  /**
   * 删除所有聊天会话
   */
  async deleteAllChatSessions() {
    return request<{ success: boolean; deleted_count: number }>(`/chat/sessions/all`, {
      method: 'DELETE',
    });
  },

  /**
   * 获取会话的所有消息
   */
  async getChatMessages(sessionId: string) {
    return request<{
      messages: ChatHistoryMessage[];
    }>(`/chat/sessions/${sessionId}/messages`, { method: 'GET' });
  },

  /**
   * 准备 lumen 线程运行时
   */
  async prepareChatRuntime(sessionId: string, payload: ChatRuntimePrepareRequest) {
    return request<ChatRuntimePrepareResponse>(`/chat-runtime/sessions/${sessionId}/thread/prepare`, {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  },

  async getChatRuntimeThreadUploads(
    sessionId: string,
    options?: {
      sync_workspace_assets?: boolean;
      sync_kb_documents?: boolean;
    },
  ) {
    const params = new URLSearchParams();
    if (typeof options?.sync_workspace_assets === 'boolean') {
      params.set('sync_workspace_assets', String(options.sync_workspace_assets));
    }
    if (typeof options?.sync_kb_documents === 'boolean') {
      params.set('sync_kb_documents', String(options.sync_kb_documents));
    }
    const suffix = params.toString() ? `?${params.toString()}` : '';
    return request<ChatRuntimeThreadUploadsResponse>(`/chat-runtime/sessions/${sessionId}/thread/uploads${suffix}`, {
      method: 'GET',
    });
  },

  async uploadChatRuntimeThreadFiles(sessionId: string, files: File[]) {
    const body = new FormData();
    files.forEach((file) => {
      body.append('files', file);
    });
    return request<ChatRuntimeThreadUploadMutationResponse>(`/chat-runtime/sessions/${sessionId}/thread/uploads`, {
      method: 'POST',
      body,
    });
  },

  async deleteChatRuntimeThreadFile(sessionId: string, filename: string) {
    return request<ChatRuntimeThreadUploadDeleteResponse>(
      `/chat-runtime/sessions/${sessionId}/thread/uploads/${encodeURIComponent(filename)}`,
      {
        method: 'DELETE',
      },
    );
  },

  /**
   * 添加消息到会话
   */
  async addChatMessage(
    sessionId: string,
    role: string,
    content: string,
    thinking?: string,
    imageDataUrls?: string[],
    attachments?: ChatAttachment[],
    documentSummaries?: Array<{
      doc_id: string;
      doc_name: string;
      summary: string;
      from_cache: boolean;
    }>,
    artifacts?: ChatArtifact[],
    toolTraces?: ChatToolTrace[],
    assistantTupleMessages?: ChatAssistantTupleMessage[],
    truncationMetadata?: {
      wasTruncated: boolean;
      truncatedAt?: string;
    },
    interruption?: ChatInterruption,
    options?: {
      messageId?: string;
      keepalive?: boolean;
    },
  ) {
    const normalizedAssistantTupleMessages = (assistantTupleMessages || []).map((message) => ({
      type: message.type,
      id: message.id,
      content: message.content,
      tool_calls: (message.tool_calls || []).map((toolCall) => ({
        id: toolCall.id,
        name: toolCall.name,
        args: toolCall.args,
      })),
      tool_call_id: message.tool_call_id,
      name: message.name,
    }));

    return request<{
      id: string;
      role: string;
      content: string;
      thinking?: string;
      createdAt: string;
      imageDataUrls?: string[];
      attachments?: ChatAttachment[];
      artifacts?: ChatArtifact[];
      toolTraces?: ChatToolTrace[];
      assistantTupleMessages?: ChatAssistantTupleMessage[];
      documentSummaries?: Array<{
        doc_id: string;
        doc_name: string;
        summary: string;
        from_cache: boolean;
      }>;
      wasTruncated?: boolean;
      truncatedAt?: string;
      interruption?: ChatInterruption | null;
    }>(`/chat/sessions/${sessionId}/messages`, {
      method: 'POST',
      keepalive: options?.keepalive,
      body: JSON.stringify({ 
        role, 
        message_id: options?.messageId,
        content, 
        thinking, 
        image_data_urls: imageDataUrls,
        attachments: serializeChatAttachments(attachments),
        document_summaries: documentSummaries,
        artifacts,
        tool_traces: toolTraces,
        assistant_tuple_messages: normalizedAssistantTupleMessages,
        was_truncated: truncationMetadata?.wasTruncated,
        truncated_at: truncationMetadata?.truncatedAt,
        interruption: interruption ? {
          reason: interruption.reason,
          interrupted_at: interruption.interruptedAt,
          retryable: interruption.retryable ?? true,
        } : undefined,
      }),
    });
  },

  /**
   * 获取会话产物附件的下载 URL
   */
  async getSessionArtifactUrl(sessionId: string, objectPath: string) {
    const encodedPath = encodeURIComponent(objectPath);
    return request<{
      objectPath: string;
      name: string;
      url: string;
      expiresIn: number;
    }>(`/chat/sessions/${sessionId}/artifacts/url?object_path=${encodedPath}`, {
      method: 'GET',
    });
  },

  async downloadSessionArtifact(sessionId: string, objectPath: string) {
    const encodedPath = encodeURIComponent(objectPath);
    return requestBlob(`/chat/sessions/${sessionId}/artifacts/download?object_path=${encodedPath}`, {
      method: 'GET',
    });
  },

  /**
   * 删除会话中最后一条 AI 回复
   */
  async deleteLastAssistantMessage(sessionId: string) {
    return request<{
      success: boolean;
      deleted_message_id: string | null;
    }>(`/chat/sessions/${sessionId}/messages/last-assistant`, {
      method: 'DELETE',
    });
  },
};

// 管理员相关 API
export const adminAPI = {
  /**
   * 生成激活码
   */
  async generateCode(data: {
    type: 'member' | 'premium';
    duration_days?: number;
    max_usage: number;
    code_expires_in_days?: number;
  }) {
    return request<{
      id: string;
      code: string;
      type: string;
      duration_days: number | null;
      max_usage: number;
      used_count: number;
      expires_at: string | null;
      is_active: boolean;
      is_valid: boolean;
    }>('/admin/codes', {
      method: 'POST',
      body: JSON.stringify(data),
    });
  },

  /**
   * 批量生成激活码
   */
  async batchGenerateCodes(
    count: number,
    data: {
      type: 'member' | 'premium';
      duration_days?: number;
      max_usage: number;
      code_expires_in_days?: number;
    }
  ) {
    const params = new URLSearchParams({ count: count.toString() });
    return request<{
      count: number;
      type: string;
      duration_days: number | null;
      max_usage: number;
      expires_at: string | null;
      codes: Array<{
        id: string;
        code: string;
        type: string;
        duration_days: number | null;
        max_usage: number;
        used_count: number;
        expires_at: string | null;
        is_active: boolean;
        is_valid: boolean;
      }>;
    }>(`/admin/codes/batch?${params}`, {
      method: 'POST',
      body: JSON.stringify(data),
    });
  },

  /**
   * 列出激活码
   */
  async listCodes(
    type?: 'member' | 'premium',
    isActive?: boolean,
    page: number = 1,
    pageSize: number = 20
  ) {
    const params = new URLSearchParams({ 
      page: page.toString(), 
      page_size: pageSize.toString() 
    });
    if (type) params.append('type', type);
    if (isActive !== undefined) params.append('is_active', isActive.toString());
    
    return request<{
      items: Array<{
        id: string;
        code: string;
        type: string;
        duration_days: number | null;
        max_usage: number;
        used_count: number;
        expires_at: string | null;
        is_active: boolean;
        is_valid: boolean;
        created_at: string;
      }>;
      total: number;
      page: number;
      page_size: number;
    }>(`/admin/codes?${params}`, {
      method: 'GET',
    });
  },

  /**
   * 作废激活码
   */
  async deactivateCode(code: string) {
    return request<{ message: string }>(`/admin/codes/${code}`, {
      method: 'DELETE',
    });
  },

  /**
   * 验证激活码
   */
  async validateCode(code: string) {
    return request<{
      valid: boolean;
      type?: string;
      duration_days?: number;
      remaining_usage?: number;
      reason?: string;
    }>(`/admin/codes/validate/${code}`, {
      method: 'GET',
    });
  },

  /**
   * 设置用户为管理员
   */
  async setUserAdmin(userId: string) {
    return request<{
      message: string;
      user: AdminUserSummary;
    }>(`/admin/users/${userId}/set-admin`, {
      method: 'POST',
    });
  },

  /**
   * 取消用户管理员权限
   */
  async removeUserAdmin(userId: string) {
    return request<{
      message: string;
      user: AdminUserSummary;
    }>(`/admin/users/${userId}/remove-admin`, {
      method: 'DELETE',
    });
  },

  /**
   * 获取统计数据
   */
  async getStatistics() {
    return request<{
      users: {
        total: number;
        explorers: number;
        members: number;
        advanced_members: number;
        admins: number;
      };
      organizations: {
        total: number;
        average_members: number;
      };
      knowledge_bases: {
        total: number;
        public: number;
        shared: number;
      };
    }>('/admin/statistics', {
      method: 'GET',
    });
  },

  /**
   * 列出所有用户
   */
  async listUsers(page: number = 1, pageSize: number = 20) {
    const params = new URLSearchParams({ 
      page: page.toString(), 
      page_size: pageSize.toString() 
    });
    return request<{
      items: Array<{
        id: string;
        name: string;
        email: string;
        avatar: string | null;
        user_level: string;
        is_admin: boolean;
        created_at: string;
        last_active_at: string | null;
        weekly_token_total: number;
      }>;
      total: number;
      page: number;
      page_size: number;
    }>(`/admin/users?${params}`, {
      method: 'GET',
    });
  },
};

// ==================== 统一导出所有 API ====================
export const api = {
  ...authAPI,
  ...kbAPI,
  ...noteAPI,
  ...favoriteAPI,
  ...chatAPI,
  ...organizationAPI,
  ...adminAPI,
};
