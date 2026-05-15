import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';
import { createPortal } from 'react-dom';
import Sidebar from '@/app/components/Sidebar/Sidebar';
import {
  AssistantArtifactList,
  AssistantMessageFlow,
  AssistantMessageInterruption,
  ChatArtifactPreviewPane,
  ChatUIModeSwitch,
  KnowledgeBaseSelector,
  type SelectionState,
  SendStopButton,
  ChatModelSelector,
  ChatImagePreviewList,
  QuotaExceededModal,
} from '@/features/chat/components';
import { initializeEmptySessionRuntime, useRAGChat } from '@/features/chat/hooks/useRAGChat';
import { useChatImageUpload } from '@/features/chat/hooks/useChatImageUpload';
import { resolvePreferredModelName, useChatModels } from '@/features/chat/hooks/useChatModels';
import { useGuestMode } from '@/shared/hooks/useGuestMode';
import { useToast } from '@/shared/hooks/useToast';
import { useUserProfile } from '@/shared/hooks/useUserProfile';
import { api, type ChatAttachment, type ChatRuntimeThreadUploadFile } from '@/shared/api/client';
import type { ChatArtifact } from '@/shared/api/client';
import { getAssistantRenderableText, hasAssistantActionBar, hasAssistantVisiblePayload } from '@/features/chat/lib/assistant-message';
import { assertChatUIMode, type ChatUIMode } from '@/shared/contracts/chat-ui-mode';
import {
  getArtifactPreviewType,
  resolveArtifactName,
  type ChatArtifactPreviewTarget,
} from '@/features/chat/lib/artifact-preview';
import { copyTextToClipboard } from '@/shared/utils/clipboard';
import { saveConversationToNoteById } from '@/shared/utils/noteUtils';
import { getFileIcon } from '@/shared/utils/fileIcons';
import { Menu, ChevronDown, X, Check, Copy, ThumbsUp, ThumbsDown, FileText, Paperclip, RefreshCw, Presentation, PanelsTopLeft, Sparkles, Search, Sheet, ChartColumn, BookOpenText, ScrollText } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import aiAvatarUrl from '@/assets/ai.jpg';
import defaultAvatar from '@/assets/default-avatar.svg';
import Tooltip from '@/shared/components/Tooltip';
import styles from './HomePage.module.css';

// 附件类型定义
interface AttachedFile {
  file: File;
  localId: string;
  attachment?: ChatAttachment;
  status: 'uploading' | 'ready' | 'error';
  progress?: number;
}

const ALLOWED_IMAGE_EXTENSIONS = ['.jpg', '.jpeg', '.png', '.webp'];
const MAX_FILES = 5;
const PLACEHOLDER_SESSION_TITLES = new Set(['新对话', '文件会话']);

const getFileExtension = (file: File) => `.${file.name.split('.').pop()?.toLowerCase() || ''}`;

const isChatImageCandidate = (file: File) =>
  ALLOWED_IMAGE_EXTENSIONS.includes(getFileExtension(file))
  || ['image/jpeg', 'image/png', 'image/webp'].includes(file.type);

const buildRuntimeUploadAttachment = (
  file: File,
  upload: ChatRuntimeThreadUploadFile,
): ChatAttachment => ({
  attachment_id: `runtime-upload:${upload.filename}`,
  name: file.name,
  object_path: upload.virtual_path.replace(/^\/+/u, ''),
  workspace_path: `uploads/${upload.filename}`,
  mime_type: file.type || undefined,
  source_kind: 'user_upload',
  role: 'source',
  input_mode: 'workspace_file',
  size_bytes: upload.size,
  parse_status: 'ready',
  metadata: {
    runtime_upload: {
      filename: upload.filename,
      original_name: file.name,
      size: upload.size,
      path: upload.path,
      virtual_path: upload.virtual_path,
      artifact_url: upload.artifact_url,
      markdown_file: upload.markdown_file,
      markdown_path: upload.markdown_path,
      markdown_virtual_path: upload.markdown_virtual_path,
      markdown_artifact_url: upload.markdown_artifact_url,
    },
  },
});

const getRuntimeUploadFilename = (attachment?: ChatAttachment): string | null => {
  const metadata = attachment?.metadata;
  if (!metadata || typeof metadata !== 'object' || Array.isArray(metadata)) {
    return null;
  }
  const runtimeUpload = (metadata as Record<string, unknown>).runtime_upload;
  if (!runtimeUpload || typeof runtimeUpload !== 'object' || Array.isArray(runtimeUpload)) {
    return null;
  }
  const filename = String((runtimeUpload as Record<string, unknown>).filename || '').trim();
  return filename || null;
};

interface RestorableSessionDocument {
  id: string;
}

interface RestorableChatSessionConfig {
  uiMode: ChatUIMode;
  kbIds?: string[];
  docIds?: string[];
  modelName?: string;
  isKBLocked?: boolean;
  sourceType?: string;
}

interface RestorableChatSession {
  id: string;
  title: string;
  lastMessage: string;
  timestamp: string;
  createdAt: string;
  config?: RestorableChatSessionConfig;
}

interface KnowledgeBaseOption {
  id: string;
  name: string;
  avatar?: string;
  contents?: number;
  description?: string;
  category?: string;
}

interface QuotaExceededLikeErrorDetails {
  user_level?: string;
  used_tokens?: number;
  quota_limit?: number;
  reset_date?: string;
}

interface QuotaExceededLikeError extends Error {
  code?: string;
  details?: QuotaExceededLikeErrorDetails;
}

interface TaskShortcut {
  id: string;
  label: string;
  prompt: string;
  icon: LucideIcon;
}

interface TaskMenuPosition {
  left: number;
  width: number;
  maxHeight: number;
  top?: number;
  bottom?: number;
}

const PRIMARY_TASK_SHORTCUTS: TaskShortcut[] = [
  {
    id: 'slides',
    label: '制作幻灯片',
    prompt: '请基于我的主题或资料，制作一份结构清晰、适合直接汇报的 PPT，要求PPT页数不少于10页，使用中文。',
    icon: Presentation,
  },
  {
    id: 'deep-report',
    label: '生成深度研究报告',
    prompt: '请围绕这个主题先完成系统化调研，再生成一份结构完整的深度研究报告。',
    icon: Search,
  },
  {
    id: 'review',
    label: '生成文献综述',
    prompt: '请基于我提供的论文或资料，写一篇结构化文献综述。',
    icon: BookOpenText,
  },
  {
    id: 'website',
    label: '创建网页',
    prompt: '请帮我创建一个极简但有设计感的网页，主要功能是一个抽奖系统。',
    icon: PanelsTopLeft,
  },
];

const MORE_TASK_SHORTCUTS: TaskShortcut[] = [
  {
    id: 'summary',
    label: '多文档总结',
    prompt: '请对这批资料做主题化综合总结，生成最终的总结报告。',
    icon: FileText,
  },
  {
    id: 'visualization',
    label: '生成图表',
    prompt: '请根据我的数据或结论，推荐合适的图表，并输出可视化的结果文件。',
    icon: ChartColumn,
  },
  {
    id: 'spreadsheet',
    label: 'excel/csv分析',
    prompt: '请帮我对这份 Excel 或 CSV 做结构化分析，先梳理字段、分析维度、关键发现和建议输出结果。',
    icon: Sheet,
  },
  {
    id: 'blog',
    label: '生成播客',
    prompt: '请把这份内容改写成适合收听的播客内容，优先输出自然的双人对谈脚本，并兼顾后续音频生成。',
    icon: ScrollText,
  },
  {
    id: 'creative',
    label: '其它创造力的任务',
    prompt: '输入你其它更有创造力的任务需求吧',
    icon: Sparkles,
  },
];

// 格式化文件大小
const formatFileSize = (bytes: number) => {
  if (bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
};

const buildSessionTitleFromMessage = (content: string) => {
  const normalized = content.trim();
  if (!normalized) {
    return '新对话';
  }
  return normalized.slice(0, 30) + (normalized.length > 30 ? '...' : '');
};

const resolveAttachmentStatusLabel = (attachment: ChatAttachment): string | null => {
  const metadata = attachment.metadata;
  const kbProjection = (
    metadata
    && typeof metadata === 'object'
    && !Array.isArray(metadata)
    && metadata.kb_projection
    && typeof metadata.kb_projection === 'object'
    && !Array.isArray(metadata.kb_projection)
  ) ? (metadata.kb_projection as Record<string, unknown>) : null;
  const kbStatus = String(kbProjection?.status || '').trim().toLowerCase();

  if (attachment.parse_status === 'failed' || kbStatus === 'failed') {
    return '处理失败';
  }
  if (attachment.parse_status === 'pending' || kbStatus === 'pending') {
    return '处理中';
  }
  if (attachment.parse_status === 'ready' || kbStatus === 'ready') {
    return null;
  }
  return null;
};

const resolveAttachmentStatusTone = (attachment: ChatAttachment) => {
  const label = resolveAttachmentStatusLabel(attachment);
  if (label === '处理失败') {
    return 'error';
  }
  if (label === '处理中') {
    return 'pending';
  }
  return 'neutral';
};

const isInlineImageWorkspaceAttachment = (attachment: ChatAttachment) => {
  const metadata = attachment.metadata;
  if (!metadata || typeof metadata !== 'object' || Array.isArray(metadata)) {
    return false;
  }
  return String((metadata as Record<string, unknown>).origin || '').trim().toLowerCase() === 'image_data_url';
};

export default function Home() {
  const toast = useToast();
  const { isGuestMode, hasReachedGuestMessageLimit, consumeGuestMessage, promptLogin } = useGuestMode();
  const { profile } = useUserProfile();
  const welcomePrefixText = useMemo(() => {
    const name = (profile?.name || '').trim();
    if (!name) return '用';
    const safeName = name.length > 16 ? `${name.slice(0, 16)}...` : name;
    return `${safeName}，用`;
  }, [profile?.name]);
  const [searchParams, setSearchParams] = useSearchParams();
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);
  const [isMobile, setIsMobile] = useState(false);
  const [uiMode, setUiMode] = useState<ChatUIMode>('normal');
  const [selectedModelName, setSelectedModelName] = useState<string | undefined>(undefined);
  const [inputMessage, setInputMessage] = useState('');
  const [attachedFiles, setAttachedFiles] = useState<AttachedFile[]>([]);
  const removedAttachedFileIdsRef = useRef<Set<string>>(new Set());
  const fileInputRef = useRef<HTMLInputElement>(null);
  const composerInputRef = useRef<HTMLTextAreaElement>(null);
  const taskMenuRef = useRef<HTMLDivElement>(null);
  const taskMenuButtonRef = useRef<HTMLButtonElement>(null);
  const taskMenuPopupRef = useRef<HTMLDivElement>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const chatContainerRef = useRef<HTMLDivElement>(null);
  const shouldAutoScrollRef = useRef(true);
  const [isTaskMenuOpen, setIsTaskMenuOpen] = useState(false);
  const [taskMenuPosition, setTaskMenuPosition] = useState<TaskMenuPosition | null>(null);

  useEffect(() => {
    if (inputMessage !== '') {
      return;
    }

    const input = composerInputRef.current;
    if (!input) {
      return;
    }

    input.style.height = '36px';
  }, [inputMessage]);

  // 处理滚动事件
  const handleScroll = useCallback(() => {
    if (chatContainerRef.current) {
      const { scrollTop, scrollHeight, clientHeight } = chatContainerRef.current;
      const isAtBottom = scrollHeight - scrollTop - clientHeight < 100;
      shouldAutoScrollRef.current = isAtBottom;
    }
  }, []);

  const scrollToBottom = useCallback((behavior: ScrollBehavior = 'auto') => {
    if (chatContainerRef.current) {
      chatContainerRef.current.scrollTo({
        top: chatContainerRef.current.scrollHeight,
        behavior,
      });
      return;
    }
    messagesEndRef.current?.scrollIntoView({ behavior, block: 'end' });
  }, []);

  const [chatSessions, setChatSessions] = useState<RestorableChatSession[]>([]);
  const [currentSessionId, setCurrentSessionId] = useState<string | undefined>(undefined);
  const [hasRestoredSession, setHasRestoredSession] = useState(false);
  const sessionRestoreSequenceRef = useRef(0);
  
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
  const [activeArtifactPreview, setActiveArtifactPreview] = useState<ChatArtifactPreviewTarget | null>(null);
  
  // 处理文件上传点击
  const handleUploadClick = () => {
    if (isGuestMode) {
      promptLogin({
        title: '登录后可上传文件',
        message: '游客模式下可以浏览页面和发送 3 条消息，上传文件需要先登录。',
        confirmText: '去登录',
      });
      return;
    }

    if (fileInputRef.current) {
      fileInputRef.current.click();
    }
  };

  // 处理文件移除
  const handleRemoveFile = useCallback(async (targetFile: AttachedFile) => {
    removedAttachedFileIdsRef.current.add(targetFile.localId);
    setAttachedFiles((prev) => prev.filter((item) => item.localId !== targetFile.localId));
    if (fileInputRef.current) {
      fileInputRef.current.value = '';
    }

    const runtimeFilename = getRuntimeUploadFilename(targetFile.attachment);
    if (!runtimeFilename || !currentSessionId) {
      return;
    }

    try {
      await api.deleteChatRuntimeThreadFile(currentSessionId, runtimeFilename);
      removedAttachedFileIdsRef.current.delete(targetFile.localId);
    } catch (error) {
      removedAttachedFileIdsRef.current.delete(targetFile.localId);
      console.error('Failed to delete removed thread upload:', error);
      setAttachedFiles((prev) => {
        if (prev.some((item) => item.localId === targetFile.localId)) {
          return prev;
        }
        return [...prev, targetFile];
      });
      toast.error(`移除文件失败：${targetFile.file.name}`);
    }
  }, [currentSessionId, toast]);

  // 消息反馈状态
  const [likedMessages, setLikedMessages] = useState<Set<string>>(new Set());
  const [dislikedMessages, setDislikedMessages] = useState<Set<string>>(new Set());
  const [savedToNotes, setSavedToNotes] = useState<Set<string>>(new Set()); // 已保存到笔记的消息ID
  const [copiedMessages, setCopiedMessages] = useState<Set<string>>(new Set()); // 已复制的消息ID
  const [showRegenerateMenu, setShowRegenerateMenu] = useState<string | null>(null); // 显示重新生成菜单的消息ID
  
  // 知识库选择相关状态
  const [showKBSelector, setShowKBSelector] = useState(false);
  const [selectedKBs, setSelectedKBs] = useState<string[]>([]);
  const [myKBs, setMyKBs] = useState<KnowledgeBaseOption[]>([]);
  const [favoriteKBs, setFavoriteKBs] = useState<KnowledgeBaseOption[]>([]);
  const [, setLoadingKBs] = useState(false);
  const [isKBLocked, setIsKBLocked] = useState(false); // 知识库是否已锁定
  const kbButtonRef = useRef<HTMLButtonElement>(null); // 知识库按钮 ref
  const kbButtonRef2 = useRef<HTMLButtonElement>(null); // 对话界面的知识库按钮 ref
  const [kbPanelPosition, setKbPanelPosition] = useState<React.CSSProperties | null>(null);
  
  // 知识库文档缓存（用于会话恢复）
  const [, setKbDocuments] = useState<Record<string, RestorableSessionDocument[]>>({});
  
  // 所有选中知识库的文档ID及其所属知识库映射
  const [selectedDocIds, setSelectedDocIds] = useState<string[]>([]);
  const [docToKbMap, setDocToKbMap] = useState<Record<string, string>>({});

  // 智能传递参数：
  // - 如果选择了知识库 → 传递所有文档ID（让后端判断单文档/多文档）
  // - 单文档 → 传递该文档所属的kb_id（后端会获取markdown content）
  // - 多文档 → 传递第一个知识库的ID（用于权限验证）
  // - 其他情况 → 后端使用召回模式
  const hasSelectedKB = selectedKBs.length > 0;
  
  // 确定传递哪个kb_id
  // ✅ 使用 useMemo 缓存 kbIdToPass 计算，避免每次渲染都重新计算
  const kbIdToPass = useMemo(() => {
    if (selectedKBs.length === 1) {
      // 单知识库：直接传该知识库ID
      return selectedKBs[0];
    } else if (selectedDocIds.length === 1 && docToKbMap[selectedDocIds[0]]) {
      // 多知识库但只有1个文档：传该文档所属的知识库ID（用于获取content）
      return docToKbMap[selectedDocIds[0]];
    } else if (selectedKBs.length > 1) {
      // 多知识库多文档：传第一个知识库ID（用于权限验证）
      return selectedKBs[0];
    }
    return undefined;
  }, [selectedKBs, selectedDocIds, docToKbMap]);

  // 加载聊天会话列表
  const loadChatSessions = useCallback(async () => {
    if (isGuestMode) {
      setChatSessions([]);
      return;
    }

    try {
      const response = await api.listChatSessions(1, 50);
      setChatSessions(response.sessions);
    } catch (error) {
      console.error('Failed to load chat sessions:', error);
    }
  }, [isGuestMode]);

  // ✅ 使用 useCallback 包装回调函数，避免不必要的重新渲染
  const handleError = useCallback((error: string | Error) => {
    // 检查是否为配额超限错误
    if (typeof error === 'object' && (error as QuotaExceededLikeError).code === 'QUOTA_EXCEEDED') {
      const details = (error as QuotaExceededLikeError).details || {};
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
    try {
      localStorage.setItem('home_last_session_id', newSessionId);
    } catch (error) {
      console.error('Failed to save session ID to localStorage:', error);
    }
    loadChatSessions();
  }, [loadChatSessions]);

  const {
    messages,
    isStreaming,
    isLoading,
    sendMessage,
    clearMessages,
    regenerateLastMessage,
    stopGeneration,
  } = useRAGChat({
    sessionId: currentSessionId,
    kbId: kbIdToPass,                                   // 智能传递kb_id
    docIds: hasSelectedKB ? selectedDocIds : undefined, // 选择了知识库时传所有doc_ids
    modelName: selectedModelName,
    uiMode,
    sourceType: 'home',                                 // 标记为首页会话
    onError: handleError,
    onSessionCreated: handleSessionCreated,
    onStopComplete: () => toast.info('已停止生成')      // 停止生成完成时显示提示
  });

  const {
    chatImages,
    isImageDragOver,
    maxChatImages,
    appendChatImages,
    handleRemoveChatImage,
    handleImageDragOver,
    handleImageDragLeave,
    clearChatImages,
  } = useChatImageUpload({
    isStreaming,
    onError: (message) => toast.error(message),
  });

  const uploadSelectedFiles = useCallback(async (selectedFiles: File[]) => {
    if (isGuestMode) {
      promptLogin({
        title: '登录后可上传文件',
        message: '游客模式下可以浏览页面和发送 3 条消息，上传文件需要先登录。',
        confirmText: '去登录',
      });
      return;
    }

    if (selectedFiles.length === 0) return;

    const imageFiles: File[] = [];
    const documentFiles: File[] = [];

    for (const file of selectedFiles) {
      if (isChatImageCandidate(file)) {
        imageFiles.push(file);
      } else {
        documentFiles.push(file);
      }
    }

    if (imageFiles.length > 0) {
      await appendChatImages(imageFiles);
    }

    if (documentFiles.length === 0) {
      return;
    }

    const currentCount = attachedFiles.length;
    const newFilesCount = documentFiles.length;
    if (currentCount + newFilesCount > MAX_FILES) {
      toast.error(`最多只能上传 ${MAX_FILES} 个文件，当前已有 ${currentCount} 个`);
      return;
    }

    const ensureSessionForThreadUpload = async () => {
      if (currentSessionId) {
        return currentSessionId;
      }

      const sessionConfig = {
        uiMode,
        kbIds: selectedKBs,
        docIds: selectedDocIds,
        sourceType: 'home' as const,
        isKBLocked: selectedKBs.length > 0 || selectedDocIds.length > 0,
        modelName: selectedModelName,
      };
      const session = await api.createEmptyChatSession(sessionConfig);
      initializeEmptySessionRuntime(session.id);
      setCurrentSessionId(session.id);
      setChatSessions(prev => [
        session as RestorableChatSession,
        ...prev.filter(item => item.id !== session.id),
      ]);
      try {
        localStorage.setItem('home_last_session_id', session.id);
      } catch (error) {
        console.error('Failed to save session ID to localStorage:', error);
      }
      return session.id;
    };

    let sessionId: string;
    try {
      sessionId = await ensureSessionForThreadUpload();
    } catch (error) {
      console.error('Failed to create session for thread upload:', error);
      toast.error('初始化文件会话失败，请重试');
      return;
    }

    for (const file of documentFiles) {
      const localId = `temp_${Date.now()}_${Math.random().toString(36).slice(2, 9)}`;
      const newFileRecord: AttachedFile = {
        file,
        localId,
        status: 'uploading',
      };
      setAttachedFiles(prev => [...prev, newFileRecord]);

      try {
        setAttachedFiles(prev => prev.map(item =>
          item.localId === localId ? { ...item, progress: 1 } : item
        ));

        const response = await api.uploadChatRuntimeThreadFiles(sessionId, [file]);
        const uploaded = response.files.find((item) => item.filename === file.name) || response.files[0];
        if (!uploaded) {
          throw new Error('runtime 未返回上传结果');
        }
        const attachment = buildRuntimeUploadAttachment(file, uploaded);
        const wasRemovedDuringUpload = removedAttachedFileIdsRef.current.has(localId);
        if (wasRemovedDuringUpload) {
          try {
            await api.deleteChatRuntimeThreadFile(sessionId, uploaded.filename);
          } catch (cleanupError) {
            console.error('Failed to delete removed in-flight upload:', cleanupError);
            setAttachedFiles((prev) => [
              ...prev,
              { ...newFileRecord, attachment, status: 'ready', progress: 100 },
            ]);
            toast.error(`文件已上传，但撤销失败：${file.name}`);
          } finally {
            removedAttachedFileIdsRef.current.delete(localId);
          }
          continue;
        }

        setAttachedFiles(prev => prev.map(item =>
          item.localId === localId
            ? { ...item, attachment, status: 'ready', progress: 100 }
            : item
        ));
      } catch (error: unknown) {
        removedAttachedFileIdsRef.current.delete(localId);
        console.error('Failed to upload file:', error);
        const message = error instanceof Error ? error.message : '请重试';
        toast.error(`${file.name} 上传失败：${message}`);
        setAttachedFiles(prev => prev.map(item =>
          item.localId === localId ? { ...item, status: 'error', progress: undefined } : item
        ));
      }
    }
  }, [
    appendChatImages,
    attachedFiles.length,
    currentSessionId,
    isGuestMode,
    promptLogin,
    selectedKBs,
    selectedDocIds,
    selectedModelName,
    toast,
    uiMode,
  ]);

  const handleFileSelect = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files || files.length === 0) return;

    const selectedFiles = Array.from(files);
    e.target.value = '';
    await uploadSelectedFiles(selectedFiles);
  };

  const handleComposerDrop = async (e: React.DragEvent<HTMLDivElement>) => {
    handleImageDragLeave(e);
    if (isStreaming) return;
    await uploadSelectedFiles(Array.from(e.dataTransfer.files || []));
  };

  const {
    models: chatModels,
    defaultModelName,
  } = useChatModels();

  useEffect(() => {
    const nextModelName = resolvePreferredModelName(chatModels, selectedModelName, {
      defaultModelName,
      requireVision: chatImages.length > 0,
    });
    if (!nextModelName || nextModelName === selectedModelName) {
      return;
    }

    const previousModelName = (selectedModelName || '').trim();
    setSelectedModelName(nextModelName);

    if (!currentSessionId || !previousModelName) {
      return;
    }

    void api.updateChatSessionConfig(currentSessionId, {
      uiMode,
      modelName: nextModelName,
    }).catch((error) => {
      console.error('Failed to normalize stale session model config:', error);
    });
  }, [chatImages.length, chatModels, currentSessionId, defaultModelName, selectedModelName, uiMode]);

  // 加载知识库
  const loadKnowledgeBases = async () => {
    if (isGuestMode) {
      setMyKBs([]);
      setFavoriteKBs([]);
      setLoadingKBs(false);
      return;
    }

    setLoadingKBs(true);
    try {
      const [myKBResponse, favoriteKBResponse] = await Promise.all([
        api.listKnowledgeBases(undefined, 1, 50),
        api.listFavoriteKBs(1, 50)
      ]);
      setMyKBs(myKBResponse.items || []);
      setFavoriteKBs(favoriteKBResponse.items || []);
    } catch (error) {
      console.error('Failed to load knowledge bases:', error);
      toast.error('加载知识库失败');
    } finally {
      setLoadingKBs(false);
    }
  };

  // 打开知识库选择器时加载知识库并计算位置
  const handleOpenKBSelector = (buttonRef?: React.RefObject<HTMLButtonElement>) => {
    if (isGuestMode) {
      promptLogin({
        title: '登录后可选择知识库',
        message: '游客模式下暂不支持绑定知识库，请先登录。',
        confirmText: '去登录',
      });
      return;
    }

    const ref = buttonRef || kbButtonRef;
    
    if (ref?.current) {
      const rect = ref.current.getBoundingClientRect();
      const viewportHeight = window.innerHeight;
      const spaceAbove = rect.top;
      const spaceBelow = viewportHeight - rect.bottom;
      
      // 优先显示在上方，除非上方空间太小（小于300px）且下方空间更多
      // 使用 bottom 定位可以实现"从下往上长"的效果，避免高度不足时的悬空问题
      const showAbove = spaceAbove > 300 || spaceAbove > spaceBelow;
      
      const newPosition: React.CSSProperties = {
        left: Math.max(16, rect.left - 50), // 稍微向左偏移
      };

      if (showAbove) {
        // 显示在上方：定位到底部 = 视口高度 - 按钮顶部 + 间距
        newPosition.bottom = viewportHeight - rect.top + 8;
        newPosition.maxHeight = Math.min(400, spaceAbove - 16); // 留出一点顶部边距
      } else {
        // 显示在下方
        newPosition.top = rect.bottom + 8;
        newPosition.maxHeight = Math.min(400, spaceBelow - 16); // 留出一点底部边距
      }

      setKbPanelPosition(newPosition);
      setShowKBSelector(true);
      if (myKBs.length === 0 && favoriteKBs.length === 0) {
        loadKnowledgeBases();
      }
    }
  };

  useEffect(() => {
    const checkMobile = () => {
      const isNarrowViewport = window.innerWidth <= 768;
      const isTouchDevice = window.matchMedia('(pointer: coarse)').matches;
      setIsMobile(isNarrowViewport && isTouchDevice);
    };
    checkMobile();
    window.addEventListener('resize', checkMobile);
    return () => window.removeEventListener('resize', checkMobile);
  }, []);

  useEffect(() => {
    if (!isTaskMenuOpen) {
      setTaskMenuPosition(null);
      return undefined;
    }

    const handlePointerDown = (event: MouseEvent) => {
      if (taskMenuRef.current && taskMenuRef.current.contains(event.target as Node)) {
        return;
      }
      if (taskMenuPopupRef.current && taskMenuPopupRef.current.contains(event.target as Node)) {
        return;
      }
      setIsTaskMenuOpen(false);
    };

    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setIsTaskMenuOpen(false);
      }
    };

    document.addEventListener('mousedown', handlePointerDown);
    document.addEventListener('keydown', handleEscape);

    return () => {
      document.removeEventListener('mousedown', handlePointerDown);
      document.removeEventListener('keydown', handleEscape);
    };
  }, [isTaskMenuOpen]);

  useEffect(() => {
    if (!isTaskMenuOpen) {
      return undefined;
    }

    const updateTaskMenuPosition = () => {
      const button = taskMenuButtonRef.current;
      if (!button) {
        return;
      }

      const rect = button.getBoundingClientRect();
      const viewportPadding = 16;
      const gap = 12;
      const width = Math.min(318, window.innerWidth - viewportPadding * 2);
      const left = Math.min(
        Math.max(viewportPadding, rect.right - width),
        window.innerWidth - width - viewportPadding,
      );
      const spaceAbove = rect.top - viewportPadding - gap;
      const spaceBelow = window.innerHeight - rect.bottom - viewportPadding - gap;
      const openUpward = spaceAbove >= spaceBelow;
      const maxHeight = Math.max(
        180,
        Math.min(420, openUpward ? spaceAbove : spaceBelow),
      );

      if (openUpward) {
        setTaskMenuPosition({
          left,
          width,
          maxHeight,
          bottom: window.innerHeight - rect.top + gap,
        });
        return;
      }

      setTaskMenuPosition({
        left,
        width,
        maxHeight,
        top: rect.bottom + gap,
      });
    };

    updateTaskMenuPosition();

    window.addEventListener('resize', updateTaskMenuPosition);
    window.addEventListener('scroll', updateTaskMenuPosition, true);

    return () => {
      window.removeEventListener('resize', updateTaskMenuPosition);
      window.removeEventListener('scroll', updateTaskMenuPosition, true);
    };
  }, [isTaskMenuOpen]);

  // 点击外部关闭重新生成菜单
  const regenerateMenuRef = useRef<HTMLDivElement>(null);
  
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      // 检查点击是否在菜单内部
      if (regenerateMenuRef.current && regenerateMenuRef.current.contains(event.target as Node)) {
        // 点击在菜单内部，不关闭
        return;
      }
      // 点击在菜单外部，关闭菜单
      setShowRegenerateMenu(null);
    };

    if (showRegenerateMenu) {
      // 使用 setTimeout 延迟添加监听器，避免立即触发
      const timer = setTimeout(() => {
        document.addEventListener('mousedown', handleClickOutside);
      }, 0);
      return () => {
        clearTimeout(timer);
        document.removeEventListener('mousedown', handleClickOutside);
      };
    }
  }, [showRegenerateMenu]);

  // 加载历史会话
  useEffect(() => {
    loadChatSessions();
  }, [loadChatSessions]);

  // ✅ 会话恢复的统一处理函数（必须在 useEffect 之前定义）
  const handleSessionRestore = useCallback(async (session: RestorableChatSession, sessionId: string) => {
    // ✅ 移除强制跳转逻辑，允许用户在任何页面查看任何会话
    // 这样提供更大的灵活性，用户可以自由选择在哪里查看会话

    const restoreSequence = ++sessionRestoreSequenceRef.current;
    const config = session?.config;
    let restoredUIMode: ChatUIMode;
    try {
      restoredUIMode = assertChatUIMode(config?.uiMode, 'session.config.uiMode');
    } catch (error) {
      const message = error instanceof Error ? error.message : '会话缺少合法的 uiMode';
      toast.error(`会话配置无效: ${message}`);
      return;
    }
    const nextSelectedKBs = Array.isArray(config?.kbIds) ? config.kbIds : [];
    const nextSelectedDocIds = Array.isArray(config?.docIds) ? config.docIds : [];

    // 先同步基础状态，避免旧会话配置短暂残留在界面上。
    setUiMode(restoredUIMode);
    setSelectedKBs(nextSelectedKBs);
    setSelectedDocIds(nextSelectedDocIds);
    setSelectedModelName(config?.modelName);
    setIsKBLocked(config?.isKBLocked === true);
    setKbDocuments({});
    setDocToKbMap({});

    setCurrentSessionId(sessionId);

    if (nextSelectedKBs.length === 0) {
      return;
    }

    const newDocToKbMap: Record<string, string> = {};
    const newKbDocuments: Record<string, RestorableSessionDocument[]> = {};

    await Promise.all(nextSelectedKBs.map(async (kbId) => {
      try {
        const response = await api.listDocuments(kbId, 1, 100);
        const docs = (response.items || []) as RestorableSessionDocument[];
        newKbDocuments[kbId] = docs;
        docs.forEach((doc) => {
          newDocToKbMap[doc.id] = kbId;
        });
      } catch (error) {
        console.error(`Failed to load documents for kb ${kbId}:`, error);
      }
    }));

    if (sessionRestoreSequenceRef.current !== restoreSequence) {
      return;
    }

    setKbDocuments(newKbDocuments);
    setDocToKbMap(newDocToKbMap);

    // 注意：不在这里清除 URL 参数，统一在调用方（useEffect）中处理
    // 避免重复调用 setSearchParams
  }, [toast]);

  const handleModelChange = useCallback(async (nextModelName: string) => {
    setSelectedModelName(nextModelName);
    if (!currentSessionId) {
      return;
    }

    try {
      await api.updateChatSessionConfig(currentSessionId, {
        uiMode,
        modelName: nextModelName,
      });
    } catch (error) {
      console.error('Failed to update session model config:', error);
      toast.error('模型设置保存失败');
    }
  }, [currentSessionId, toast, uiMode]);

  const handleTaskShortcutSelect = useCallback((prompt: string) => {
    setInputMessage(prompt);
    setIsTaskMenuOpen(false);

    requestAnimationFrame(() => {
      const input = composerInputRef.current;
      if (!input) {
        return;
      }

      input.focus();
      input.style.height = 'auto';
      input.style.height = `${Math.max(input.scrollHeight, 36)}px`;

      const caretPosition = prompt.length;
      input.setSelectionRange(caretPosition, caretPosition);
    });
  }, []);

  const handleUIModeChange = useCallback(async (nextMode: ChatUIMode) => {
    if (nextMode === uiMode) {
      return;
    }

    const previousMode = uiMode;
    setUiMode(nextMode);

    if (!currentSessionId) {
      return;
    }

    try {
      await api.updateChatSessionConfig(currentSessionId, { uiMode: nextMode });
    } catch (error) {
      console.error('Failed to update session ui mode:', error);
      toast.error('模式设置保存失败');
      setUiMode(previousMode);
    }
  }, [currentSessionId, toast, uiMode]);

  // 处理知识库选择变化的回调
  const handleKBSelectionChange = useCallback((selection: SelectionState) => {
    setSelectedKBs(selection.selectedKBs);
    setSelectedDocIds(selection.selectedDocIds);
    setDocToKbMap(selection.docToKbMap);
  }, []);

  // ✅ 统一的会话恢复逻辑：优先处理 URL 参数，然后处理 localStorage
  useEffect(() => {
    if (chatSessions.length === 0) return;

    const chatIdFromUrl = searchParams.get('chatId');

    // ✅ 优先级1：处理 URL 参数中的 chatId（总是处理，不受 hasRestoredSession 限制）
    if (chatIdFromUrl) {
      if (chatIdFromUrl !== currentSessionId) {
        const session = chatSessions.find(s => s.id === chatIdFromUrl);

        if (session) {
          // 处理会话恢复逻辑（包括智能跳转）
          handleSessionRestore(session, chatIdFromUrl);
        } else {
          console.warn(`URL 参数中的会话 ${chatIdFromUrl} 不存在`);
        }

        // ✅ 清除 URL 参数，保持 URL 干净（统一在这里处理）
        setSearchParams({});
      }
      return; // URL 参数处理完毕，不再处理 localStorage
    }

    // ✅ 优先级2：从 localStorage 恢复最后活跃的会话（只在首次加载时执行）
    if (!hasRestoredSession) {
      try {
        const savedSessionId = localStorage.getItem('home_last_session_id');
        if (savedSessionId) {
          const session = chatSessions.find(s => s.id === savedSessionId);
          if (session) {
            // 验证会话确实存在且属于首页
            if (session.config?.sourceType === 'home') {
              handleSessionRestore(session, savedSessionId);
            } else {
              // 如果保存的会话不属于首页，清除 localStorage
              try {
                localStorage.removeItem('home_last_session_id');
              } catch (error) {
                console.error('Failed to remove invalid session from localStorage:', error);
              }
            }
          } else {
            // 如果会话不存在，清除 localStorage
            try {
              localStorage.removeItem('home_last_session_id');
            } catch (error) {
              console.error('Failed to remove non-existent session from localStorage:', error);
            }
          }
        }
      } catch (error) {
        console.error('Failed to restore session from localStorage:', error);
      }

      setHasRestoredSession(true);
    }
  }, [chatSessions, hasRestoredSession, handleSessionRestore, searchParams, setSearchParams, currentSessionId]);
  // ✅ 修复：URL 参数总是处理，localStorage 只在首次加载时处理
  // - searchParams: 响应 URL 参数变化，支持从其他页面跳转回来
  // - currentSessionId: 避免重复恢复同一个会话
  // - hasRestoredSession: 保护 localStorage 恢复只执行一次

  // 历史消息加载完成后的处理：滚动到底部
  useEffect(() => {
    if (currentSessionId && !isLoading && messages.length > 0 && !isStreaming) {
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          scrollToBottom('smooth');
        });
      });
    }
  }, [currentSessionId, isLoading, isStreaming, messages.length, scrollToBottom]);

  useEffect(() => {
    if (!currentSessionId) {
      return;
    }

    const firstUserMessage = messages.find((message) => message.role === 'user' && message.content.trim());
    if (!firstUserMessage) {
      return;
    }

    const nextTitle = buildSessionTitleFromMessage(firstUserMessage.content);
    setChatSessions((prev) => {
      let changed = false;
      const nextSessions = prev.map((session) => {
        if (session.id !== currentSessionId || !PLACEHOLDER_SESSION_TITLES.has(session.title)) {
          return session;
        }
        if (session.title === nextTitle) {
          return session;
        }
        changed = true;
        return {
          ...session,
          title: nextTitle,
        };
      });
      return changed ? nextSessions : prev;
    });
  }, [currentSessionId, messages]);

  // 自动滚动到最新消息 - 优化版本
  useEffect(() => {
    // 只在流式传输时才自动滚动，且使用 requestAnimationFrame 防抖
    if (isStreaming && shouldAutoScrollRef.current) {
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          scrollToBottom('auto');
        });
      });
    }
  }, [isStreaming, messages, scrollToBottom]);

  const handleSend = async () => {
    if (isStreaming) return;
    const text = inputMessage.trim();
    if (!text) {
      if (chatImages.length > 0) {
        toast.warning('请在上传图片后输入问题再发送');
      }
      return;
    }
    
    // 如果有附件但未全部准备好
    const hasUnreadyFiles = attachedFiles.some(f => f.status !== 'ready' && f.status !== 'error');
    if (hasUnreadyFiles) {
      toast.warning('请等待所有文件上传完成');
      return;
    }

    if (isGuestMode && hasReachedGuestMessageLimit) {
      promptLogin({
        title: '登陆解锁更多功能',
        message: '游客试用仅支持发送 3 条消息，登录后可继续完整体验。',
        confirmText: '去登录',
      });
      return;
    }

    const imageDataUrls = chatImages.map((item) => item.dataUrl);
    const readyAttachments = attachedFiles
      .filter((item) => item.status === 'ready' && item.attachment)
      .map((item) => item.attachment as ChatAttachment);

    shouldAutoScrollRef.current = true; // 强制滚动
    
    // ✅ 如果是已有会话，在发送消息前同步更新会话配置（确保文档选择被保存）
    if (currentSessionId && (selectedKBs.length > 0 || selectedDocIds.length > 0)) {
      try {
        await api.updateChatSessionConfig(currentSessionId, {
          uiMode,
          kbIds: selectedKBs,
          docIds: selectedDocIds,
          isKBLocked: true
        });
      } catch (error) {
        console.error('Failed to update session config:', error);
        // 继续发送消息，不阻塞用户操作
      }
    }

    // 在流式回答开始前立即清空输入框和附件预览，避免已发送内容残留在编辑区。
    setInputMessage('');
    clearChatImages();
    if (attachedFiles.length > 0) {
      setAttachedFiles([]);
    }

    await sendMessage(text, {
      imageDataUrls,
      attachments: readyAttachments,
    });
    if (isGuestMode) {
      consumeGuestMessage();
    }
    
    // 清除配额超限弹窗（如果有的话）
    if (quotaExceededModal.isOpen) {
      setQuotaExceededModal({ ...quotaExceededModal, isOpen: false });
    }

    // 🔒 如果选择了知识库且还未锁定，发送第一条消息后锁定
    if (selectedKBs.length > 0 && !isKBLocked) {
      setIsKBLocked(true);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey && !isStreaming) {
      e.preventDefault();
      handleSend();
    }
  };

  // ✅ 新建对话 - 完整的状态清理
  const handleNewChat = () => {
    if (isGuestMode) {
      promptLogin({
        title: '登录后可新建对话',
        message: '游客模式下仅支持体验当前会话，创建新对话需要先登录。',
        confirmText: '去登录',
      });
      return;
    }

    const oldSessionId = currentSessionId;

    setCurrentSessionId(undefined);
    // 清除 localStorage 中的会话ID
    try {
      localStorage.removeItem('home_last_session_id');
    } catch (error) {
      console.error('Failed to remove session ID from localStorage:', error);
    }
    clearMessages({ preserveSessionRuntime: true });
    setInputMessage('');
    setAttachedFiles([]);
    clearChatImages();
    setIsKBLocked(false);
    setSelectedKBs([]);

    // ✅ 重置所有会话相关状态到默认值
    setSelectedDocIds([]);
    setDocToKbMap({});
    setSelectedModelName(resolvePreferredModelName(chatModels, undefined, { defaultModelName }));

    // ✅ 重置消息反馈状态
    setLikedMessages(new Set());
    setDislikedMessages(new Set());
    setSavedToNotes(new Set());

    // 清除 URL 参数
    setSearchParams({});

    if (oldSessionId) {
      console.debug(`Started new chat, cleared previous session: ${oldSessionId}`);
    }
  };

  // 选择历史会话
  const handleSelectChat = (chatId: string) => {
    if (chatId !== currentSessionId) {
      // 保存到 localStorage
      try {
        localStorage.setItem('home_last_session_id', chatId);
      } catch (error) {
        console.error('Failed to save session ID to localStorage:', error);
      }

      // ✅ 复用 handleSessionRestore 逻辑，确保所有配置都被正确恢复
      const session = chatSessions.find(s => s.id === chatId);
      if (session) {
        handleSessionRestore(session, chatId);
      } else {
        // 如果会话不存在（理论上不应该发生），至少切换会话ID
        setCurrentSessionId(chatId);
      }
    }
    // 清除 URL 参数
    setSearchParams({});
  };

  // 删除会话
  const handleDeleteChat = async (chatId: string) => {
    if (isGuestMode) {
      promptLogin({
        title: '登录后可管理对话',
        message: '删除历史对话需要先登录。',
        confirmText: '去登录',
      });
      return;
    }

    try {
      await api.deleteChatSession(chatId);
      // 如果删除的是当前会话，切换到新对话
      if (chatId === currentSessionId) {
        handleNewChat();
      }
      // 重新加载会话列表
      await loadChatSessions();
      toast.success('对话已删除');
    } catch (error) {
      console.error('Failed to delete chat:', error);
      toast.error('删除对话失败');
    }
  };

  // 复制消息内容
  const handleCopyMessage = async (content: string, messageId: string) => {
    try {
      await copyTextToClipboard(content);
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
        // 如果之前点踩了，取消点踩
        setDislikedMessages(prev => {
          const newDisliked = new Set(prev);
          newDisliked.delete(messageId);
          return newDisliked;
        });
      }
      return newSet;
    });
  };

  // 点踩消息
  const handleDislikeMessage = (messageId: string) => {
    setDislikedMessages(prev => {
      const newSet = new Set(prev);
      if (newSet.has(messageId)) {
        newSet.delete(messageId);
      } else {
        newSet.add(messageId);
        // 如果之前点赞了，取消点赞
        setLikedMessages(prev => {
          const newLiked = new Set(prev);
          newLiked.delete(messageId);
          return newLiked;
        });
      }
      return newSet;
    });
  };

  // 保存对话到笔记
  const handleSaveToNotes = async (messageId: string) => {
    if (isGuestMode) {
      promptLogin({
        title: '登录后可保存到笔记',
        message: '游客模式下暂不支持保存内容，登录后可继续操作。',
        confirmText: '去登录',
      });
      return;
    }

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
    } catch (error: unknown) {
      console.error('保存到笔记失败:', error);
      toast.error('保存失败，请重试');
    }
  };

  useEffect(() => {
    setActiveArtifactPreview((current) => (
      current && current.sessionId === currentSessionId ? current : null
    ));
  }, [currentSessionId]);

  const handlePreviewArtifact = useCallback(async (artifact: ChatArtifact) => {
    const objectPath = (artifact.object_path || '').trim();
    if (!objectPath) {
      toast.error('文件路径无效，无法预览');
      return;
    }

    const targetSessionId = (artifact.session_id || currentSessionId || '').trim();
    if (!targetSessionId) {
      toast.error('当前会话不存在，无法预览文件');
      return;
    }

    const fileName = resolveArtifactName(artifact);
    try {
      const response = await api.getSessionArtifactUrl(targetSessionId, objectPath);
      setActiveArtifactPreview({
        sessionId: targetSessionId,
        objectPath,
        fileName,
        url: response.url,
        previewType: getArtifactPreviewType(fileName),
      });
    } catch (error) {
      console.error('Failed to preview artifact:', error);
      toast.error(error instanceof Error ? error.message : '打开预览失败，请稍后重试');
    }
  }, [currentSessionId, toast]);

  const renderComposer = (
    placeholder: string,
    kbButton: React.RefObject<HTMLButtonElement | null>,
    showDisclaimer = false
  ) => (
    <div className={styles.inputSection}>
      <div className={styles.inputWrapper}>
        <div className={styles.inputBox}>
          {/* 文件附件卡片 */}
          {attachedFiles.length > 0 && (
            <div className={styles.attachedFileContainer}>
              <div className={styles.fileCardList}>
                {attachedFiles.map((attachedFile) => (
                  <div key={attachedFile.localId} className={styles.fileCard}>
                    <div className={styles.fileIcon}>
                      <img src={getFileIcon(attachedFile.file.name)} alt="File" />
                    </div>
                    <div className={styles.fileInfo}>
                      <div className={styles.fileName} title={attachedFile.file.name}>
                        {attachedFile.file.name}
                      </div>
                      <div className={styles.fileMeta}>
                        {formatFileSize(attachedFile.file.size)}
                        {attachedFile.status === 'uploading' && <span className={styles.statusText}> · 上传中</span>}
                        {attachedFile.status === 'error' && <span className={styles.errorText}> · 失败</span>}
                      </div>
                    </div>
                    <button
                      className={styles.removeFileButton}
                      onClick={(e) => { e.stopPropagation(); void handleRemoveFile(attachedFile); }}
                      title="移除文件"
                    >
                      <X size={16} />
                    </button>

                    {attachedFile.status === 'uploading' && (
                      <div className={styles.progressTrack}>
                        <div
                          className={styles.progressFill}
                          style={{ width: `${Math.max(2, Math.min(100, attachedFile.progress ?? 0))}%` }}
                        />
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* 聊天图片附件 */}
          <ChatImagePreviewList
            images={chatImages}
            onRemoveImage={handleRemoveChatImage}
            classNames={{
              list: styles.chatImagePreviewList,
              item: styles.chatImagePreviewItem,
              image: styles.chatImagePreview,
              removeButton: styles.removeChatImageButton,
            }}
          />

          <div
            className={`${styles.inputRow} ${isImageDragOver ? styles.imageDragOver : ''}`}
            onDragOver={handleImageDragOver}
            onDragLeave={handleImageDragLeave}
            onDrop={handleComposerDrop}
          >
            <textarea
              ref={composerInputRef}
              className={styles.input}
              placeholder={placeholder}
              value={inputMessage}
              onChange={(e) => setInputMessage(e.target.value)}
              onKeyDown={handleKeyDown}
              disabled={isStreaming}
              rows={1}
              style={{
                height: 'auto',
                minHeight: '36px',
                maxHeight: '200px'
              }}
              onInput={(e) => {
                const target = e.target as HTMLTextAreaElement;
                target.style.height = 'auto';
                target.style.height = `${Math.max(target.scrollHeight, 36)}px`;
              }}
            />
          </div>

          <input
            type="file"
            ref={fileInputRef}
            style={{ display: 'none' }}
            multiple
            onChange={handleFileSelect}
            disabled={isStreaming || (attachedFiles.length >= MAX_FILES && chatImages.length >= maxChatImages)}
          />

          <div className={styles.modeSwitch}>
            <div className={styles.modeSwitchLeft}>
              <ChatModelSelector
                models={chatModels}
                value={selectedModelName}
                onChange={handleModelChange}
                disabled={isStreaming}
                requireVision={chatImages.length > 0}
                defaultModelName={defaultModelName}
              />
              <ChatUIModeSwitch
                value={uiMode}
                onChange={handleUIModeChange}
                disabled={isStreaming}
              />
              <div className={styles.kbSelectorWrapper}>
                <button
                  ref={kbButton}
                  className={`${styles.kbInlineButton} ${selectedKBs.length > 0 ? styles.kbInlineButtonActive : ''} ${isKBLocked ? styles.kbInlineButtonReadonly : ''}`}
                  onClick={(e) => {
                    e.stopPropagation();
                    handleOpenKBSelector(kbButton);
                  }}
                  onMouseDown={(e) => {
                    e.stopPropagation();
                  }}
                  disabled={isStreaming}
                  title={isKBLocked ? "查看当前会话使用的知识库（只读）" : "选择知识库"}
                >
                  <span>知识库{selectedDocIds.length > 0 && ` (${selectedDocIds.length})`}</span>
                  <ChevronDown size={14} />
                </button>

                <KnowledgeBaseSelector
                  selectedKBs={selectedKBs}
                  selectedDocIds={selectedDocIds}
                  docToKbMap={docToKbMap}
                  onSelectionChange={handleKBSelectionChange}
                  isOpen={showKBSelector}
                  onClose={() => setShowKBSelector(false)}
                  position={kbPanelPosition || undefined}
                  disabled={isKBLocked}
                />
              </div>
            </div>

            <div className={styles.modeSwitchRight}>
              <button
                className={styles.uploadButton}
                onClick={handleUploadClick}
                disabled={isStreaming || (attachedFiles.length >= MAX_FILES && chatImages.length >= maxChatImages)}
                title={`上传文件或图片 - 文件最多${MAX_FILES}个，图片最多${maxChatImages}张`}
              >
                <Paperclip size={20} />
              </button>
              <SendStopButton
                isStreaming={isStreaming}
                disabled={attachedFiles.length > 0 && attachedFiles.some(f => f.status !== 'ready' && f.status !== 'error')}
                onSend={handleSend}
                onStop={stopGeneration}
                hasContent={!!inputMessage.trim()}
              />
            </div>
          </div>
        </div>

        {showDisclaimer && (
          <div className={styles.disclaimer}>
            答案由AI生成，仅供参考
          </div>
        )}
      </div>
    </div>
  );

  const renderTaskLauncher = () => (
    <div className={styles.heroTaskDock}>
      {PRIMARY_TASK_SHORTCUTS.map((task) => {
        const Icon = task.icon;
        return (
          <button
            key={task.id}
            type="button"
            className={styles.heroTaskChip}
            onClick={() => handleTaskShortcutSelect(task.prompt)}
          >
            <Icon size={22} strokeWidth={1.8} />
            <span>{task.label}</span>
          </button>
        );
      })}

      <div className={styles.heroTaskMenuShell} ref={taskMenuRef}>
        <button
          ref={taskMenuButtonRef}
          type="button"
          className={`${styles.heroTaskChip} ${styles.heroTaskChipCompact}`}
          onClick={() => setIsTaskMenuOpen((current) => !current)}
          aria-haspopup="menu"
          aria-expanded={isTaskMenuOpen}
        >
          <span>更多</span>
        </button>

        {isTaskMenuOpen && taskMenuPosition && createPortal(
          <div
            ref={taskMenuPopupRef}
            className={styles.heroTaskMenu}
            role="menu"
            aria-label="更多任务类型"
            style={{
              position: 'fixed',
              left: taskMenuPosition.left,
              width: taskMenuPosition.width,
              maxHeight: taskMenuPosition.maxHeight,
              top: taskMenuPosition.top,
              bottom: taskMenuPosition.bottom,
            }}
          >
            {MORE_TASK_SHORTCUTS.map((task) => {
              const Icon = task.icon;
              return (
                <button
                  key={task.id}
                  type="button"
                  className={styles.heroTaskMenuItem}
                  onClick={() => handleTaskShortcutSelect(task.prompt)}
                  role="menuitem"
                >
                  <Icon size={20} strokeWidth={1.8} />
                  <span>{task.label}</span>
                </button>
              );
            })}
          </div>,
          document.body,
        )}
      </div>
    </div>
  );

  return (
    <div className={styles.home}>
      {isMobile && isSidebarOpen && (
        <div className={styles.overlay} onClick={() => setIsSidebarOpen(false)} />
      )}
      
      <div className={`${styles.sidebarContainer} ${isMobile && isSidebarOpen ? styles.open : ''}`}>
        <Sidebar
          onNewChat={handleNewChat}
          onSelectChat={handleSelectChat}
          onDeleteChat={handleDeleteChat}
          onClearAllChats={async () => {
            // 立即清空会话列表，提供即时反馈
            setChatSessions([]);
            handleNewChat();
            // 重新加载会话列表（应该为空）
            await loadChatSessions();
          }}
          selectedChatId={currentSessionId}
          chats={chatSessions}
        />
      </div>

      <div className={styles.mainContent}>
        {isMobile && (
          <div className={styles.mobileHeader}>
            <button onClick={() => setIsSidebarOpen(true)} className={styles.menuButton}>
              <Menu size={20} />
            </button>
            <h1 className={styles.mobileTitle}>Lumen</h1>
          </div>
        )}
        
        <div className={styles.chatContainer}>
          {isLoading ? (
            // 加载历史消息
            <div className={styles.loadingContainer}>
              <div className={styles.loadingSpinner}></div>
              <p className={styles.loadingText}>加载历史消息...</p>
            </div>
          ) : messages.length === 0 ? (
            // 欢迎屏幕 + 居中布局 (Centered Layout - Polished)
            <div className={styles.emptyContainer}>
              <div className={styles.welcomeContent}>
                <h1 className={styles.welcomeTitle}>
                  <span className={styles.titleText}>{welcomePrefixText}</span>
                  <span className={styles.highlight}>提问</span>
                  <span className={styles.titleText}>来发现世界</span>
                </h1>
              </div>

              {renderComposer('输入您的问题......', kbButtonRef, false)}
              {renderTaskLauncher()}
            </div>
          ) : (
            // 对话界面
            <>
              {/* 计算是否有双栏布局激活（最后一条AI消息使用双栏） */}
              <div className={`${styles.chatWorkspace} ${activeArtifactPreview ? styles.chatWorkspaceSplit : ''}`}>
                <div 
                  className={`${styles.messagesArea} ${activeArtifactPreview ? styles.dualLayoutActive : ''}`}
                  ref={chatContainerRef}
                  onScroll={handleScroll}
	                >
	                  <div className={styles.messageGroup}>
	                  {messages.map((msg, index) => {
	                    const assistantText = msg.role === 'assistant'
	                      ? getAssistantRenderableText(msg)
	                      : '';
	                    const hasAssistantActions = msg.role === 'assistant'
	                      && hasAssistantActionBar(msg)
	                      && (!isStreaming || index !== messages.length - 1);
	                    return (
	                    <div key={msg.id} className={`${styles.messageItem} ${msg.role === 'user' ? styles.userMessageItem : styles.aiMessageItem}`}>
                        <div className={msg.role === 'user' ? styles.userAvatar : styles.aiAvatar}>
                          {msg.role === 'user' ? (
                          <img src={profile?.avatar || defaultAvatar} alt="User" className={styles.avatarImage} />
                        ) : (
                          <img src={aiAvatarUrl} alt="AI" className={styles.avatarImage} />
                        )}
                      </div>
                      <div className={styles.messageContent}>
	                        {msg.role === 'assistant'
	                          && !hasAssistantVisiblePayload(msg)
	                          && isStreaming
	                          && index === messages.length - 1 ? (
                          <div className={styles.thinking}>
                            <div className={styles.thinkingDots}>
                              <span className={styles.dot}></span>
                              <span className={styles.dot}></span>
                              <span className={styles.dot}></span>
                            </div>
                          </div>
                        ) : (
                          <>
                            {msg.role === 'user' ? (
                              msg.content && (
                                <div className={styles.userMessageBlock}>
                                  <div className={styles.userMessageActions}>
                                    <button
                                      className={`${styles.actionButton} ${copiedMessages.has(msg.id) ? styles.copied : ''}`}
                                      onClick={() => handleCopyMessage(msg.content, msg.id)}
                                      aria-label={copiedMessages.has(msg.id) ? '已复制用户消息' : '复制用户消息'}
                                    >
                                      {copiedMessages.has(msg.id) ? <Check size={16} /> : <Copy size={16} />}
                                    </button>
                                  </div>
                                  <div className={styles.userMessageText}>
                                    <div className={styles.userMessageContent}>
                                      <div>{msg.content}</div>
                                      {msg.imageDataUrls && msg.imageDataUrls.length > 0 && (
                                        <div className={styles.chatImageGrid}>
                                          {msg.imageDataUrls.map((url, imageIndex) => (
                                            <img
                                              key={`${msg.id}-img-${imageIndex}`}
                                              src={url}
                                              alt={`上传图片 ${imageIndex + 1}`}
                                              className={styles.chatImage}
                                            />
                                          ))}
                                        </div>
                                      )}
                                      {(() => {
                                        const visibleAttachments = (msg.attachments || []).filter((attachment) => (
                                          !(msg.imageDataUrls && msg.imageDataUrls.length > 0 && isInlineImageWorkspaceAttachment(attachment))
                                        ));
                                        return visibleAttachments.length > 0 ? (
                                        <div className={styles.userAttachmentList}>
                                          {visibleAttachments.map((attachment, attachmentIndex) => {
                                            const tone = resolveAttachmentStatusTone(attachment);
                                            const statusLabel = resolveAttachmentStatusLabel(attachment);
                                            return (
                                              <div
                                                key={`${msg.id}-attachment-${attachment.attachment_id || attachmentIndex}`}
                                                className={styles.userAttachmentCard}
                                              >
                                                <div className={styles.userAttachmentInfo}>
                                                  <img
                                                    src={getFileIcon(attachment.name)}
                                                    alt="attachment"
                                                    className={styles.userAttachmentIcon}
                                                  />
                                                  <div className={styles.userAttachmentMeta}>
                                                    <div className={styles.userAttachmentName}>
                                                      {attachment.name}
                                                    </div>
                                                    <div className={styles.userAttachmentSubMeta}>
                                                      {typeof attachment.size_bytes === 'number' && attachment.size_bytes >= 0
                                                        ? formatFileSize(attachment.size_bytes)
                                                        : '文件已上传'}
                                                    </div>
                                                  </div>
                                                </div>
                                                {statusLabel && (
                                                  <span
                                                    className={`${styles.userAttachmentStatus} ${
                                                      tone === 'error'
                                                        ? styles.userAttachmentStatusError
                                                        : tone === 'pending'
                                                          ? styles.userAttachmentStatusPending
                                                          : ''
                                                    }`}
                                                  >
                                                    {statusLabel}
                                                  </span>
                                                )}
                                              </div>
                                            );
                                          })}
                                        </div>
                                        ) : null;
                                      })()}
                                    </div>
                                  </div>
                                </div>
                              )
                            ) : (
                              <>
                                <AssistantMessageFlow
                                  tupleMessages={msg.assistantTupleMessages}
                                  content={msg.content}
                                  thinking={msg.thinking}
                                  toolTraces={msg.toolTraces}
                                  contentClassName={styles.aiMessageText}
                                  isStreaming={isStreaming && index === messages.length - 1}
                                />
                                <AssistantMessageInterruption interruption={msg.interruption} />
                              </>
                            )}
	                            {msg.role === 'assistant' && (
	                              <AssistantArtifactList
	                                artifacts={msg.artifacts}
	                                sessionId={currentSessionId}
	                                messageId={msg.id}
                                  onPreviewArtifact={handlePreviewArtifact}
	                              />
	                            )}
	                            {/* AI 消息操作按钮 - 只在流式输出完成后显示 */}
	                            {hasAssistantActions && (
	                              <div className={styles.messageActions}>
	                                {assistantText && (
	                                  <>
	                                    <Tooltip content={copiedMessages.has(msg.id) ? "已复制" : "复制"} position="top">
	                                      <button
	                                        className={`${styles.actionButton} ${copiedMessages.has(msg.id) ? styles.copied : ''}`}
	                                        onClick={() => handleCopyMessage(assistantText, msg.id)}
	                                      >
	                                        {copiedMessages.has(msg.id) ? <Check size={16} /> : <Copy size={16} />}
	                                      </button>
                                    </Tooltip>
                                    <Tooltip content={likedMessages.has(msg.id) ? "取消点赞" : "点赞"} position="top">
                                      <button
                                        className={`${styles.actionButton} ${likedMessages.has(msg.id) ? styles.liked : ''}`}
                                        onClick={() => handleLikeMessage(msg.id)}
                                      >
                                        <ThumbsUp size={16} />
                                      </button>
                                    </Tooltip>
                                    <Tooltip content={dislikedMessages.has(msg.id) ? "取消点踩" : "点踩"} position="top">
                                      <button
                                        className={`${styles.actionButton} ${dislikedMessages.has(msg.id) ? styles.disliked : ''}`}
                                        onClick={() => handleDislikeMessage(msg.id)}
                                      >
                                        <ThumbsDown size={16} />
                                      </button>
                                    </Tooltip>
                                    <Tooltip content={savedToNotes.has(msg.id) ? "已保存到笔记" : "保存到笔记"} position="top">
                                      <button
                                        className={`${styles.actionButton} ${savedToNotes.has(msg.id) ? styles.saved : ''}`}
                                        onClick={() => handleSaveToNotes(msg.id)}
                                      >
                                        <FileText size={16} />
                                      </button>
                                    </Tooltip>
                                  </>
                                )}
                                <div className={styles.regenerateWrapper}>
                                  <Tooltip content="重新生成" position="top">
                                    <button
                                      className={styles.actionButton}
                                      onClick={() => regenerateLastMessage()}
                                      onContextMenu={(e) => {
                                        e.preventDefault();
                                        setShowRegenerateMenu(showRegenerateMenu === msg.id ? null : msg.id);
                                      }}
                                    >
                                      <RefreshCw size={16} />
                                    </button>
                                  </Tooltip>
                                  {showRegenerateMenu === msg.id && (
                                    <div className={styles.regenerateMenu} ref={regenerateMenuRef}>
                                      <button
                                        className={styles.regenerateMenuItem}
                                        onClick={(e) => {
                                          e.stopPropagation();
                                          regenerateLastMessage();
                                          setShowRegenerateMenu(null);
                                        }}
                                      >
                                        <RefreshCw size={14} />
                                        <span>重新生成</span>
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
                  );
                  })}
                    <div ref={messagesEndRef} />
                  </div>
                </div>
                {activeArtifactPreview ? (
                  <div className={styles.previewPane}>
                    <ChatArtifactPreviewPane
                      preview={activeArtifactPreview}
                      onClose={() => setActiveArtifactPreview(null)}
                    />
                  </div>
                ) : null}
              </div>

              {renderComposer('继续对话...', kbButtonRef2, true)}
            </>
          )}
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
