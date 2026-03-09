/**
 * 收藏页面
 * 显示用户收藏的知识库和文档，支持PDF文档预览
 */
import { useState, useEffect, useRef, useCallback } from 'react';
import { Database, FileText, Trash2, ExternalLink, Loader2, Star, MessageCircle, Send, User, Sparkles, Copy, Check, ThumbsUp, ThumbsDown, ChevronDown, ChevronUp, PlusCircle } from 'lucide-react';
import QuotaExceededModal from '@/components/QuotaExceededModal/QuotaExceededModal';
import { useNavigate, useSearchParams } from 'react-router-dom';
import Sidebar from '@/components/Sidebar/Sidebar';
import OptimizedMarkdown from '@/components/OptimizedMarkdown';
import PDFViewer from '@/components/PDFViewer/PDFViewer';
import DocumentViewer from '@/components/DocumentViewer/DocumentViewer';
import SendStopButton from '@/components/SendStopButton';
import { api, favoriteAPI, kbAPI } from '@/lib/api';
import { useToast } from '@/hooks/useToast';
import { useRAGChat } from '@/hooks/useRAGChat';
import { useUserProfile } from '@/hooks/useUserProfile';
import { useChatSessions } from '@/hooks/useChatSessions';
import { getKnowledgeBaseAvatar } from '@/utils/avatarUtils';
import { saveConversationToNoteById } from '@/utils/noteUtils';
import { getFileIcon, pdfIconUrl } from '@/utils/fileIcons';
import styles from './Favorites.module.css';
import knowledgeIconUrl from '@/assets/knowledge.svg';
import aiAvatarUrl from '@/assets/ai.jpg';

type TabType = 'kb' | 'doc';

export default function Favorites() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const toast = useToast();
  const { profile } = useUserProfile();
  const { chatSessions, refreshSessions } = useChatSessions();
  const [activeTab, setActiveTab] = useState<TabType>('kb');
  const [favoriteKBs, setFavoriteKBs] = useState<any[]>([]);
  const [favoriteDocs, setFavoriteDocs] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);
  const [isMobile, setIsMobile] = useState(false);
  
  // Preview State
  const [previewDoc, setPreviewDoc] = useState<any>(null);
  const [previewUrl, setPreviewUrl] = useState<string>('');
  const [previewContent, setPreviewContent] = useState<string>(''); // 非 PDF 文件的 markdown 内容
  const [loadingPreview, setLoadingPreview] = useState(false);
  
  // Chat Session State
  const [currentSessionId, setCurrentSessionId] = useState<string | undefined>(undefined);

  // 配额超限内联消息状态（显示在聊天区域内，不保存到历史）
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
  }, []);

  const handleSessionCreated = useCallback((newSessionId: string) => {
    setCurrentSessionId(newSessionId);
  }, []);

  const handleFirstContentToken = useCallback((messageId: string) => {
    // 当第一个 content token 到达时，自动折叠 thinking
    setCollapsedThinking(prev => {
      const newSet = new Set(prev);
      newSet.add(messageId);
      return newSet;
    });
  }, []);

  // RAG Chat Hook - 当预览文档时使用
  const { messages, isStreaming, sendMessage, clearMessages, stopGeneration } = useRAGChat({
    sessionId: currentSessionId,
    kbId: previewDoc?.kbId,
    docIds: previewDoc ? [previewDoc.id] : undefined,
    mode: 'deep',
    onError: handleError,
    onSessionCreated: handleSessionCreated,
    onFirstContentToken: handleFirstContentToken
  });
  
  const [inputMessage, setInputMessage] = useState('');
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // 消息反馈状态
  const [likedMessages, setLikedMessages] = useState<Set<string>>(new Set());
  const [dislikedMessages, setDislikedMessages] = useState<Set<string>>(new Set());
  const [collapsedThinking, setCollapsedThinking] = useState<Set<string>>(new Set());
  const [savedToNotes, setSavedToNotes] = useState<Set<string>>(new Set()); // 已保存到笔记的消息ID
  const [copiedMessages, setCopiedMessages] = useState<Set<string>>(new Set()); // 已复制的消息ID
  const collapseTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const chatContainerRef = useRef<HTMLDivElement>(null);
  const shouldAutoScrollRef = useRef(true);

  // 处理滚动事件，判断用户是否在底部
  const handleScroll = useCallback(() => {
    if (chatContainerRef.current) {
      const { scrollTop, scrollHeight, clientHeight } = chatContainerRef.current;
      // 如果距离底部小于 100px，则认为在底部，允许自动滚动
      const isAtBottom = scrollHeight - scrollTop - clientHeight < 100;
      shouldAutoScrollRef.current = isAtBottom;
    }
  }, []);

  useEffect(() => {
    const check = () => setIsMobile(window.innerWidth <= 768);
    check();
    window.addEventListener('resize', check);
    return () => window.removeEventListener('resize', check);
  }, []);

  useEffect(() => {
    loadFavorites();
  }, [activeTab]);

  // ✅ 处理 URL 参数中的 chatId，添加验证逻辑
  useEffect(() => {
    const chatIdFromUrl = searchParams.get('chatId');
    if (chatIdFromUrl && chatIdFromUrl !== currentSessionId && chatSessions.length > 0) {
      // 验证会话是否存在且属于收藏页面（实际上收藏页面可以显示任何会话）
      const session = chatSessions.find(s => s.id === chatIdFromUrl);
      if (session) {
        setCurrentSessionId(chatIdFromUrl);
        // 清除 URL 参数，保持 URL 干净
        setSearchParams({});
      } else {
        console.warn(`会话 ${chatIdFromUrl} 不存在，忽略 URL 参数`);
        // 清除无效的 URL 参数
        setSearchParams({});
      }
    }
  }, [searchParams, currentSessionId, setSearchParams, chatSessions]);

  // 自动滚动到最新消息
  useEffect(() => {
    if (isStreaming && shouldAutoScrollRef.current && messagesEndRef.current) {
      requestAnimationFrame(() => {
        messagesEndRef.current?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
      });
    }
  }, [messages, isStreaming]);

  const loadFavorites = async () => {
    setLoading(true);
    try {
      if (activeTab === 'kb') {
        const response = await favoriteAPI.listFavoriteKBs();
        setFavoriteKBs(response.items);
      } else {
        const response = await favoriteAPI.listFavoriteDocuments();
        setFavoriteDocs(response.items);
      }
    } catch (error: any) {
      console.error('Failed to load favorites:', error);
      toast.error(error.message || '加载收藏失败');
    } finally {
      setLoading(false);
    }
  };

  const handleUnfavoriteKB = async (kbId: string) => {
    console.log('handleUnfavoriteKB called with kbId:', kbId);
    try {
      await favoriteAPI.unfavoriteKB(kbId);
      console.log('unfavoriteKB API call successful');
      toast.success('已取消收藏');
      loadFavorites();
      
      // 触发全局事件，通知其他页面刷新订阅列表
      // 后端在取消收藏时会自动取消订阅（如果收藏来源是订阅）
      localStorage.setItem('kb_subscription_changed', Date.now().toString());
      window.dispatchEvent(new Event('kb_subscription_changed'));
    } catch (error: any) {
      console.error('unfavoriteKB error:', error);
      toast.error(error.message || '操作失败');
    }
  };

  const handleUnfavoriteDoc = async (docId: string) => {
    console.log('handleUnfavoriteDoc called with docId:', docId);
    try {
      await favoriteAPI.unfavoriteDocument(docId);
      console.log('unfavoriteDocument API call successful');
      toast.success('已取消收藏');
      // 如果当前正在预览这个文档，关闭预览
      if (previewDoc?.id === docId) {
        handleClosePreview();
      }
      loadFavorites();
    } catch (error: any) {
      console.error('unfavoriteDocument error:', error);
      toast.error(error.message || '操作失败');
    }
  };

  // 判断是否为 PDF 文件
  const isPdfFile = (filename: string) => {
    return filename.toLowerCase().endsWith('.pdf');
  };

  // 预览文档
  const handlePreviewDocument = async (doc: any) => {
    setLoadingPreview(true);
    setCurrentSessionId(undefined); // 重置会话ID
    clearMessages(); // 清空对话历史
    setPreviewUrl('');
    setPreviewContent('');
    setPreviewDoc(doc);
    
    try {
      // 获取文件 URL（所有格式都需要）
      const urlResponse = await kbAPI.getDocumentUrl(doc.kbId, doc.id);
      setPreviewUrl(urlResponse.url);
      
      // 对于 .doc 文件，额外获取 markdown 内容作为降级方案
      const ext = doc.name.toLowerCase().split('.').pop();
      if (ext === 'doc') {
        try {
          const mdResponse = await kbAPI.getDocumentMarkdown(doc.kbId, doc.id);
          setPreviewContent(mdResponse.content);
        } catch (e) {
          console.warn('Failed to get markdown content for .doc file:', e);
        }
      }
    } catch (error: any) {
      toast.error(error.message || '无法加载文档预览');
      setPreviewDoc(null);
    } finally {
      setLoadingPreview(false);
    }
  };

  // 关闭预览
  const handleClosePreview = () => {
    setPreviewDoc(null);
    setPreviewUrl('');
    setPreviewContent('');
    setCurrentSessionId(undefined);
    clearMessages();
    setInputMessage('');
  };

  // 处理PDF文本选择
  const handlePDFTextSelect = useCallback((text: string) => {
    // 将选中的文本添加到输入框
    setInputMessage((prev: string) => {
      if (prev.trim()) {
        return `${prev}\n\n${text}`;
      }
      return text;
    });

    // 调整输入框高度，并将光标定位到末尾
    setTimeout(() => {
      if (textareaRef.current) {
        const textarea = textareaRef.current;
        textarea.style.height = 'auto';
        textarea.style.height = Math.min(textarea.scrollHeight, 200) + 'px';
        
        // 聚焦并将光标移到末尾
        textarea.focus();
        const length = textarea.value.length;
        textarea.setSelectionRange(length, length);
        // 滚动到光标位置（末尾）
        textarea.scrollTop = textarea.scrollHeight;
      }
    }, 0);
  }, []);

  // 发送消息
  const handleSendMessage = () => {
    if (!inputMessage.trim() || !previewDoc || isStreaming) return;
    shouldAutoScrollRef.current = true; // 发送消息时强制开启自动滚动
    sendMessage(inputMessage);
    setInputMessage('');
    // 清除配额超限弹窗（如果有的话）
    if (quotaExceededModal.isOpen) {
      setQuotaExceededModal({ ...quotaExceededModal, isOpen: false });
    }
    // 重置输入框高度
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
    }
  };

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

  // 聊天处理函数
  const handleNewChat = () => {
    navigate('/');
  };

  const handleSelectChat = (chatId: string) => {
    navigate(`/?chatId=${chatId}`);
  };

  const handleDeleteChat = async (chatId: string) => {
    try {
      await api.deleteChatSession(chatId);
      await refreshSessions();
      toast.success('对话已删除');
    } catch (error) {
      console.error('Failed to delete chat:', error);
      toast.error('删除对话失败');
    }
  };

  return (
    <div className={styles.page}>
      {isMobile && isSidebarOpen && (
        <div className={styles.overlay} onClick={() => setIsSidebarOpen(false)} />
      )}

      <div className={`${styles.sidebarContainer} ${isMobile && isSidebarOpen ? styles.open : ''}`}>
        <Sidebar 
          onNewChat={handleNewChat}
          onSelectChat={handleSelectChat}
          onDeleteChat={handleDeleteChat}
          chats={chatSessions}
        />
      </div>
      
      <div className={styles.main}>
        {previewDoc && activeTab === 'doc' ? (
          /* 预览模式：文档预览 + 对话界面 */
          <div className={styles.previewMode}>
            {/* 左侧文档预览 - 50% */}
            <div className={styles.pdfPreviewSection}>
              {loadingPreview ? (
                <div className={styles.previewLoading}>
                  <Loader2 size={32} className="animate-spin" />
                  <p>加载中...</p>
                </div>
              ) : previewUrl && isPdfFile(previewDoc.name) ? (
                // PDF 文件使用 PDFViewer
                <PDFViewer
                  url={previewUrl}
                  fileName={previewDoc.name}
                  onTextSelect={handlePDFTextSelect}
                  onClose={handleClosePreview}
                />
              ) : previewUrl ? (
                // 非 PDF 文件使用 DocumentViewer（docx/doc/txt/md）
                <DocumentViewer
                  url={previewUrl}
                  fileName={previewDoc.name}
                  markdownContent={previewContent}
                  onTextSelect={handlePDFTextSelect}
                  onClose={handleClosePreview}
                />
              ) : (
                <div className={styles.previewLoading}>
                  <FileText size={48} />
                  <p>无法加载文档预览</p>
                </div>
              )}
            </div>

            {/* 右侧对话界面 - 50% */}
            <div className={styles.chatSectionFull}>
              <div className={styles.chatHeader}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                  <span>文档对话</span>
                </div>
                <button
                  className={styles.newChatButton}
                  onClick={() => {
                    setCurrentSessionId(undefined);
                    clearMessages();
                  }}
                  title="开始新对话"
                >
                  <PlusCircle size={18} />
                </button>
              </div>
              <div 
                className={styles.chatMessages} 
                ref={chatContainerRef}
                onScroll={handleScroll}
              >
                {messages.length === 0 ? (
                  <div className={styles.chatEmpty}>
                    <MessageCircle size={56} />
                    <p>开始与文档对话</p>
                    <span>提出你的问题，AI 将基于文档内容回答</span>
                  </div>
                ) : (
                  <>
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
                            <img src={aiAvatarUrl} alt="AI" className={styles.avatarImage} />
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
                                </div>
                              )}
                            </>
                          )}
                        </div>
                      </div>
                    ))}
                    <div ref={messagesEndRef} style={{ height: 0, overflow: 'hidden' }} />
                  </>
                )}
              </div>
              <div className={styles.chatInputArea}>
                <div className={styles.chatInputWrap}>
                  <textarea
                    ref={textareaRef}
                    placeholder="输入你的问题..."
                    value={inputMessage}
                    onChange={(e) => {
                      setInputMessage(e.target.value);
                      // 自动调整高度
                      const target = e.target as HTMLTextAreaElement;
                      target.style.height = 'auto';
                      target.style.height = Math.min(target.scrollHeight, 200) + 'px';
                    }}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' && !e.shiftKey && !isStreaming && inputMessage.trim()) {
                        e.preventDefault();
                        handleSendMessage();
                      }
                    }}
                    className={styles.chatInput}
                    disabled={isStreaming}
                    rows={1}
                  />
                  <SendStopButton
                    isStreaming={isStreaming}
                    disabled={!inputMessage.trim()}
                    hasContent={!!inputMessage.trim()}
                    onSend={handleSendMessage}
                    onStop={stopGeneration}
                  />
                </div>
                <div className={styles.chatFooter}>
                  答案由AI生成，仅供参考
                </div>
              </div>
            </div>
          </div>
        ) : (
          /* 列表模式：显示我的收藏 */
          <div className={styles.contentArea}>
            {/* 左侧列表区域 */}
            <div className={styles.listSection}>
            <div className={styles.header}>
              <h1 className={styles.title}>
                <Star size={24} className={styles.titleIcon} />
                我的收藏
              </h1>
            </div>

            <div className={styles.tabs}>
              <button
                className={`${styles.tab} ${activeTab === 'kb' ? styles.tabActive : ''}`}
                onClick={() => {
                  setActiveTab('kb');
                  handleClosePreview();
                }}
              >
                <img src={knowledgeIconUrl} alt="知识库" width={16} height={16} />
                知识库
              </button>
              <button
                className={`${styles.tab} ${activeTab === 'doc' ? styles.tabActive : ''}`}
                onClick={() => setActiveTab('doc')}
              >
                <img src={pdfIconUrl} alt="PDF" width={16} height={16} />
                文档
              </button>
            </div>

            {loading ? (
              <div className={styles.loading}>
                <Loader2 size={24} className="animate-spin" />
                <p>加载中...</p>
              </div>
            ) : (
              <div className={styles.content}>
                {activeTab === 'kb' ? (
                  favoriteKBs.length === 0 ? (
                    <div className={styles.empty}>
                      <Database size={48} />
                      <p>还没有收藏任何知识库</p>
                    </div>
                  ) : (
                    <div className={styles.grid}>
                      {favoriteKBs.map((kb) => (
                        <div 
                          key={kb.id} 
                          className={styles.card}
                          onClick={() => navigate(`/knowledge/${kb.id}`)}
                        >
                          <div className={styles.cardHeader}>
                            <img src={getKnowledgeBaseAvatar(kb)} alt={kb.name} className={styles.avatar} />
                          </div>
                          <div className={styles.cardBody}>
                            <div className={styles.cardHeaderTop}>
                              <div className={styles.titleGroup}>
                                <h3 className={styles.cardTitle}>{kb.name}</h3>
                                {kb.category && (
                                  <div className={styles.categoryBadge}>
                                    <span>{kb.category}</span>
                                  </div>
                                )}
                              </div>
                              {(kb.is_admin_recommended || kb.from_organization) && (
                                <div className={styles.sourceTag}>
                                  {kb.is_admin_recommended 
                                    ? '来自：Reader官方' 
                                    : `组织：${kb.organization_name}`}
                                </div>
                              )}
                            </div>
                            <p className={styles.cardDesc}>{kb.description || '暂无描述'}</p>
                            <div className={styles.cardFooter}>
                              <div className={styles.stats}>
                                <div className={styles.cardMeta}>
                                  <Star size={12} fill="currentColor" /> {kb.subscribersCount || 0} 订阅
                                </div>
                                <div className={styles.cardMeta}>
                                  <Database size={12} /> {kb.contents || 0} 文档
                                </div>
                              </div>
                              {kb.creator_name && (
                                <div className={styles.creatorInfo}>
                                  {kb.creator_avatar ? (
                                    <img src={kb.creator_avatar} alt={kb.creator_name} className={styles.creatorAvatar} />
                                  ) : (
                                    <div className={styles.creatorAvatarPlaceholder}>
                                      <User size={12} color="#64748b" />
                                    </div>
                                  )}
                                  <span className={styles.creatorName}>{kb.creator_name}</span>
                                </div>
                              )}
                            </div>
                          </div>
                        </div>
                      ))}
                    </div>
                  )
                ) : (
                  favoriteDocs.length === 0 ? (
                    <div className={styles.empty}>
                      <FileText size={48} />
                      <p>还没有收藏任何文档</p>
                    </div>
                  ) : (
                    <div className={styles.docList}>
                      {favoriteDocs.map((doc) => (
                        <div 
                          key={doc.id} 
                          className={`${styles.docItem} ${previewDoc?.id === doc.id ? styles.docItemActive : ''}`}
                        >
                          <div 
                            className={styles.docClickArea}
                            onClick={() => handlePreviewDocument(doc)}
                          >
                            <img src={getFileIcon(doc.name)} alt="File" width={20} height={20} className={styles.docIcon} />
                            <div className={styles.docInfo}>
                              <div className={styles.docName}>{doc.name}</div>
                              <div className={styles.docKb}>
                                <Database size={12} />
                                {doc.kbName}
                              </div>
                            </div>
                          </div>
                          <button
                            className={styles.btnFavorite}
                            onClick={(e) => {
                              e.preventDefault();
                              e.stopPropagation();
                              console.log('Star clicked! Unfavoriting docId:', doc.id);
                              handleUnfavoriteDoc(doc.id);
                            }}
                            onMouseDown={(e) => {
                              e.preventDefault();
                              e.stopPropagation();
                            }}
                            title="取消收藏"
                          >
                            <Star size={18} fill="#f59e0b" color="#f59e0b" />
                          </button>
                        </div>
                      ))}
                    </div>
                  )
                )}
              </div>
            )}
          </div>
          </div>
        )}
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
