// API 客户端工具
import { safeLocalStorageRemove } from '@/utils/localStorage';

// API 基础配置
// 开发环境使用相对路径，通过 Vite 代理
// 生产环境使用环境变量配置的完整 URL
export const API_BASE_URL = import.meta.env.VITE_API_URL || '/api';

// 通用请求函数
async function request<T>(
  endpoint: string,
  options: RequestInit = {}
): Promise<T> {
  const token = localStorage.getItem('auth_token');
  
  const headers: HeadersInit = {
    'Content-Type': 'application/json',
    ...options.headers,
  };
  
  // 添加 Authorization header（如果有 token）
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }
  
  const response = await fetch(`${API_BASE_URL}${endpoint}`, {
    ...options,
    headers,
  });
  
  const data = await response.json();
  
  if (!response.ok) {
    // 处理错误响应
    // 1. 处理 FastAPI Pydantic 验证错误 (422)
    if (response.status === 422 && Array.isArray(data.detail)) {
      const firstError = data.detail[0];
      const field = firstError.loc[firstError.loc.length - 1];
      const msg = firstError.msg;
      throw new Error(`参数错误 (${field}): ${msg}`);
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
        safeLocalStorageRemove('userProfile');
        
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
  
  return data;
}

// 认证相关 API
export const authAPI = {
  /**
   * 用户登录
   */
  async login(email: string, password: string) {
    return request<{ token: string; user: any }>('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ email, password }),
    });
  },
  
  /**
   * 用户注册
   */
  async register(email: string, password: string, name: string, code: string) {
    return request<{ token: string; user: any }>('/auth/register', {
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
    return request<{ 
      created: any[]; 
      joined: any[] 
    }>('/organizations', {
      method: 'GET',
    });
  },

  /**
   * 获取组织详情
   */
  async get(id: string) {
    return request<any>(`/organizations/${id}`, {
      method: 'GET',
    });
  },

  /**
   * 更新组织信息
   */
  async update(id: string, data: { name?: string; description?: string; avatar?: string }) {
    return request<any>(`/organizations/${id}`, {
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
    return request<{ members: any[] }>(`/organizations/${id}/members`, {
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
    return request<{ total: number; page: number; pageSize: number; items: any[] }>(
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
      isPublic: boolean;
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
   * 上传文档
   */
  async uploadDocument(kbId: string, file: File) {
    const formData = new FormData();
    formData.append('file', file);

    const token = localStorage.getItem('auth_token');
    const response = await fetch(`${API_BASE_URL}/kb/${kbId}/documents`, {
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

  /**
   * 列出文档
   */
  async listDocuments(kbId: string, page: number = 1, pageSize: number = 20) {
    const params = new URLSearchParams({ page: page.toString(), pageSize: pageSize.toString() });
    return request<{ total: number; page: number; pageSize: number; items: any[] }>(
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
    return request<{ messageId: string; references: any[]; answer: string }>(
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
    return request<{ isPublic: boolean; subscribersCount: number }>(`/kb/${kbId}/toggle-public`, {
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
    return request<{ total: number; page: number; pageSize: number; items: any[] }>(
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
    return request<{ total: number; page: number; pageSize: number; items: any[] }>(
      `/kb/public/list?${params}`,
      { method: 'GET' }
    );
  },

  /**
   * 获取精选知识库列表（2025年度精选）
   */
  async listFeatured(page: number = 1, pageSize: number = 30) {
    const params = new URLSearchParams({ page: page.toString(), pageSize: pageSize.toString() });
    return request<{ total: number; page: number; pageSize: number; items: any[] }>(
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
    return request<{ total: number; page: number; pageSize: number; items: any[] }>(
      `/kb/plaza?${params}`,
      { method: 'GET' }
    );
  },

  /**
   * 设置知识库可见性
   */
  async updateVisibility(kbId: string, visibility: 'private' | 'organization' | 'public', sharedToOrgs?: string[]) {
    return request<any>(
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
    return request<any>(
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
      is_public: boolean;
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
    return request<{ total: number; page: number; pageSize: number; items: any[] }>(
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
    return request<any>(
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
    return request<any>(`/notes/${noteId}`, {
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
    return request<{ total: number; page: number; pageSize: number; items: any[] }>(
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
    return request<{ total: number; page: number; pageSize: number; items: any[] }>(
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
        config?: {
          kbIds?: string[];
          docIds?: string[];
          sourceType?: 'home' | 'knowledge' | 'favorites';
          isKBLocked?: boolean;
          allowWebSearch?: boolean;
          deepThinking?: boolean;
        };
      }>;
      page: number;
      pageSize: number;
    }>(`/chat/sessions?${params}`, { method: 'GET' });
  },

  /**
   * 创建新的聊天会话
   */
  async createChatSession(firstMessage: string, config?: {
    kbIds?: string[];
    docIds?: string[];
    sourceType?: 'home' | 'knowledge' | 'favorites';
    isKBLocked?: boolean;
    allowWebSearch?: boolean;
    deepThinking?: boolean;
  }) {
    return request<{
      id: string;
      title: string;
      lastMessage: string;
      timestamp: string;
      createdAt: string;
      updatedAt: string;
      messageCount: number;
      config?: any;
    }>('/chat/sessions', {
      method: 'POST',
      body: JSON.stringify({ 
        first_message: firstMessage,
        config: config 
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
    }>(`/chat/sessions/${sessionId}`, { method: 'GET' });
  },

  /**
   * 更新聊天会话配置（部分更新）
   */
  async updateChatSessionConfig(sessionId: string, configUpdates: {
    deepThinking?: boolean;
    allowWebSearch?: boolean;
    showThinking?: boolean;
    kbIds?: string[];
    docIds?: string[];
    isKBLocked?: boolean;
  }) {
    return request<{
      id: string;
      title: string;
      config?: any;
    }>(`/chat/sessions/${sessionId}/config`, {
      method: 'PATCH',
      body: JSON.stringify({ config: configUpdates }),
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
      messages: Array<{
        id: string;
        role: string;
        content: string;
        thinking?: string;
        mode?: string;
        createdAt: string;
        documentSummaries?: Array<{  // 文档总结信息
          doc_id: string;
          doc_name: string;
          summary: string;
          from_cache: boolean;
        }>;
      }>;
    }>(`/chat/sessions/${sessionId}/messages`, { method: 'GET' });
  },

  /**
   * 添加消息到会话
   */
  async addChatMessage(
    sessionId: string,
    role: string,
    content: string,
    thinking?: string,
    mode?: string,
    documentSummaries?: Array<{
      doc_id: string;
      doc_name: string;
      summary: string;
      from_cache: boolean;
    }>,
    truncationMetadata?: {
      wasTruncated: boolean;
      truncatedAt?: string;
    }
  ) {
    return request<{
      id: string;
      role: string;
      content: string;
      thinking?: string;
      mode?: string;
      createdAt: string;
      documentSummaries?: Array<{
        doc_id: string;
        doc_name: string;
        summary: string;
        from_cache: boolean;
      }>;
      wasTruncated?: boolean;
      truncatedAt?: string;
    }>(`/chat/sessions/${sessionId}/messages`, {
      method: 'POST',
      body: JSON.stringify({ 
        role, 
        content, 
        thinking, 
        mode,
        document_summaries: documentSummaries,
        was_truncated: truncationMetadata?.wasTruncated,
        truncated_at: truncationMetadata?.truncatedAt
      }),
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
      pageSize: pageSize.toString() 
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
      user: any;
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
      user: any;
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
      }>;
      total: number;
      page: number;
      page_size: number;
    }>(`/admin/users?${params}`, {
      method: 'GET',
    });
  },
};

// ==================== Token 用量统计 API ====================

// Token Usage API Response Types
interface UsageDataPoint {
  time: string;
  input_tokens: number;
  output_tokens: number;
}

interface UsageTotal {
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  request_count: number;
}

export interface HourlyUsageResponse {
  period: 'hourly';
  start_time: string;
  end_time: string;
  data: UsageDataPoint[];
  total: UsageTotal;
}

export interface DailyUsageResponse {
  period: 'daily';
  start_time: string;
  end_time: string;
  data: UsageDataPoint[];
  total: UsageTotal;
}

export interface TotalUsageResponse {
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  request_count: number;
}

export interface QuotaStatusResponse {
  user_level: 'basic' | 'member' | 'premium';
  quota_limit: number;
  used_tokens: number;
  remaining_tokens: number;
  is_exceeded: boolean;
  billing_cycle_start: string;
  billing_cycle_end: string;
  reset_date: string;
}

export const tokenUsageAPI = {
  /**
   * 获取按小时聚合的 Token 使用量
   * @param hours 回溯小时数 (1-168，默认24)
   * @param startTime 可选的开始时间 (ISO 格式)
   * @param endTime 可选的结束时间 (ISO 格式)
   */
  async getHourlyUsage(
    hours: number = 24,
    startTime?: string,
    endTime?: string
  ): Promise<HourlyUsageResponse> {
    const params = new URLSearchParams({ hours: hours.toString() });
    if (startTime) params.append('start_time', startTime);
    if (endTime) params.append('end_time', endTime);
    return request<HourlyUsageResponse>(`/token-usage/hourly?${params}`, {
      method: 'GET',
    });
  },

  /**
   * 获取按天聚合的 Token 使用量
   * @param days 回溯天数 (1-365，默认30)
   * @param startTime 可选的开始时间 (ISO 格式)
   * @param endTime 可选的结束时间 (ISO 格式)
   */
  async getDailyUsage(
    days: number = 30,
    startTime?: string,
    endTime?: string
  ): Promise<DailyUsageResponse> {
    const params = new URLSearchParams({ days: days.toString() });
    if (startTime) params.append('start_time', startTime);
    if (endTime) params.append('end_time', endTime);
    return request<DailyUsageResponse>(`/token-usage/daily?${params}`, {
      method: 'GET',
    });
  },

  /**
   * 获取用户总 Token 使用量
   */
  async getTotalUsage(): Promise<TotalUsageResponse> {
    return request<TotalUsageResponse>('/token-usage/total', {
      method: 'GET',
    });
  },

  /**
   * 获取用户配额状态
   * 返回当前计费周期内的配额使用情况
   */
  async getQuotaStatus(): Promise<QuotaStatusResponse> {
    return request<QuotaStatusResponse>('/token-usage/quota-status', {
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
  ...tokenUsageAPI,
};
