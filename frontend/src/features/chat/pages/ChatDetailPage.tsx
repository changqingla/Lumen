import React, { useState, useRef, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import Sidebar from '@/app/components/Sidebar/Sidebar';
import {
  AssistantArtifactList,
  AssistantMessageFlow,
  AssistantMessageInterruption,
  ChatUIModeSwitch,
  SendStopButton,
  ChatModelSelector,
  QuotaExceededModal,
} from '@/features/chat/components';
import { ArrowLeft, Menu, Copy, Check, RefreshCw, ThumbsUp, ThumbsDown, FileText } from 'lucide-react';
import { useRAGChat } from '@/features/chat/hooks/useRAGChat';
import { resolvePreferredModelName, useChatModels } from '@/features/chat/hooks/useChatModels';
import { useToast } from '@/shared/hooks/useToast';
import { getAssistantRenderableText, hasAssistantActionBar, hasAssistantVisiblePayload } from '@/features/chat/lib/assistant-message';
import { assertChatUIMode, type ChatUIMode } from '@/shared/contracts/chat-ui-mode';
import { saveConversationToNoteById } from '@/shared/utils/noteUtils';
import { api } from '@/shared/api/client';
import styles from './ChatDetailPage.module.css';

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

export default function ChatDetail() {
  const { chatId } = useParams();
  const navigate = useNavigate();
  const toast = useToast();
  const [isMobile, setIsMobile] = useState(() => {
    const isNarrowViewport = window.innerWidth <= 768;
    const isTouchDevice = window.matchMedia('(pointer: coarse)').matches;
    return isNarrowViewport && isTouchDevice;
  });
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);

  useEffect(() => {
    const check = () => {
      const isNarrowViewport = window.innerWidth <= 768;
      const isTouchDevice = window.matchMedia('(pointer: coarse)').matches;
      setIsMobile(isNarrowViewport && isTouchDevice);
    };
    window.addEventListener('resize', check);
    return () => window.removeEventListener('resize', check);
  }, []);
  const [inputMessage, setInputMessage] = useState('');
  const [uiMode, setUiMode] = useState<ChatUIMode>('normal');
  const [selectedModelName, setSelectedModelName] = useState<string | undefined>(undefined);
  const [hasLoadedSessionConfig, setHasLoadedSessionConfig] = useState(false);
  
  // 消息反馈状态
  const [copiedMessages, setCopiedMessages] = useState<Set<string>>(new Set());
  const [likedMessages, setLikedMessages] = useState<Set<string>>(new Set());
  const [dislikedMessages, setDislikedMessages] = useState<Set<string>>(new Set());
  const [savedToNotes, setSavedToNotes] = useState<Set<string>>(new Set()); // 已保存到笔记的消息ID
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
  
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const chatContainerRef = useRef<HTMLDivElement>(null);
  const shouldAutoScrollRef = useRef(true);

  // 处理滚动事件
  const handleScroll = React.useCallback(() => {
    if (chatContainerRef.current) {
      const { scrollTop, scrollHeight, clientHeight } = chatContainerRef.current;
      // 如果距离底部小于 100px，则认为在底部
      const isAtBottom = scrollHeight - scrollTop - clientHeight < 100;
      shouldAutoScrollRef.current = isAtBottom;
    }
  }, []);

  const scrollToBottom = React.useCallback((behavior: ScrollBehavior = 'auto') => {
    if (chatContainerRef.current) {
      chatContainerRef.current.scrollTo({
        top: chatContainerRef.current.scrollHeight,
        behavior,
      });
      return;
    }
    messagesEndRef.current?.scrollIntoView({ behavior, block: 'end' });
  }, []);
  
  // RAG Chat Hook
  const {
    models: chatModels,
    defaultModelName,
  } = useChatModels();

  useEffect(() => {
    if (chatId && !hasLoadedSessionConfig) {
      return;
    }
    const nextModelName = resolvePreferredModelName(chatModels, selectedModelName, {
      defaultModelName,
    });
    if (!nextModelName || nextModelName === selectedModelName) {
      return;
    }

    const previousModelName = (selectedModelName || '').trim();
    setSelectedModelName(nextModelName);

    if (!chatId || !hasLoadedSessionConfig || !previousModelName) {
      return;
    }

    void api.updateChatSessionConfig(chatId, { uiMode, modelName: nextModelName }).catch((error) => {
      console.error('Failed to normalize stale model config:', error);
    });
  }, [chatId, chatModels, defaultModelName, hasLoadedSessionConfig, selectedModelName, uiMode]);

  const {
    messages,
    isStreaming,
    sendMessage,
    regenerateLastMessage,
    stopGeneration,
  } = useRAGChat({
    sessionId: chatId,
    modelName: selectedModelName,
    uiMode,
    onError: (error) => {
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
    },
    onStopComplete: () => toast.info('已停止生成')  // 停止生成完成时显示提示
  });

  useEffect(() => {
    let cancelled = false;

    const loadSessionConfig = async () => {
      if (!chatId) {
        setHasLoadedSessionConfig(true);
        return;
      }
      try {
        const session = await api.getChatSession(chatId);
        if (cancelled) {
          return;
        }
        setUiMode(assertChatUIMode(session.config?.uiMode, 'session.config.uiMode'));
        setSelectedModelName(session.config?.modelName);
      } catch (error) {
        if (!cancelled) {
          console.error('Failed to load chat session config:', error);
        }
      } finally {
        if (!cancelled) {
          setHasLoadedSessionConfig(true);
        }
      }
    };

    setHasLoadedSessionConfig(false);
    loadSessionConfig();

    return () => {
      cancelled = true;
    };
  }, [chatId]);

  const handleModelChange = async (nextValue: string) => {
    setSelectedModelName(nextValue);
    if (!chatId) return;

    try {
      await api.updateChatSessionConfig(chatId, { uiMode, modelName: nextValue });
    } catch (error) {
      console.error('Failed to update model config:', error);
      toast.error('更新模型设置失败');
    }
  };

  const handleUIModeChange = async (nextMode: ChatUIMode) => {
    if (nextMode === uiMode) {
      return;
    }

    const previousMode = uiMode;
    setUiMode(nextMode);
    if (!chatId) {
      return;
    }

    try {
      await api.updateChatSessionConfig(chatId, { uiMode: nextMode });
    } catch (error) {
      console.error('Failed to update ui mode config:', error);
      toast.error('更新对话模式失败');
      setUiMode(previousMode);
    }
  };

  // 历史消息加载完成后滚动到底部
  React.useEffect(() => {
    if (messages.length > 0) {
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          scrollToBottom('smooth');
        });
      });
    }
  }, [messages.length, scrollToBottom]); // 只在消息数量变化时触发

  // 流式传输时的自动滚动
  React.useEffect(() => {
    if (isStreaming && shouldAutoScrollRef.current) {
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          scrollToBottom('auto');
        });
      });
    }
  }, [isStreaming, messages, scrollToBottom]);

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

  const handleSendMessage = () => {
    if (!inputMessage.trim() || isStreaming) return;
    shouldAutoScrollRef.current = true; // 发送新消息时强制滚动
    sendMessage(inputMessage);
    setInputMessage('');
    // 清除配额超限弹窗（如果有的话）
    if (quotaExceededModal.isOpen) {
      setQuotaExceededModal({ ...quotaExceededModal, isOpen: false });
    }
  };

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
      setCopiedMessages(prev => new Set(prev).add(messageId));
      setTimeout(() => {
        setCopiedMessages(prev => {
          const next = new Set(prev);
          next.delete(messageId);
          return next;
        });
      }, 2000);
    } catch (err) {
      console.error('复制失败:', err);
      toast.error('复制失败，请重试');
    }
  };

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
    } catch (error) {
      console.error('保存到笔记失败:', error);
      toast.error('保存失败，请重试');
    }
  };

  return (
    <div className={styles.chatDetail}>
      {/* Mobile Sidebar Overlay */}
      {isMobile && isSidebarOpen && (
        <div className={styles.overlay} onClick={() => setIsSidebarOpen(false)} />
      )}
      
      {/* Sidebar */}
      <div className={`${styles.sidebarContainer} ${isMobile && isSidebarOpen ? styles.open : ''}`}>
        <Sidebar
          onNewChat={() => navigate('/')}
          onSelectChat={(id) => navigate(`/chat/${id}`)}
          selectedChatId={chatId}
        />
      </div>

      {/* Main Content */}
      <div className={styles.mainContent}>
        {/* Header */}
        <div className={styles.header}>
          {isMobile && (
            <button onClick={() => setIsSidebarOpen(true)} className={styles.menuButton}>
              <Menu size={24} />
            </button>
          )}
          <button onClick={() => navigate('/')} className={styles.backButton}>
            <ArrowLeft size={20} />
          </button>
          <h1 className={styles.title}>对话详情</h1>
        </div>

        {/* Messages Area */}
        <div 
          className={styles.messagesContainer}
          ref={chatContainerRef}
          onScroll={handleScroll}
        >
	          {messages.map((msg, index) => {
	            const assistantText = msg.role === 'assistant'
	              ? getAssistantRenderableText(msg)
	              : '';
	            const hasAssistantActions = msg.role === 'assistant'
	              && hasAssistantActionBar(msg)
	              && (!isStreaming || index !== messages.length - 1);

	            return (
	              <div
	                key={msg.id}
	                className={msg.role === 'user' ? styles.userMessage : styles.aiMessage}
	              >
	                {msg.role === 'assistant'
	                  && !hasAssistantVisiblePayload(msg)
	                  && isStreaming
	                  && index === messages.length - 1 && (
	                  <div className={styles.thinking}>
	                    <div className={styles.thinkingDots}>
	                      <span className={styles.dot}></span>
	                      <span className={styles.dot}></span>
	                      <span className={styles.dot}></span>
	                    </div>
	                  </div>
	                )}

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
	                      <div className={styles.messageContent}>
	                        {msg.content}
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
	                      contentClassName={styles.messageContent}
	                      isStreaming={isStreaming && index === messages.length - 1}
	                    />
	                    <AssistantMessageInterruption interruption={msg.interruption} />
	                    <AssistantArtifactList
	                      artifacts={msg.artifacts}
	                      sessionId={chatId}
	                      messageId={msg.id}
	                    />
	                  </>
	                )}

	                {hasAssistantActions && (
	                  <div className={styles.messageActions}>
	                    {assistantText && (
	                      <>
	                        <button
	                          className={`${styles.actionButton} ${copiedMessages.has(msg.id) ? styles.copied : ''}`}
	                          onClick={() => handleCopyMessage(assistantText, msg.id)}
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
	                      </>
	                    )}
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
	                        </div>
	                      )}
	                    </div>
	                  </div>
	                )}
	              </div>
	            );
	          })}
          {isStreaming && (
            <div className={styles.streamingIndicator}>
              <div className={styles.loadingDots}>
                <span>.</span><span>.</span><span>.</span>
              </div>
            </div>
          )}
          <div ref={messagesEndRef} style={{ height: 0, overflow: 'hidden' }} />
        </div>

        {/* Input Area */}
        <div className={styles.inputArea}>
          <div className={styles.inputControls}>
            <ChatModelSelector
              models={chatModels}
              value={selectedModelName}
              onChange={handleModelChange}
              disabled={isStreaming}
              defaultModelName={defaultModelName}
            />
            <ChatUIModeSwitch
              value={uiMode}
              onChange={handleUIModeChange}
              disabled={isStreaming}
            />
          </div>
          <div className={styles.inputRow}>
            <input
              value={inputMessage}
              onChange={(e) => setInputMessage(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey && !isStreaming) {
                  handleSendMessage();
                }
              }}
              placeholder="继续对话..."
              className={styles.input}
              disabled={isStreaming}
            />
            <SendStopButton
              isStreaming={isStreaming}
              disabled={false}
              onSend={handleSendMessage}
              onStop={stopGeneration}
              hasContent={!!inputMessage.trim()}
            />
          </div>
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
