/**
 * 知识库详情页
 * 支持：公开/私有切换、订阅、文档收藏、文档管理
 */
import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useParams, useNavigate, useSearchParams } from 'react-router-dom';
import Sidebar from '@/components/Sidebar/Sidebar';
import OptimizedMarkdown from '@/components/OptimizedMarkdown';
import PDFViewer from '@/components/PDFViewer/PDFViewer';
import DocumentViewer from '@/components/DocumentViewer/DocumentViewer';
import KnowledgeSidebar, { KnowledgeSidebarRef } from '@/components/KnowledgeSidebar/KnowledgeSidebar';
import CreateKnowledgeModal from '@/components/CreateKnowledgeModal/CreateKnowledgeModal';
import EditKnowledgeModal from '@/components/EditKnowledgeModal/EditKnowledgeModal';
import ConfirmModal from '@/components/ConfirmModal/ConfirmModal';
import ShareToOrgModal from '@/components/ShareToOrgModal/ShareToOrgModal';
import SendStopButton from '@/components/SendStopButton';

import { kbAPI, favoriteAPI, api } from '@/lib/api';
import { useToast } from '@/hooks/useToast';
import { getKnowledgeBaseAvatar } from '@/utils/avatarUtils';
import { saveConversationToNoteById } from '@/utils/noteUtils';
import { useRAGChat } from '@/hooks/useRAGChat';
import { useUserProfile } from '@/hooks/useUserProfile';
import { getFileIcon } from '@/utils/fileIcons';
import {
  Upload,
  FileText,
  Globe,
  GlobeLock,
  Users,
  User,
  UserPlus,
  UserMinus,
  Sparkles,
  Star,
  ArrowUp,
  MessageCircle,
  Loader2,
  X,
  Settings,
  Trash2,
  Copy,
  Check,
  ThumbsUp,
  ThumbsDown,
  ChevronDown,
  ChevronUp,
  PlusCircle,
  Share2,
  RefreshCw
} from 'lucide-react';
import QuotaExceededModal from '@/components/QuotaExceededModal/QuotaExceededModal';
import styles from './KnowledgeDetail.module.css';

export default function KnowledgeDetail() {
  const { kbId } = useParams<{ kbId: string }>();
  const navigate = useNavigate();
  const { profile } = useUserProfile();
  const [searchParams, setSearchParams] = useSearchParams();
  const toast = useToast();
  
  // UI State
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);
  const [isMobile, setIsMobile] = useState(false);
  const [isDragging, setIsDragging] = useState(false);
  const [isMainSidebarCollapsed, setIsMainSidebarCollapsed] = useState(false);
  
  // Modal State
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false);
  const [isEditModalOpen, setIsEditModalOpen] = useState(false);
  const [isDeleteModalOpen, setIsDeleteModalOpen] = useState(false);
  const [isTogglePublicModalOpen, setIsTogglePublicModalOpen] = useState(false);
  const [isUnsubscribeModalOpen, setIsUnsubscribeModalOpen] = useState(false);
  const [isShareToOrgModalOpen, setIsShareToOrgModalOpen] = useState(false);
  const [isUnshareModalOpen, setIsUnshareModalOpen] = useState(false);
  
  // Visibility & Sharing State
  const [sharedOrgs, setSharedOrgs] = useState<string[]>([]);
  
  // User State
  const [isAdmin, setIsAdmin] = useState(false);
  
  // Data State
  const [myKnowledgeBases, setMyKnowledgeBases] = useState<any[]>([]);
  const [currentKb, setCurrentKb] = useState<any>(null);
  const [documents, setDocuments] = useState<any[]>([]);
  const [uploading, setUploading] = useState(false);
  const [quota, setQuota] = useState({ usedBytes: 0, limitBytes: 500000000000 });
  
  // Favorite State
  const [favoriteDocIds, setFavoriteDocIds] = useState<Set<string>>(new Set());
  
  // Chat State
  const [chatInput, setChatInput] = useState('');
  const [chatSessions, setChatSessions] = useState<any[]>([]);
  const [currentSessionId, setCurrentSessionId] = useState<string | undefined>(undefined);
  const [hasRestoredSession, setHasRestoredSession] = useState(false);
  const [deepThinking, setDeepThinking] = useState(true);
  
  // 当前知识库的所有文档ID（用于对话）
  const [kbDocIds, setKbDocIds] = useState<string[]>([]);
  
  // PDF Preview State
  const [previewDoc, setPreviewDoc] = useState<any>(null);
  const [previewUrl, setPreviewUrl] = useState<string>('');
  const [previewContent, setPreviewContent] = useState<string>(''); // 非 PDF 文件的 markdown 内容
  const [loadingPreview, setLoadingPreview] = useState(false);
  
  // 消息反馈状态
  const [likedMessages, setLikedMessages] = useState<Set<string>>(new Set());
  const [dislikedMessages, setDislikedMessages] = useState<Set<string>>(new Set());
  const [collapsedThinking, setCollapsedThinking] = useState<Set<string>>(new Set());
  const [savedToNotes, setSavedToNotes] = useState<Set<string>>(new Set()); // 已保存到笔记的消息ID
  const [copiedMessages, setCopiedMessages] = useState<Set<string>>(new Set()); // 已复制的消息ID
  const [showRegenerateMenu, setShowRegenerateMenu] = useState<string | null>(null); // 显示重新生成菜单的消息ID

  // 配额超限弹窗状态
  const [quotaExceededModal, setQuotaExceededModal] = useState<{
    isOpen: boolean;
    userLevel: string;
    usedTokens: number;
    quotaLimit: number;
    resetDate: string;
  }>({
    isOpen: false,
    userLevel: 'basic',
    usedTokens: 0,
    quotaLimit: 0,
    resetDate: '',
  });

  const collapseTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const avatarInputRef = useRef<HTMLInputElement | null>(null);
  const chatTextareaRef = useRef<HTMLTextAreaElement | null>(null);
  const kbSidebarRef = useRef<KnowledgeSidebarRef>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const pollingRef = useRef<NodeJS.Timeout | null>(null);
  
  // 头像上传状态
  const [uploadingAvatar, setUploadingAvatar] = useState(false);
  
  // 文档拖拽状态
  const [draggedDoc, setDraggedDoc] = useState<any>(null);

  // 加载聊天会话列表
  const loadChatSessions = useCallback(async () => {
    try {
      const response = await api.listChatSessions(1, 50);
      setChatSessions(response.sessions);
    } catch (error) {
      console.error('Failed to load chat sessions:', error);
    }
  }, []);

  // 选择历史会话
  const handleSelectChat = useCallback((chatId: string) => {
    // ✅ 跳转到首页显示历史对话，保持与收藏页面一致的行为
    navigate(`/?chatId=${chatId}`);
  }, [navigate]);

  // ✅ 使用 useCallback 包装回调函数，避免不必要的重新渲染
  const handleError = useCallback((error: string | Error) => {
    // 检查是否为配额超限错误
    if (typeof error === 'object' && (error as any).code === 'QUOTA_EXCEEDED') {
      const details = (error as any).details || {};
      // 显示配额超限弹窗
      setQuotaExceededModal({
        isOpen: true,
        userLevel: details.user_level || 'basic',
        usedTokens: details.used_tokens || 0,
        quotaLimit: details.quota_limit || 0,
        resetDate: details.reset_date || '',
      });
    } else {
      // 检查错误消息是否包含配额相关信息
      const errorStr = String(error);
      if (errorStr.includes('QUOTA_EXCEEDED') || errorStr.includes('配额')) {
        // 尝试解析错误详情
        try {
          const match = errorStr.match(/\{.*\}/);
          if (match) {
            const details = JSON.parse(match[0]);
            setQuotaExceededModal({
              isOpen: true,
              userLevel: details.user_level || 'basic',
              usedTokens: details.used_tokens || 0,
              quotaLimit: details.quota_limit || 0,
              resetDate: details.reset_date || '',
            });
            return;
          }
        } catch {
          // 解析失败，显示弹窗提示
        }
        setQuotaExceededModal({
          isOpen: true,
          userLevel: 'basic',
          usedTokens: 0,
          quotaLimit: 0,
          resetDate: '',
        });
      } else {
        toast.error(`对话错误: ${errorStr}`);
      }
    }
  }, [toast]);

  const handleSessionCreated = useCallback((newSessionId: string) => {
    setCurrentSessionId(newSessionId);
    // 保存到 localStorage
    if (kbId) {
      try {
        localStorage.setItem(`kb_session_${kbId}`, newSessionId);
      } catch (error) {
        console.error('Failed to save session ID to localStorage:', error);
      }
    }
    loadChatSessions();
  }, [kbId, loadChatSessions]);

  const handleFirstContentToken = useCallback((messageId: string) => {
    // 当第一个 content token 到达时，自动折叠 thinking
    setCollapsedThinking(prev => {
      const newSet = new Set(prev);
      newSet.add(messageId);
      return newSet;
    });
  }, []);

  const handleStopComplete = useCallback(() => {
    toast.info('已停止生成');
  }, [toast]);

  // RAG Chat Hook - 知识库页面固定使用 deep 模式
  const { messages, isStreaming, sendMessage, clearMessages, regenerateLastMessage, stopGeneration } = useRAGChat({
    kbId: kbId,           // 传递当前知识库ID
    docIds: kbDocIds,     // 传递该知识库的所有文档ID
    sessionId: currentSessionId,
    mode: 'deep',
    enableWebSearch: true, // 知识库页面默认开启联网搜索
    sourceType: 'knowledge', // 标记为知识库会话
    onError: handleError,
    onSessionCreated: handleSessionCreated,
    onFirstContentToken: handleFirstContentToken,
    onStopComplete: handleStopComplete
  });

  // 格式化相对时间
  const formatRelativeTime = (dateString: string) => {
    const date = new Date(dateString);
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffMins = Math.floor(diffMs / 60000);
    const diffHours = Math.floor(diffMs / 3600000);
    const diffDays = Math.floor(diffMs / 86400000);

    if (diffMins < 1) return '刚刚';
    if (diffMins < 60) return `${diffMins}分钟前`;
    if (diffHours < 24) return `${diffHours}小时前`;
    if (diffDays < 7) return `${diffDays}天前`;
    
    // 超过7天显示具体日期
    return date.toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' });
  };

  useEffect(() => {
    const check = () => setIsMobile(window.innerWidth <= 768);
    check();
    window.addEventListener('resize', check);
    return () => window.removeEventListener('resize', check);
  }, []);

  // 点击外部关闭重新生成菜单
  useEffect(() => {
    const handleClickOutside = () => {
      setShowRegenerateMenu(null);
    };

    if (showRegenerateMenu) {
      const timer = setTimeout(() => {
        document.addEventListener('mousedown', handleClickOutside);
      }, 0);
      return () => {
        clearTimeout(timer);
        document.removeEventListener('mousedown', handleClickOutside);
      };
    }
  }, [showRegenerateMenu]);

  useEffect(() => {
    loadKnowledgeBases();
    loadQuota();
    loadChatSessions();
    loadUserInfo();
  }, []);

  const loadUserInfo = async () => {
    try {
      const cached = localStorage.getItem('userProfile');
      if (cached) {
        const profile = JSON.parse(cached);
        setIsAdmin(profile.is_admin || false);
      }
    } catch (error) {
      console.error('Failed to load user info:', error);
    }
  };

  // ✅ 验证并恢复 localStorage 中的 sessionId
  useEffect(() => {
    if (!hasRestoredSession && chatSessions.length > 0 && kbId) {
      try {
        const savedSessionId = localStorage.getItem(`kb_session_${kbId}`);
        if (savedSessionId) {
          // 验证会话是否存在且属于当前知识库
          const session = chatSessions.find(s => s.id === savedSessionId);
          if (session && session.config?.sourceType === 'knowledge' &&
              session.config?.kbIds?.[0] === kbId) {
            setCurrentSessionId(savedSessionId);
          } else {
            // 会话不存在或不属于当前知识库，清除 localStorage
            try {
              localStorage.removeItem(`kb_session_${kbId}`);
            } catch (removeError) {
              console.error('Failed to remove invalid session from localStorage:', removeError);
            }
          }
        }
      } catch (error) {
        console.error('Failed to restore session from localStorage:', error);
      }
      setHasRestoredSession(true);
    }
  }, [chatSessions.length, hasRestoredSession, kbId]);

  // ✅ 处理 URL 参数中的 chatId，添加验证逻辑
  useEffect(() => {
    const chatIdFromUrl = searchParams.get('chatId');
    if (chatIdFromUrl && chatIdFromUrl !== currentSessionId && chatSessions.length > 0 && kbId) {
      // ✅ 更灵活的会话验证：只验证会话存在性，允许跨页面会话查看
      const session = chatSessions.find(s => s.id === chatIdFromUrl);
      if (session) {
        // 会话存在，允许加载（提供更大的灵活性）
        setCurrentSessionId(chatIdFromUrl);

        // ✅ 只有当会话确实属于当前知识库时才保存到 localStorage
        const isKnowledgeSession = session.config?.sourceType === 'knowledge' &&
                                   session.config?.kbIds?.includes(kbId);
        if (isKnowledgeSession) {
          try {
            localStorage.setItem(`kb_session_${kbId}`, chatIdFromUrl);
          } catch (error) {
            console.error('Failed to save session ID to localStorage:', error);
          }
        }

        console.log(`加载会话 ${chatIdFromUrl}${isKnowledgeSession ? ' (知识库会话)' : ' (跨页面会话)'}`);
      } else {
        console.warn(`会话 ${chatIdFromUrl} 不存在，忽略 URL 参数`);
      }
      // 清除 URL 参数，保持 URL 干净
      setSearchParams({});
    }
  }, [searchParams, currentSessionId, kbId, setSearchParams, chatSessions]);

  useEffect(() => {
    if (kbId) {
      loadCurrentKB();
      loadDocuments();
    }
  }, [kbId]);

  // 轮询检查文档处理状态
  useEffect(() => {
    // 清理之前的轮询
    if (pollingRef.current) {
      clearInterval(pollingRef.current);
      pollingRef.current = null;
    }
    
    if (!kbId) return;
    
    // 检查是否有正在处理中的文档
    const processingStatuses = ['processing', 'uploading', 'chunking', 'embedding'];
    const hasProcessingDocs = documents.some(doc => processingStatuses.includes(doc.status));
    
    if (!hasProcessingDocs) {
      console.log('[Polling] No processing documents, skip polling');
      return;
    }
    
    console.log('[Polling] Found processing documents, starting polling...');
    
    // 启动轮询
    pollingRef.current = setInterval(async () => {
      console.log('[Polling] Fetching document status...');
      try {
        const response = await kbAPI.listDocuments(kbId);
        const prevStatuses = documents.map(d => d.status);
        const newStatuses = response.items.map((doc: any) => doc.status);
        
        setDocuments(response.items);
        
        // 只提取 ready 状态的文档ID用于对话
        const docIds = response.items
          .filter((doc: any) => doc.status === 'ready')
          .map((doc: any) => doc.id);
        setKbDocIds(docIds);
        
        // 检查是否有文档状态从处理中变为完成或失败
        const statusChanged = prevStatuses.some((status, i) => 
          processingStatuses.includes(status) && !processingStatuses.includes(newStatuses[i])
        );
        
        // 如果有状态变化，刷新知识库信息（更新文档计数）
        if (statusChanged) {
          console.log('[Polling] Document status changed, refreshing KB info...');
          loadCurrentKB();
          loadKnowledgeBases();
        }
        
        // 检查是否还有处理中的文档
        const stillProcessing = response.items.some((doc: any) => 
          processingStatuses.includes(doc.status)
        );
        
        if (!stillProcessing && pollingRef.current) {
          console.log('[Polling] All documents processed, stopping polling');
          clearInterval(pollingRef.current);
          pollingRef.current = null;
        }
      } catch (error) {
        console.error('[Polling] Error fetching documents:', error);
      }
    }, 3000);
    
    return () => {
      if (pollingRef.current) {
        console.log('[Polling] Cleanup on unmount');
        clearInterval(pollingRef.current);
        pollingRef.current = null;
      }
    };
  }, [kbId, documents.map(d => `${d.id}:${d.status}`).join(',')]); // 当文档状态变化时重新评估

  // 自动滚动到最新消息
  useEffect(() => {
    if (isStreaming && messagesEndRef.current) {
      requestAnimationFrame(() => {
        messagesEndRef.current?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
      });
    }
  }, [messages, isStreaming]);

  const loadKnowledgeBases = async () => {
    try {
      const response = await kbAPI.listKnowledgeBases();
      setMyKnowledgeBases(response.items);
    } catch (error: any) {
      console.error('Failed to load knowledge bases:', error);
      toast.error(error.message || '加载知识库失败');
    }
  };

  const loadCurrentKB = async () => {
    if (!kbId) return;
    try {
      // 使用 getKnowledgeBaseInfo 支持公开和私有知识库
      const kb = await kbAPI.getKnowledgeBaseInfo(kbId);
      setCurrentKb(kb);
      // 如果是自己的知识库，加载可见性状态
      if (kb.isOwner) {
        await loadVisibilityStatus();
      }
    } catch (error: any) {
      console.error('Failed to load current KB:', error);
      toast.error(error.message || '无法访问该知识库');
      navigate('/knowledge');
    }
  };

  const loadQuota = async () => {
    try {
      const response = await kbAPI.getQuota();
      setQuota(response);
    } catch (error) {
      console.error('Failed to load quota:', error);
    }
  };

  const loadDocuments = async () => {
    if (!kbId) return;
    
    try {
      const response = await kbAPI.listDocuments(kbId);
      setDocuments(response.items);
      
      // 只提取 ready 状态的文档ID用于对话
      const docIds = response.items
        .filter((doc: any) => doc.status === 'ready')
        .map((doc: any) => doc.id);
      setKbDocIds(docIds);
      console.log(`Loaded ${docIds.length} ready documents for KB ${kbId}`);
      
      // 检查文档收藏状态
      if (response.items.length > 0) {
        try {
          const items = response.items.map((doc: any) => ({ type: 'document', id: doc.id }));
          const favoriteStatus = await favoriteAPI.checkFavorites(items);
          const favoritedIds = new Set<string>();
          for (const [key, isFavorited] of Object.entries(favoriteStatus)) {
            if (isFavorited) {
              // key format is "document:docId"
              const docId = key.split(':')[1];
              if (docId) favoritedIds.add(docId);
            }
          }
          setFavoriteDocIds(favoritedIds);
        } catch (error) {
          console.error('Failed to check favorite status:', error);
        }
      }
      
      // 返回文档列表，用于轮询判断
      return response.items;
    } catch (error: any) {
      console.error('Failed to load documents:', error);
      toast.error(error.message || '加载文档失败');
      return [];
    }
  };

  // ============ File Upload Handlers ============

  const handleDragEnter: React.DragEventHandler<HTMLDivElement> = (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (currentKb?.isOwner) setIsDragging(true);
  };

  const handleDragLeave: React.DragEventHandler<HTMLDivElement> = (e) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);
  };

  const handleDrop: React.DragEventHandler<HTMLDivElement> = async (e) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);
    
    if (!currentKb?.isOwner) {
      toast.warning('只有所有者可以上传文档');
      return;
    }
    
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      await handleFilesUpload(Array.from(e.dataTransfer.files));
    }
  };

  const handleOpenPicker = () => fileInputRef.current?.click();

  // 复制消息内容
  const handleCopyMessage = async (content: string, messageId: string) => {
    try {
      // 优先使用现代 Clipboard API
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(content);
      } else {
        // 降级方案：使用传统方法
        const textArea = document.createElement('textarea');
        textArea.value = content;
        textArea.style.position = 'fixed';
        textArea.style.left = '-999999px';
        textArea.style.top = '-999999px';
        document.body.appendChild(textArea);
        textArea.focus();
        textArea.select();
        try {
          document.execCommand('copy');
          textArea.remove();
        } catch (err) {
          console.error('降级复制方法失败:', err);
          textArea.remove();
          throw err;
        }
      }
      // 显示复制成功状态
      setCopiedMessages(prev => new Set(prev).add(messageId));
      // 2秒后恢复
      setTimeout(() => {
        setCopiedMessages(prev => {
          const newSet = new Set(prev);
          newSet.delete(messageId);
          return newSet;
        });
      }, 2000);
    } catch (err) {
      console.error('复制失败:', err);
      toast.error('复制失败，请重试');
    }
  };

  // 点赞消息
  const handleLikeMessage = (messageId: string) => {
    setLikedMessages(prev => {
      const newSet = new Set(prev);
      if (newSet.has(messageId)) {
        newSet.delete(messageId);
      } else {
        newSet.add(messageId);
        setDislikedMessages(prev => {
          const newDisliked = new Set(prev);
          newDisliked.delete(messageId);
          return newDisliked;
        });
      }
      return newSet;
    });
    // TODO: 发送到后端记录
  };

  // 点踩消息
  const handleDislikeMessage = (messageId: string) => {
    setDislikedMessages(prev => {
      const newSet = new Set(prev);
      if (newSet.has(messageId)) {
        newSet.delete(messageId);
      } else {
        newSet.add(messageId);
        setLikedMessages(prev => {
          const newLiked = new Set(prev);
          newLiked.delete(messageId);
          return newLiked;
        });
      }
      return newSet;
    });
    // TODO: 发送到后端记录
  };

  const toggleThinkingCollapse = (messageId: string) => {
    // 清除之前的定时器
    if (collapseTimeoutRef.current) {
      clearTimeout(collapseTimeoutRef.current);
    }
    
    setCollapsedThinking(prev => {
      const newSet = new Set(prev);
      if (newSet.has(messageId)) {
        newSet.delete(messageId);
      } else {
        newSet.add(messageId);
      }
      return newSet;
    });
  };

  // 保存对话到笔记
  const handleSaveToNotes = async (messageId: string) => {
    if (savedToNotes.has(messageId)) {
      toast.info('该对话已保存到笔记');
      return;
    }

    try {
      const result = await saveConversationToNoteById(messages, messageId);

      if (result.success) {
        setSavedToNotes(prev => new Set(prev).add(messageId));
        toast.success('已保存到笔记');
      } else {
        toast.error(result.error || '保存失败');
      }
    } catch (error: any) {
      console.error('保存到笔记失败:', error);
      toast.error('保存失败，请重试');
    }
  };

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files.length > 0) {
      await handleFilesUpload(Array.from(e.target.files));
      e.target.value = '';
    }
  };

  const handleFilesUpload = async (files: File[]) => {
    if (!kbId) {
      toast.warning('请先选择一个知识库');
      return;
    }

    if (!currentKb?.isOwner) {
      toast.warning('只有所有者可以上传文档');
      return;
    }

    setUploading(true);
    try {
      for (const file of files) {
        await kbAPI.uploadDocument(kbId, file);
      }
      await loadDocuments();
      await loadCurrentKB(); // 刷新知识库信息，更新文档数量
      await loadKnowledgeBases(); // 刷新侧边栏知识库列表
      await loadQuota();
      toast.success('文档上传成功！');
    } catch (error: any) {
      toast.error(error.message || '上传失败');
    } finally {
      setUploading(false);
    }
  };

  const handleDeleteDocument = async (docId: string) => {
    if (!kbId) return;
    if (!currentKb?.isOwner) {
      toast.warning('只有所有者可以删除文档');
      return;
    }

    setIsDeleteModalOpen(true);
    // Store docId for confirmation
    (window as any).__pendingDeleteDocId = docId;
  };

  const confirmDeleteDocument = async () => {
    const docId = (window as any).__pendingDeleteDocId;
    if (!docId || !kbId) return;

    try {
      await kbAPI.deleteDocument(kbId, docId);
      await loadDocuments();
      await loadCurrentKB(); // 刷新知识库信息，更新文档数量
      await loadKnowledgeBases(); // 刷新侧边栏知识库列表
      await loadQuota();
      toast.success('文档已删除');
    } catch (error: any) {
      toast.error(error.message || '删除失败');
    } finally {
      setIsDeleteModalOpen(false);
      delete (window as any).__pendingDeleteDocId;
    }
  };

  // 重试处理失败的文档
  const handleRetryDocument = async (docId: string) => {
    if (!kbId) return;
    if (!currentKb?.isOwner) {
      toast.warning('只有所有者可以重新处理文档');
      return;
    }

    try {
      // 立即更新本地状态，让用户看到"处理中"
      setDocuments(prev => prev.map(doc => 
        doc.id === docId ? { ...doc, status: 'processing', errorMessage: null } : doc
      ));
      toast.success('已开始重新处理');
      
      // 调用 API（后端会在后台处理）
      await kbAPI.retryDocument(kbId, docId);
      
      // API 返回后刷新文档列表（此时状态应该已经是 processing）
      await loadDocuments();
    } catch (error: any) {
      // 如果失败，恢复原状态
      await loadDocuments();
      toast.error(error.message || '重新处理失败');
    }
  };

  // ============ Knowledge Base Management ============

  const handleCreateKB = async (data: { name: string; description: string; category: string }) => {
    try {
      await kbAPI.createKnowledgeBase(data.name, data.description, data.category);
      await loadKnowledgeBases();
      toast.success('知识库创建成功！');
    } catch (error: any) {
      toast.error(error.message || '创建知识库失败');
    }
  };

  const handleEditKB = (kb: any) => {
    setIsEditModalOpen(true);
  };

  const handleSaveKB = async (data: { name: string; description: string; category: string }) => {
    if (!kbId) return;
    try {
      await kbAPI.updateKnowledgeBase(kbId, data);
      await loadCurrentKB();
      await loadKnowledgeBases();
      toast.success('知识库已更新');
    } catch (error: any) {
      toast.error(error.message || '更新知识库失败');
    }
  };

  // 处理知识库头像上传
  const handleAvatarUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file || !kbId || !currentKb?.isOwner) return;
    
    // 验证文件类型
    if (!file.type.startsWith('image/')) {
      toast.error('请选择图片文件');
      return;
    }
    
    // 验证文件大小（最大 5MB）
    if (file.size > 5 * 1024 * 1024) {
      toast.error('图片大小不能超过 5MB');
      return;
    }
    
    setUploadingAvatar(true);
    try {
      await kbAPI.uploadAvatar(kbId, file);
      await loadCurrentKB();
      toast.success('头像已更新');
    } catch (error: any) {
      toast.error(error.message || '头像上传失败');
    } finally {
      setUploadingAvatar(false);
      // 清空 input 以便下次选择同一文件
      if (avatarInputRef.current) {
        avatarInputRef.current.value = '';
      }
    }
  };

  // ============ Document Drag & Drop ============
  
  const handleDocDragStart = (e: React.DragEvent, doc: any) => {
    if (!currentKb?.isOwner) return;
    setDraggedDoc(doc);
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('docId', doc.id);
    e.dataTransfer.setData('sourceKbId', kbId || '');
  };

  const handleDocDragEnd = () => {
    setDraggedDoc(null);
  };

  const handleDocumentDrop = async (docId: string, sourceKbId: string, targetKbId: string) => {
    if (!currentKb?.isOwner) {
      toast.warning('只有所有者可以移动文档');
      return;
    }
    
    try {
      const result = await kbAPI.moveDocument(sourceKbId, docId, targetKbId);
      
      // 从当前列表移除文档
      setDocuments(prev => prev.filter(d => d.id !== docId));
      
      // 刷新知识库列表（更新文档计数）
      await loadKnowledgeBases();
      await loadCurrentKB();
      
      const targetKb = myKnowledgeBases.find(kb => kb.id === targetKbId);
      toast.success(`已移动到「${targetKb?.name || '目标知识库'}」`);
    } catch (error: any) {
      toast.error(error.message || '移动文档失败');
    }
  };

  const handleDeleteKB = async () => {
    if (!kbId) return;
    try {
      await kbAPI.deleteKnowledgeBase(kbId);
      toast.success('知识库已删除');
      navigate('/knowledge');
    } catch (error: any) {
      toast.error(error.message || '删除知识库失败');
    }
  };

  // ============ Public/Private Toggle ============

  const handleTogglePublic = () => {
    setIsTogglePublicModalOpen(true);
  };

  const confirmTogglePublic = async () => {
    if (!kbId) return;
    try {
      const result = await kbAPI.togglePublic(kbId);
      await loadCurrentKB();
      const message = result.isPublic 
        ? '知识库已公开到论文广场！' 
        : '知识库已设为私有';
      toast.success(message);
    } catch (error: any) {
      toast.error(error.message || '操作失败');
    } finally {
      setIsTogglePublicModalOpen(false);
    }
  };

  // 共享到组织
  const handleShareBtnClick = () => {
    if (sharedOrgs.length > 0) {
      setIsUnshareModalOpen(true);
    } else {
    setIsShareToOrgModalOpen(true);
    }
  };

  const handleUnshareAll = async () => {
    if (!kbId) return;
    try {
      // 传入空数组，表示设为私有（或清除所有共享组织）
      await kbAPI.updateVisibility(kbId, 'private', []);
      await loadVisibilityStatus();
      await loadCurrentKB();
      toast.success('已取消所有共享');
      setIsUnshareModalOpen(false);
    } catch (error: any) {
      console.error('Failed to unshare:', error);
      toast.error(error.message || '取消共享失败');
    }
  };

  const confirmShareToOrg = async (orgIds: string[]) => {
    if (!kbId) return;
    try {
      // Use updateVisibility to set the complete org list (replaces existing)
      const visibility = orgIds.length > 0 ? 'organization' : 'private';
      await kbAPI.updateVisibility(kbId, visibility, orgIds);
    await loadVisibilityStatus();
      await loadCurrentKB(); // Reload knowledge base info
      // Don't show success toast here - let modal handle it
    } catch (error: any) {
      console.error('Failed to update organization sharing:', error);
      toast.error(error.message || '操作失败');
      throw error; // Re-throw to let modal handle it
    }
  };

  // 加载可见性状态
  const loadVisibilityStatus = async () => {
    if (!kbId) return;
    try {
      const status = await kbAPI.getSharedStatus(kbId);
      setSharedOrgs(status.shared_to_orgs?.map(org => org.id) || []);
    } catch (error) {
      console.error('Failed to load visibility status:', error);
    }
  };

  // ============ Subscribe/Unsubscribe ============

  const handleSubscribe = async () => {
    if (!kbId) return;
    if (currentKb?.isSubscribed) {
      setIsUnsubscribeModalOpen(true);
    } else {
      try {
        await kbAPI.subscribe(kbId);
        await loadCurrentKB();
        // 刷新侧边栏的订阅列表
        await kbSidebarRef.current?.refreshSubscriptions();
        
        // 触发全局事件，通知其他页面刷新订阅列表
        localStorage.setItem('kb_subscription_changed', Date.now().toString());
        window.dispatchEvent(new Event('kb_subscription_changed'));
        
        toast.success('订阅成功！');
      } catch (error: any) {
        toast.error(error.message || '操作失败');
      }
    }
  };

  const confirmUnsubscribe = async () => {
    if (!kbId) return;
    try {
      await kbAPI.unsubscribe(kbId);
      await loadCurrentKB();
      // 刷新侧边栏的订阅列表
      await kbSidebarRef.current?.refreshSubscriptions();
      
      // 触发全局事件，通知其他页面刷新订阅列表
      localStorage.setItem('kb_subscription_changed', Date.now().toString());
      window.dispatchEvent(new Event('kb_subscription_changed'));
      
      toast.success('已取消订阅');
    } catch (error: any) {
      toast.error(error.message || '操作失败');
    } finally {
      setIsUnsubscribeModalOpen(false);
    }
  };

  // ============ Document Preview ============

  // 判断是否为 PDF 文件
  const isPdfFile = (filename: string) => {
    return filename.toLowerCase().endsWith('.pdf');
  };

  const handlePreviewDocument = async (doc: any) => {
    if (!kbId) return;

    setLoadingPreview(true);
    setCurrentSessionId(undefined); // ✅ 重置会话ID，避免与知识库对话混淆
    clearMessages(); // ✅ 清空对话历史，开始新的文档对话
    setPreviewDoc(doc);
    setPreviewUrl('');
    setPreviewContent('');
    setIsMainSidebarCollapsed(true); // 自动折叠主侧边栏

    try {
      // 获取文件 URL（所有格式都需要）
      const urlResponse = await kbAPI.getDocumentUrl(kbId, doc.id);
      setPreviewUrl(urlResponse.url);
      
      // 对于 .doc 文件，额外获取 markdown 内容作为降级方案
      const ext = doc.name.toLowerCase().split('.').pop();
      if (ext === 'doc') {
        try {
          const mdResponse = await kbAPI.getDocumentMarkdown(kbId, doc.id);
          setPreviewContent(mdResponse.content);
        } catch (e) {
          // markdown 获取失败不影响主流程
          console.warn('Failed to get markdown content for .doc file:', e);
        }
      }
    } catch (error: any) {
      toast.error(error.message || '无法加载文档预览');
      setPreviewDoc(null);
      setIsMainSidebarCollapsed(false); // 如果失败，恢复侧边栏
    } finally {
      setLoadingPreview(false);
    }
  };

  const handleClosePreview = () => {
    setPreviewDoc(null);
    setPreviewUrl('');
    setPreviewContent('');
    setCurrentSessionId(undefined); // ✅ 重置会话ID
    clearMessages(); // ✅ 清空对话历史
    setIsMainSidebarCollapsed(false); // 恢复主侧边栏
  };

  // ============ Favorite Document ============

  const handleToggleFavoriteDoc = async (docId: string) => {
    if (!kbId) return;
    
    try {
      if (favoriteDocIds.has(docId)) {
        await favoriteAPI.unfavoriteDocument(docId);
        setFavoriteDocIds(prev => {
          const next = new Set(prev);
          next.delete(docId);
          return next;
        });
        toast.success('已取消收藏');
      } else {
        await favoriteAPI.favoriteDocument(docId, kbId);
        setFavoriteDocIds(prev => new Set(prev).add(docId));
        toast.success('已收藏');
      }
    } catch (error: any) {
      toast.error(error.message || '操作失败');
    }
  };

  // ============ Utilities ============

  const formatBytes = (bytes: number) => {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return (bytes / Math.pow(k, i)).toFixed(2) + ' ' + sizes[i];
  };

  return (
    <div className={styles.page}>
      {isMobile && isSidebarOpen && (
        <div className={styles.overlay} onClick={() => setIsSidebarOpen(false)} />
      )}

      <div className={`${styles.sidebarContainer} ${isMobile && isSidebarOpen ? styles.open : ''}`}>
        <Sidebar
          onNewChat={() => {
            setCurrentSessionId(undefined);
            clearMessages();
            // 清除 localStorage
            if (kbId) {
              localStorage.removeItem(`kb_session_${kbId}`);
            }
          }}
          onSelectChat={handleSelectChat}
          onDeleteChat={async (chatId) => {
            try {
              await api.deleteChatSession(chatId);
              if (chatId === currentSessionId) {
                setCurrentSessionId(undefined);
                clearMessages();
                // 清除 localStorage
                if (kbId) {
                  localStorage.removeItem(`kb_session_${kbId}`);
                }
              }
              await loadChatSessions();
              toast.success('对话已删除');
            } catch (error) {
              console.error('Failed to delete chat:', error);
              toast.error('删除对话失败');
            }
          }}
          selectedChatId={currentSessionId}
          chats={chatSessions}
          collapsed={isMainSidebarCollapsed}
          onToggleCollapse={() => setIsMainSidebarCollapsed(!isMainSidebarCollapsed)}
        />
      </div>

      {/* Modals */}
      <CreateKnowledgeModal
        isOpen={isCreateModalOpen}
        onClose={() => setIsCreateModalOpen(false)}
        onSubmit={handleCreateKB}
      />

      {currentKb && (
        <EditKnowledgeModal
          isOpen={isEditModalOpen}
          onClose={() => setIsEditModalOpen(false)}
          onSave={handleSaveKB}
          initialData={{
            name: currentKb.name,
            description: currentKb.description,
            category: currentKb.category || '其它'
          }}
        />
      )}

      <ConfirmModal
        isOpen={isTogglePublicModalOpen}
        title={currentKb?.isPublic ? "设为私有" : "公开知识库"}
        message={currentKb?.isPublic 
          ? "设为私有后，其他用户将无法访问此知识库。确定继续吗？"
          : "公开后，所有用户都可以查看并订阅此知识库。其他用户无法修改或删除内容。确定公开吗？"
        }
        type={currentKb?.isPublic ? "warning" : "info"}
        confirmText="确认"
        cancelText="取消"
        onConfirm={confirmTogglePublic}
        onCancel={() => setIsTogglePublicModalOpen(false)}
      />

      <ConfirmModal
        isOpen={isUnsubscribeModalOpen}
        title="取消订阅"
        message="确定要取消订阅此知识库吗？取消后将不再显示在「我的订阅」中。"
        type="warning"
        confirmText="确认取消订阅"
        cancelText="取消"
        onConfirm={confirmUnsubscribe}
        onCancel={() => setIsUnsubscribeModalOpen(false)}
      />

      <ConfirmModal
        isOpen={isDeleteModalOpen}
        title="删除文档"
        message="删除后无法恢复，确定要删除这个文档吗？"
        type="danger"
        confirmText="删除"
        cancelText="取消"
        onConfirm={confirmDeleteDocument}
        onCancel={() => setIsDeleteModalOpen(false)}
      />

      <ShareToOrgModal
        isOpen={isShareToOrgModalOpen}
        onClose={() => setIsShareToOrgModalOpen(false)}
        onConfirm={confirmShareToOrg}
        currentlySharedOrgs={sharedOrgs}
      />

      {/* Unshare Confirmation Modal */}
      <ConfirmModal
        isOpen={isUnshareModalOpen}
        onCancel={() => setIsUnshareModalOpen(false)}
        onConfirm={handleUnshareAll}
        title="取消共享"
        message="确定要取消该知识库的共享状态吗？取消后，组织成员将无法再访问此知识库。"
        type="danger"
        confirmText="确认取消"
      />

      <div className={styles.main}>
        <div className={styles.contentArea}>
          {/* Knowledge Sidebar */}
          <KnowledgeSidebar
            ref={kbSidebarRef}
            knowledgeBases={myKnowledgeBases}
            onKnowledgeBasesChange={loadKnowledgeBases}
            onCreateClick={() => setIsCreateModalOpen(true)}
            onEditClick={handleEditKB}
            onDeleteClick={handleDeleteKB}
            onDocumentDrop={handleDocumentDrop}
            currentKbId={kbId}
          />

          {/* Center - KB Info & Upload */}
          <main className={styles.uploadSection}>
            {/* KB Info Card */}
            <div className={styles.kbInfoCard}>
              <div className={styles.kbHeader}>
                <div className={styles.kbInfo}>
                  <div className={styles.kbAvatarSection}>
                    {/* 头像上传 - 只有创建者可以点击上传 */}
                    <div 
                      className={`${styles.kbAvatarWrapper} ${currentKb?.isOwner ? styles.clickable : ''}`}
                      onClick={() => currentKb?.isOwner && avatarInputRef.current?.click()}
                      title={currentKb?.isOwner ? '点击更换头像' : undefined}
                    >
                      <img
                        src={currentKb ? getKnowledgeBaseAvatar(currentKb) : '/kb.png'}
                        alt={currentKb?.name || 'KB'}
                        className={styles.kbAvatar}
                      />
                      {currentKb?.isOwner && (
                        <div className={styles.avatarOverlay}>
                          {uploadingAvatar ? (
                            <Loader2 size={20} className={styles.spinning} />
                          ) : (
                            <Upload size={20} />
                          )}
                        </div>
                      )}
                    </div>
                    {/* 隐藏的头像上传 input */}
                    <input
                      ref={avatarInputRef}
                      type="file"
                      accept="image/*"
                      onChange={handleAvatarUpload}
                      style={{ display: 'none' }}
                    />
                    <div className={styles.kbMeta}>
                      <span>{currentKb?.contents || 0} 文档</span>
                      {currentKb?.isPublic && (
                        <>
                          <span>·</span>
                          <span className={styles.metaItem}><Users size={12} /> {currentKb.subscribersCount || 0} 订阅</span>
                        </>
                      )}
                    </div>
                  </div>
                  <div className={styles.kbDetails}>
                    <div className={styles.kbTitle}>{currentKb?.name || '请选择知识库'}</div>
                    {currentKb?.description && (
                      <div className={styles.kbDescription}>{currentKb.description}</div>
                    )}
                    
                    {/* Action Buttons */}
                    <div className={styles.kbActions}>
                      {currentKb?.isOwner ? (
                        <>
                          {/* 上传按钮 - 只在有文档时显示 */}
                          {documents.length > 0 && (
                            <button 
                              className={styles.iconBtn}
                              onClick={() => fileInputRef.current?.click()}
                              title="上传文件"
                              disabled={uploading}
                            >
                              <Upload size={18} />
                            </button>
                          )}
                          {isAdmin && (
                          <button 
                            className={styles.iconBtn}
                            onClick={handleTogglePublic}
                            title={currentKb.isPublic ? "设为私有" : "公开知识库"}
                          >
                            {currentKb.isPublic ? <Globe size={18} /> : <GlobeLock size={18} />}
                          </button>
                          )}
                          <button 
                            className={`${styles.shareOrgBtn} ${sharedOrgs.length > 0 ? styles.shared : ''}`}
                            onClick={handleShareBtnClick}
                            title={sharedOrgs.length > 0 ? "点击取消共享" : "共享到组织"}
                          >
                            {sharedOrgs.length > 0 ? (
                              <span>已共享</span>
                            ) : (
                              <>
                                <Share2 size={16} />
                                <span>共享到组织</span>
                              </>
                            )}
                          </button>
                          <button 
                            className={styles.iconBtn}
                            onClick={() => handleEditKB(currentKb)}
                            title="编辑知识库"
                          >
                            <Settings size={18} />
                          </button>
                        </>
                      ) : currentKb?.isPublic && (
                        <button 
                          className={`${styles.subscribeBtn} ${currentKb.isSubscribed ? styles.subscribed : ''}`}
                          onClick={handleSubscribe}
                        >
                          {currentKb.isSubscribed ? (
                            <><UserMinus size={16} /> 已订阅</>
                          ) : (
                            <><UserPlus size={16} /> 订阅</>
                          )}
                        </button>
                      )}
                    </div>
                  </div>
                </div>
              </div>

              {/* Hidden file input - always present */}
              {currentKb?.isOwner && (
                <input
                  ref={fileInputRef}
                  type="file"
                  multiple
                  accept=".pdf,.txt,.md,.doc,.docx"
                  onChange={handleFileChange}
                  className={styles.hiddenInput}
                  disabled={uploading}
                />
              )}

              {/* Upload Area - Only show when no documents */}
              {currentKb?.isOwner && documents.length === 0 && (
                <>
                  <div
                    className={`${styles.dropzone} ${isDragging ? styles.dropzoneActive : ''}`}
                    onDragOver={(e) => { e.preventDefault(); e.stopPropagation(); }}
                    onDragEnter={handleDragEnter}
                    onDragLeave={handleDragLeave}
                    onDrop={handleDrop}
                  >
                    <Upload size={60} strokeWidth={1.5} className={styles.uploadIcon} />
                    <div className={styles.dropTitle}>拖拽文件到这里上传</div>
                    <div className={styles.dropHint}>或点击下方按钮选择文件</div>
                  </div>

                  <div className={styles.fileTypeHint}>
                    支持 PDF、TXT、MD、DOC、DOCX 格式，单个文件最大 100MB
                  </div>

                  <div className={styles.uploadActions}>
                    <button 
                      className={styles.uploadButton} 
                      onClick={handleOpenPicker} 
                      disabled={uploading}
                    >
                      {uploading ? <><Loader2 size={16} className="animate-spin" /> 上传中...</> : '上传文件'}
                    </button>
                  </div>
                </>
              )}
            </div>

            {/* Document List - Flat design, not in card */}
            {documents.length > 0 && (
              <div className={styles.documentList}>
                  {documents.map((doc) => (
                    <div 
                      key={doc.id} 
                      className={`${styles.fileRow} ${previewDoc?.id === doc.id ? styles.fileRowActive : ''} ${draggedDoc?.id === doc.id ? styles.fileDragging : ''}`}
                      draggable={currentKb?.isOwner && doc.status === 'ready'}
                      onDragStart={(e) => handleDocDragStart(e, doc)}
                      onDragEnd={handleDocDragEnd}
                    >
                      <img src={getFileIcon(doc.name)} alt="File" width={16} height={16} />
                      <div 
                        className={styles.fileInfo}
                        onClick={() => doc.status === 'ready' && handlePreviewDocument(doc)}
                        style={{ cursor: doc.status === 'ready' ? 'pointer' : 'default' }}
                      >
                        <span className={styles.fileName}>{doc.name}</span>
                        <span className={styles.fileStatus}>
                          {doc.status === 'ready' && `${doc.uploadedAt ? formatRelativeTime(doc.uploadedAt) : ''} · 点击预览`}
                          {doc.status === 'processing' && '处理中...'}
                          {doc.status === 'uploading' && '上传中...'}
                          {doc.status === 'chunking' && '分块中...'}
                          {doc.status === 'embedding' && '向量化中...'}
                          {doc.status === 'failed' && '处理失败'}
                        </span>
                      </div>
                      <div className={styles.fileActions}>
                        {/* 重试按钮 - 只在处理失败时显示 */}
                        {doc.status === 'failed' && currentKb?.isOwner && (
                          <button 
                            className={styles.iconBtn}
                            onClick={(e) => {
                              e.stopPropagation();
                              handleRetryDocument(doc.id);
                            }}
                            title="重新处理"
                          >
                            <RefreshCw size={14} />
                          </button>
                        )}
                        <button 
                          className={`${styles.iconBtn} ${favoriteDocIds.has(doc.id) ? styles.favorited : ''}`}
                          onClick={(e) => {
                            e.stopPropagation();
                            handleToggleFavoriteDoc(doc.id);
                          }}
                          title={favoriteDocIds.has(doc.id) ? "取消收藏" : "收藏文档"}
                        >
                          <Star size={14} fill={favoriteDocIds.has(doc.id) ? 'currentColor' : 'none'} />
                        </button>
                        {currentKb?.isOwner && (
                          <button 
                            className={styles.removeBtn} 
                            onClick={(e) => {
                              e.stopPropagation();
                              handleDeleteDocument(doc.id);
                            }}
                            title="删除文档"
                          >
                            <Trash2 size={14} />
                          </button>
                        )}
                      </div>
                    </div>
                  ))}
              </div>
            )}
          </main>

          {/* Right - Preview/Chat Area */}
          <section className={styles.chatArea}>
            {previewDoc ? (
              // Document Preview
              <div className={styles.previewContainer}>
                {loadingPreview ? (
                  <div className={styles.previewLoading}>
                    <Loader2 size={48} className="animate-spin" />
                    <p>加载文档中...</p>
                  </div>
                ) : previewUrl && isPdfFile(previewDoc.name) ? (
                  // PDF 文件使用 PDFViewer（知识库页面不需要添加到对话功能）
                  <PDFViewer
                    url={previewUrl}
                    fileName={previewDoc.name}
                    onClose={handleClosePreview}
                    onToggleFavorite={() => handleToggleFavoriteDoc(previewDoc.id)}
                    isFavorited={favoriteDocIds.has(previewDoc.id)}
                  />
                ) : previewUrl ? (
                  // 非 PDF 文件使用 DocumentViewer（知识库页面不需要添加到对话功能）
                  <DocumentViewer
                    url={previewUrl}
                    fileName={previewDoc.name}
                    markdownContent={previewContent}
                    onClose={handleClosePreview}
                    onToggleFavorite={() => handleToggleFavoriteDoc(previewDoc.id)}
                    isFavorited={favoriteDocIds.has(previewDoc.id)}
                  />
                ) : (
                  <div className={styles.previewError}>
                    <FileText size={48} />
                    <p>无法加载文档预览</p>
                  </div>
                )}
              </div>
            ) : (
              // Chat Interface
              <div className={styles.chatContainer}>
                <div className={styles.chatHeader}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                    <span>提问本知识库</span>
                  </div>
                  <button
                    className={styles.newChatButton}
                    onClick={() => {
                      setCurrentSessionId(undefined);
                      clearMessages();
                      if (kbId) {
                        localStorage.removeItem(`kb_session_${kbId}`);
                      }
                    }}
                    title="开始新对话"
                  >
                    <PlusCircle size={18} />
                  </button>
                </div>

                <div className={styles.chatContent}>
                  {messages.length === 0 ? (
                    <div className={styles.chatEmpty}>
                      <MessageCircle size={48} className={styles.chatEmptyIcon} />
                      <p className={styles.chatEmptyText}>开始对话，探索知识库的内容</p>
                    </div>
                  ) : (
                    <div className={styles.chatMessages}>
                      {messages.map((msg, index) => (
                        <div 
                          key={msg.id} 
                          className={`${styles.messageItem} ${msg.role === 'user' ? styles.userMessageItem : styles.aiMessageItem}`}
                        >
                        <div className={msg.role === 'user' ? styles.userAvatar : styles.aiAvatar}>
                          {msg.role === 'user' ? (
                            profile?.avatar ? (
                              <img src={profile.avatar} alt="User" className={styles.avatarImage} />
                            ) : (
                              <User size={16} />
                            )
                          ) : (
                            <Sparkles size={16} />
                          )}
                        </div>
                          <div className={styles.messageContentWrapper}>
                            {msg.role === 'assistant' && !msg.content && !msg.thinking && isStreaming && index === messages.length - 1 ? (
                              <div className={styles.thinking}>
                                <div className={styles.thinkingDots}>
                                  <span className={styles.dot}></span>
                                  <span className={styles.dot}></span>
                                  <span className={styles.dot}></span>
                                </div>
                                <span className={styles.thinkingText}>正在思考...</span>
                              </div>
                            ) : (
                              <>
                                {/* 思考过程（仅AI消息且有思考内容时显示） */}
                                {msg.role === 'assistant' && msg.thinking && (
                                  <div className={styles.thinkingProcess}>
                                    <div 
                                      className={styles.thinkingHeader}
                                      onClick={() => toggleThinkingCollapse(msg.id)}
                                      style={{ cursor: 'pointer' }}
                                    >
                                      <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                                        <Sparkles size={14} />
                                        <span>思考过程</span>
                                      </div>
                                      {collapsedThinking.has(msg.id) ? (
                                        <ChevronDown size={16} />
                                      ) : (
                                        <ChevronUp size={16} />
                                      )}
                                    </div>
                                    {!collapsedThinking.has(msg.id) && (
                                      <div className={styles.thinkingContent}>
                                        {msg.thinking}
                                      </div>
                                    )}
                                  </div>
                                )}
                                
                                {/* 如果思考完成但答案未到达，显示生成提示 */}
                                {msg.role === 'assistant' && msg.thinking && !msg.content && isStreaming && index === messages.length - 1 && (
                                  <div className={styles.generatingAnswer}>
                                    <div className={styles.thinkingDots}>
                                      <span className={styles.dot}></span>
                                      <span className={styles.dot}></span>
                                      <span className={styles.dot}></span>
                                    </div>
                                    <span className={styles.thinkingText}>正在生成答案...</span>
                                  </div>
                                )}
                                
                                {/* 最终回答 */}
                                {msg.content && (
                                  <div className={msg.role === 'user' ? styles.userMessageText : styles.aiMessageText}>
                                    {msg.role === 'user' ? (
                                      msg.content
                                    ) : (
                                      <OptimizedMarkdown>
                                        {msg.content}
                                      </OptimizedMarkdown>
                                    )}
                                  </div>
                                )}
                                {/* AI 消息操作按钮 - 只在流式输出完成后显示 */}
                                {msg.role === 'assistant' && msg.content && (!isStreaming || index !== messages.length - 1) && (
                                  <div className={styles.messageActions}>
                                    <button
                                      className={`${styles.actionButton} ${copiedMessages.has(msg.id) ? styles.copied : ''}`}
                                      onClick={() => handleCopyMessage(msg.content, msg.id)}
                                      title={copiedMessages.has(msg.id) ? "已复制" : "复制"}
                                    >
                                      {copiedMessages.has(msg.id) ? <Check size={16} /> : <Copy size={16} />}
                                    </button>
                                    <button
                                      className={`${styles.actionButton} ${likedMessages.has(msg.id) ? styles.liked : ''}`}
                                      onClick={() => handleLikeMessage(msg.id)}
                                      title={likedMessages.has(msg.id) ? "取消点赞" : "点赞"}
                                    >
                                      <ThumbsUp size={16} />
                                    </button>
                                    <button
                                      className={`${styles.actionButton} ${dislikedMessages.has(msg.id) ? styles.disliked : ''}`}
                                      onClick={() => handleDislikeMessage(msg.id)}
                                      title={dislikedMessages.has(msg.id) ? "取消点踩" : "点踩"}
                                    >
                                      <ThumbsDown size={16} />
                                    </button>
                                    <button
                                      className={`${styles.actionButton} ${savedToNotes.has(msg.id) ? styles.saved : ''}`}
                                      onClick={() => handleSaveToNotes(msg.id)}
                                      title={savedToNotes.has(msg.id) ? "已保存到笔记" : "保存到笔记"}
                                    >
                                      <FileText size={16} />
                                    </button>
                                    <div className={styles.regenerateWrapper}>
                                      <button
                                        className={styles.actionButton}
                                        onClick={() => regenerateLastMessage()}
                                        onContextMenu={(e) => {
                                          e.preventDefault();
                                          setShowRegenerateMenu(showRegenerateMenu === msg.id ? null : msg.id);
                                        }}
                                        title="重新生成（右键查看更多选项）"
                                      >
                                        <RefreshCw size={16} />
                                      </button>
                                      {showRegenerateMenu === msg.id && (
                                        <div className={styles.regenerateMenu}>
                                          <button
                                            className={styles.regenerateMenuItem}
                                            onClick={() => {
                                              regenerateLastMessage();
                                              setShowRegenerateMenu(null);
                                            }}
                                          >
                                            🔄 重新生成
                                          </button>
                                          <button
                                            className={styles.regenerateMenuItem}
                                            onClick={() => {
                                              regenerateLastMessage();
                                              setShowRegenerateMenu(null);
                                            }}
                                            title="重新生成文档总结并刷新缓存"
                                          >
                                            🔃 刷新缓存重新生成
                                          </button>
                                        </div>
                                      )}
                                    </div>
                                  </div>
                                )}
                              </>
                            )}
                          </div>
                        </div>
                      ))}
                      <div ref={messagesEndRef} style={{ height: 0, overflow: 'hidden' }} />
                    </div>
                  )}
                </div>

                <div className={styles.chatInputSection}>
                  <div className={styles.inputWrapper}>
                    <div className={styles.inputBox}>
                      <div className={styles.inputRow}>
                        <textarea
                          ref={chatTextareaRef}
                          className={styles.input}
                          placeholder="可对本知识库进行提问..." 
                          value={chatInput}
                          onChange={(e) => setChatInput(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter' && !e.shiftKey && !isStreaming && chatInput.trim()) {
                              e.preventDefault();
                              sendMessage(chatInput);
                              setChatInput('');
                              // 清除配额超限弹窗
                              if (quotaExceededModal.isOpen) {
                                setQuotaExceededModal({ ...quotaExceededModal, isOpen: false });
                              }
                            }
                          }}
                          disabled={isStreaming}
                          rows={1}
                          style={{
                            height: 'auto',
                            minHeight: '24px',
                            maxHeight: '200px'
                          }}
                          onInput={(e) => {
                            const target = e.target as HTMLTextAreaElement;
                            target.style.height = 'auto';
                            target.style.height = target.scrollHeight + 'px';
                          }}
                        />
                        <div className={styles.inputActions}>
                          <SendStopButton
                            isStreaming={isStreaming}
                            disabled={!chatInput.trim()}
                            hasContent={!!chatInput.trim()}
                            onSend={() => {
                              if (chatInput.trim()) {
                                sendMessage(chatInput);
                                setChatInput('');
                                // 清除配额超限弹窗
                                if (quotaExceededModal.isOpen) {
                                  setQuotaExceededModal({ ...quotaExceededModal, isOpen: false });
                                }
                              }
                            }}
                            onStop={stopGeneration}
                          />
                        </div>
                      </div>
                    </div>

                    <div className={styles.disclaimer}>
                      答案由AI生成，仅供参考。
                    </div>
                  </div>
                </div>
              </div>
            )}
          </section>
        </div>
      </div>

      {/* 配额超限弹窗 */}
      <QuotaExceededModal
        isOpen={quotaExceededModal.isOpen}
        onClose={() => setQuotaExceededModal({ ...quotaExceededModal, isOpen: false })}
        onUpgrade={() => {
          // 触发全局事件，通知 Sidebar 打开 ProfileModal
          window.dispatchEvent(new Event('openProfileModal'));
        }}
        userLevel={quotaExceededModal.userLevel}
        usedTokens={quotaExceededModal.usedTokens}
        quotaLimit={quotaExceededModal.quotaLimit}
        resetDate={quotaExceededModal.resetDate}
      />
    </div>
  );
}