import { useState, useCallback, useRef, useEffect } from 'react';
import { ragAPI } from '@/lib/rag-api';
import { api } from '@/lib/api';
import { generateUUID } from '@/lib/uuid';

interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  thinking?: string; // 思考过程
  documentSummaries?: Array<{  // 文档总结信息（用于历史消息恢复）
    doc_id: string;
    doc_name: string;
    summary: string;
    from_cache: boolean;
  }>;
  // 截断元数据（用户中止生成时）
  wasTruncated?: boolean;  // 是否被用户中止
  truncatedAt?: string;    // 中止时间戳
}

interface UseRAGChatOptions {
  sessionId?: string; // 已存在的会话ID
  kbId?: string;
  docIds?: string[];
  mode?: 'deep' | 'search';
  enableWebSearch?: boolean; // 是否启用联网搜索
  showThinking?: boolean; // 是否显示思考过程（深度思考模式）
  sourceType?: 'home' | 'knowledge' | 'favorites'; // 会话来源类型
  onError?: (error: string) => void;
  onSessionCreated?: (sessionId: string) => void; // 新会话创建时的回调
  onFirstContentToken?: (messageId: string) => void; // 第一个content token到达时的回调
  onStopComplete?: () => void; // 用户停止生成完成时的回调
}

// 全局维护每个 session 的活跃流式状态（跨组件实例共享）
// 问题3修复：添加最大容量限制和过期清理机制，防止内存泄漏
const MAX_ACTIVE_STREAMS = 10;
const STREAM_EXPIRY_MS = 30 * 60 * 1000; // 30分钟过期

interface ActiveStreamEntry {
  message: Message;
  isStreaming: boolean;
  firstContentTokenFired: boolean;
  timestamp: number; // 添加时间戳用于过期清理
}

const globalActiveStreams = new Map<string, ActiveStreamEntry>();

// 清理过期的流式状态
const cleanupExpiredStreams = () => {
  const now = Date.now();
  const expiredKeys: string[] = [];
  
  globalActiveStreams.forEach((entry, key) => {
    if (now - entry.timestamp > STREAM_EXPIRY_MS) {
      expiredKeys.push(key);
    }
  });
  
  expiredKeys.forEach(key => {
    globalActiveStreams.delete(key);
  });
  
  // 如果超过最大容量，删除最旧的条目
  if (globalActiveStreams.size > MAX_ACTIVE_STREAMS) {
    const entries = Array.from(globalActiveStreams.entries())
      .sort((a, b) => a[1].timestamp - b[1].timestamp);
    
    const toDelete = entries.slice(0, globalActiveStreams.size - MAX_ACTIVE_STREAMS);
    toDelete.forEach(([key]) => globalActiveStreams.delete(key));
  }
};

// 统一的全局状态清理函数
const cleanupGlobalActiveStream = (sessionId: string | null | undefined) => {
  if (sessionId) {
    globalActiveStreams.delete(sessionId);
  }
  // 每次清理时也检查过期条目
  cleanupExpiredStreams();
};

export function useRAGChat(options: UseRAGChatOptions = {}) {
  const {
    sessionId: externalSessionId,
    kbId,
    docIds,
    mode = 'deep',
    enableWebSearch = false,
    showThinking = true,  // 默认显示思考过程
    sourceType = 'home',
    onError,
    onSessionCreated,
    onFirstContentToken,
    onStopComplete
  } = options;
  const [messages, setMessages] = useState<Message[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [isStopping, setIsStopping] = useState(false); // 是否正在停止
  const [isLoading, setIsLoading] = useState(false);
  
  // 统一的会话ID管理
  const currentSessionId = externalSessionId || null;
  const currentMessageRef = useRef<Message | null>(null);
  const firstContentTokenFiredRef = useRef<boolean>(false);
  const isSendingRef = useRef<boolean>(false);
  const abortControllerRef = useRef<AbortController | null>(null);
  const wasStoppedByUserRef = useRef<boolean>(false); // 是否由用户主动停止

  // 使用 ref 存储状态，避免 sendMessage 依赖它们而频繁重建
  const currentSessionIdRef = useRef<string | null>(currentSessionId);
  const isStreamingRef = useRef<boolean>(isStreaming);
  const onErrorRef = useRef(onError);
  const onSessionCreatedRef = useRef(onSessionCreated);
  const onFirstContentTokenRef = useRef(onFirstContentToken);
  const onStopCompleteRef = useRef(onStopComplete);

  // 同步 currentSessionId、isStreaming 和回调函数到 ref
  useEffect(() => {
    currentSessionIdRef.current = currentSessionId;
    isStreamingRef.current = isStreaming;
    onErrorRef.current = onError;
    onSessionCreatedRef.current = onSessionCreated;
    onFirstContentTokenRef.current = onFirstContentToken;
    onStopCompleteRef.current = onStopComplete;
  }, [currentSessionId, isStreaming, onError, onSessionCreated, onFirstContentToken, onStopComplete]);

  // 定义加载消息的函数
  const loadMessages = useCallback(async (sessionId: string) => {
    try {
      setIsLoading(true);
      const response = await api.getChatMessages(sessionId);
      const loadedMessages: Message[] = response.messages.map(msg => ({
        id: msg.id,
        role: msg.role as 'user' | 'assistant',
        content: msg.content,
        thinking: msg.thinking || '',
        documentSummaries: msg.documentSummaries
      }));
      setMessages(loadedMessages);
    } catch (error) {
      console.error('Failed to load messages:', error);
      onErrorRef.current?.('加载历史消息失败');
    } finally {
      setIsLoading(false);
    }
  }, []);

  // 加载历史消息并附加活跃的流式消息
  const loadMessagesWithActiveStream = useCallback(async (sessionId: string, streamingMessage: Message) => {
    try {
      setIsLoading(true);
      const response = await api.getChatMessages(sessionId);
      const loadedMessages: Message[] = response.messages.map(msg => ({
        id: msg.id,
        role: msg.role as 'user' | 'assistant',
        content: msg.content,
        thinking: msg.thinking || '',
        documentSummaries: msg.documentSummaries
      }));
      // 检查是否已经有相同的助手消息（避免重复）
      const lastMessage = loadedMessages[loadedMessages.length - 1];
      const isDuplicate = lastMessage &&
                         lastMessage.role === 'assistant' &&
                         streamingMessage.role === 'assistant' &&
                         lastMessage.content === streamingMessage.content &&
                         lastMessage.thinking === streamingMessage.thinking;

      if (isDuplicate) {
        // 如果最后一条消息与流式消息完全相同，只加载数据库消息
        setMessages(loadedMessages);
      } else {
        // 否则追加流式消息（流式消息可能包含更新的内容）
        setMessages([...loadedMessages, streamingMessage]);
      }
    } catch (error) {
      console.error('Failed to load messages:', error);
      onErrorRef.current?.('加载历史消息失败');
    } finally {
      setIsLoading(false);
    }
  }, []);

  // 简化的会话加载逻辑 - 只在会话ID变化时加载
  const prevSessionIdRef = useRef<string | null>(null);

  useEffect(() => {
    if (currentSessionId !== prevSessionIdRef.current) {
      const oldSessionId = prevSessionIdRef.current;

      // 保存旧会话的流式状态（只要有 currentMessageRef 就保存）
      if (oldSessionId && currentMessageRef.current) {
        globalActiveStreams.set(oldSessionId, {
          message: currentMessageRef.current,
          isStreaming: true,
          firstContentTokenFired: firstContentTokenFiredRef.current,
          timestamp: Date.now() // 添加时间戳
        });
        // 清理过期条目
        cleanupExpiredStreams();
      }

      // 更新会话ID引用
      prevSessionIdRef.current = currentSessionId;

      if (currentSessionId) {
        // 检查新会话是否有活跃的流式输出
        const activeStream = globalActiveStreams.get(currentSessionId);

        if (activeStream) {
          // 恢复流式状态
          currentMessageRef.current = activeStream.message;
          setIsStreaming(true);
          firstContentTokenFiredRef.current = activeStream.firstContentTokenFired;
          loadMessagesWithActiveStream(currentSessionId, activeStream.message);
        } else {
          // 正常加载历史消息
          currentMessageRef.current = null;
          setIsStreaming(false);
          firstContentTokenFiredRef.current = false;
          loadMessages(currentSessionId);
        }
      } else {
        // 清空会话
        currentMessageRef.current = null;
        firstContentTokenFiredRef.current = false;
        setMessages([]);
        setIsStreaming(false);
      }
    }
  }, [currentSessionId, loadMessages, loadMessagesWithActiveStream]);

  const sendMessage = useCallback(async (
    content: string
  ) => {
    // 使用 ref 检查 isStreaming，避免依赖数组包含它
    if (!content.trim() || isStreamingRef.current) return;

    // 防止并发发送消息
    if (isSendingRef.current) {
      console.warn('已有消息正在发送中，请稍候');
      return;
    }

    isSendingRef.current = true;

    // 从 ref 读取当前会话ID
    let targetSessionId = currentSessionIdRef.current;

    try {
      // 如果没有会话ID，先创建会话
      if (!targetSessionId) {
        const sessionConfig = {
          kbIds: kbId ? [kbId] : [],
          docIds: docIds || [],
          sourceType,
          isKBLocked: !!(kbId || (docIds && docIds.length > 0)),
          allowWebSearch: enableWebSearch
        };

        const session = await api.createChatSession(content, sessionConfig);
        targetSessionId = session.id;

        // 🔑 关键：立即更新所有会话ID引用，确保流式回调能正确识别当前会话
        prevSessionIdRef.current = session.id;
        currentSessionIdRef.current = session.id;

        // 触发回调通知外部组件会话已创建
        onSessionCreatedRef.current?.(session.id);
      }

      // Add user message to UI
      const userMessage: Message = {
        id: generateUUID(),
        role: 'user',
        content
      };
      setMessages(prev => [...prev, userMessage]);

      await api.addChatMessage(targetSessionId, 'user', content, undefined, mode);

      const assistantMessage: Message = {
        id: generateUUID(),
        role: 'assistant',
        content: '',
        thinking: ''
      };
      currentMessageRef.current = assistantMessage;
      firstContentTokenFiredRef.current = false;
      setMessages(prev => [...prev, assistantMessage]);

      setIsStreaming(true);

      // 创建新的 AbortController
      abortControllerRef.current = new AbortController();

      // 流式输出
      await ragAPI.streamChat({
        kb_id: kbId,
        doc_ids: docIds,
        message: content,
        session_id: targetSessionId,
        mode,
        enable_web_search: enableWebSearch,
        show_thinking: showThinking,  // 传递深度思考模式开关
        signal: abortControllerRef.current.signal,
        onThinking: (thinking) => {
          if (isSendingRef.current) {
            isSendingRef.current = false;
          }

          assistantMessage.thinking = (assistantMessage.thinking || '') + thinking;

          // 检查用户是否切换到了其他会话
          if (currentSessionIdRef.current === targetSessionId) {
            if (currentMessageRef.current) {
              currentMessageRef.current.thinking = assistantMessage.thinking;
            }
            setMessages(prev => [...prev.slice(0, -1), { ...assistantMessage }]);
          }
        },
        onToken: (token) => {
          if (isSendingRef.current) {
            isSendingRef.current = false;
          }

          assistantMessage.content += token;

          if (currentSessionIdRef.current === targetSessionId) {
            if (!firstContentTokenFiredRef.current && assistantMessage.thinking) {
              firstContentTokenFiredRef.current = true;
              onFirstContentTokenRef.current?.(assistantMessage.id);
            }

            if (currentMessageRef.current) {
              currentMessageRef.current.content = assistantMessage.content;
            }
            setMessages(prev => [...prev.slice(0, -1), { ...assistantMessage }]);
          }
        },
        onFinalAnswer: () => {
          // final_answer 事件仅包含 answer + session_id，无需额外处理
        },
        onError: (error) => {
          if (currentSessionIdRef.current === targetSessionId) {
            onErrorRef.current?.(error);
            setIsStreaming(false);
          }
          cleanupGlobalActiveStream(targetSessionId);
          abortControllerRef.current = null;
          isSendingRef.current = false;
        },
        onDone: async () => {
          const wasTruncated = wasStoppedByUserRef.current;
          const truncatedAt = wasTruncated ? new Date().toISOString() : undefined;
          
          // 重置用户停止标记
          wasStoppedByUserRef.current = false;
          
          const messageToSave = {
            content: assistantMessage.content,
            thinking: assistantMessage.thinking
          };

          // 只有在有内容时才保存消息
          if (messageToSave.content || messageToSave.thinking) {
            try {
              await api.addChatMessage(
                targetSessionId!,
                'assistant',
                messageToSave.content,
                messageToSave.thinking,
                mode,
                undefined,
                wasTruncated ? { wasTruncated, truncatedAt } : undefined
              );
              
              // 如果是用户主动停止，触发停止完成回调
              if (wasTruncated && currentSessionIdRef.current === targetSessionId) {
                onStopCompleteRef.current?.();
              }
            } catch (error) {
              console.error('Failed to save assistant message:', error);
              if (currentSessionIdRef.current === targetSessionId) {
                onErrorRef.current?.('消息保存失败，请重试');
              }
            }
          } else if (wasTruncated && currentSessionIdRef.current === targetSessionId) {
            // 即使没有内容，如果是用户主动停止也触发回调
            onStopCompleteRef.current?.();
          }

          cleanupGlobalActiveStream(targetSessionId);
          abortControllerRef.current = null;

          if (currentSessionIdRef.current === targetSessionId) {
            setIsStreaming(false);
            setIsStopping(false); // 重置停止状态
            currentMessageRef.current = null;
          }

          isSendingRef.current = false;
        }
      });
    } catch (error) {
      if (currentSessionIdRef.current === targetSessionId) {
        onErrorRef.current?.(String(error));
        setIsStreaming(false);
        setMessages(prev => prev.slice(0, -1));
      }

      cleanupGlobalActiveStream(targetSessionId);
      abortControllerRef.current = null;
      isSendingRef.current = false;
    }
  }, [kbId, docIds, mode, enableWebSearch, showThinking, sourceType]);

  const regenerateLastMessage = useCallback(async () => {
    if (messages.length < 2) return;

    if (isStreamingRef.current) {
      console.warn('正在流式输出，无法重新生成');
      return;
    }

    if (isSendingRef.current) {
      console.warn('已有消息正在发送中，请稍候');
      return;
    }

    isSendingRef.current = true;

    const lastUserMessage = messages.slice().reverse().find(m => m.role === 'user');
    if (!lastUserMessage) {
      isSendingRef.current = false;
      return;
    }

    const targetSessionId = currentSessionIdRef.current;
    if (!targetSessionId) {
      console.warn('没有会话ID，无法重新生成');
      isSendingRef.current = false;
      return;
    }

    try {
      try {
        await api.deleteLastAssistantMessage(targetSessionId);
      } catch (error) {
        console.error('Failed to delete old assistant message:', error);
      }

      setMessages(prev => {
        const lastAssistantIndex = prev.map(m => m.role).lastIndexOf('assistant');
        if (lastAssistantIndex === -1) return prev;
        return prev.slice(0, lastAssistantIndex);
      });

      const assistantMessage: Message = {
        id: generateUUID(),
        role: 'assistant',
        content: '',
        thinking: ''
      };
      currentMessageRef.current = assistantMessage;
      firstContentTokenFiredRef.current = false;
      setMessages(prev => [...prev, assistantMessage]);

      setIsStreaming(true);

      // 创建新的 AbortController
      abortControllerRef.current = new AbortController();

      await ragAPI.streamChat({
        kb_id: kbId,
        doc_ids: docIds,
        message: lastUserMessage.content,
        session_id: targetSessionId,
        mode,
        enable_web_search: enableWebSearch,
        show_thinking: showThinking,
        signal: abortControllerRef.current.signal,
        onThinking: (thinking) => {
          if (isSendingRef.current) isSendingRef.current = false;
          assistantMessage.thinking = (assistantMessage.thinking || '') + thinking;
          if (currentSessionIdRef.current === targetSessionId) {
            if (currentMessageRef.current) {
              currentMessageRef.current.thinking = assistantMessage.thinking;
            }
            setMessages(prev => [...prev.slice(0, -1), { ...assistantMessage }]);
          }
        },
        onToken: (token) => {
          if (isSendingRef.current) isSendingRef.current = false;
          assistantMessage.content += token;
          if (currentSessionIdRef.current === targetSessionId) {
            if (!firstContentTokenFiredRef.current && assistantMessage.thinking) {
              firstContentTokenFiredRef.current = true;
              onFirstContentTokenRef.current?.(assistantMessage.id);
            }
            if (currentMessageRef.current) {
              currentMessageRef.current.content = assistantMessage.content;
            }
            setMessages(prev => [...prev.slice(0, -1), { ...assistantMessage }]);
          }
        },
        onFinalAnswer: () => {
          // final_answer 事件仅包含 answer + session_id，无需额外处理
        },
        onError: (error) => {
          if (currentSessionIdRef.current === targetSessionId) {
            onErrorRef.current?.(error);
            setIsStreaming(false);
          }
          cleanupGlobalActiveStream(targetSessionId);
          abortControllerRef.current = null;
          isSendingRef.current = false;
        },
        onDone: async () => {
          const wasTruncated = wasStoppedByUserRef.current;
          const truncatedAt = wasTruncated ? new Date().toISOString() : undefined;
          
          // 重置用户停止标记
          wasStoppedByUserRef.current = false;
          
          // 只有在有内容时才保存消息
          if (assistantMessage.content || assistantMessage.thinking) {
            try {
              await api.addChatMessage(
                targetSessionId,
                'assistant',
                assistantMessage.content,
                assistantMessage.thinking,
                mode,
                undefined,
                wasTruncated ? { wasTruncated, truncatedAt } : undefined
              );
              
              // 如果是用户主动停止，触发停止完成回调
              if (wasTruncated && currentSessionIdRef.current === targetSessionId) {
                onStopCompleteRef.current?.();
              }
            } catch (error) {
              console.error('Failed to save regenerated message:', error);
              if (currentSessionIdRef.current === targetSessionId) {
                onErrorRef.current?.('消息保存失败，请重试');
              }
            }
          } else if (wasTruncated && currentSessionIdRef.current === targetSessionId) {
            // 即使没有内容，如果是用户主动停止也触发回调
            onStopCompleteRef.current?.();
          }
          cleanupGlobalActiveStream(targetSessionId);
          abortControllerRef.current = null;
          if (currentSessionIdRef.current === targetSessionId) {
            setIsStreaming(false);
            setIsStopping(false); // 重置停止状态
            currentMessageRef.current = null;
          }
          isSendingRef.current = false;
        }
      });
    } catch (error) {
      if (currentSessionIdRef.current === targetSessionId) {
        onErrorRef.current?.(String(error));
        setIsStreaming(false);
        // 恢复之前的消息状态
        setMessages(prev => prev.slice(0, -1));
      }
      cleanupGlobalActiveStream(targetSessionId);
      abortControllerRef.current = null;
      isSendingRef.current = false;
    }
  }, [messages, kbId, docIds, mode, enableWebSearch, showThinking]);

  const clearMessages = useCallback(() => {
    setMessages([]);
    currentMessageRef.current = null;
    setIsStreaming(false);
    isSendingRef.current = false;
    firstContentTokenFiredRef.current = false;
  }, []);

  // 停止生成（中止流式输出）
  const stopGeneration = useCallback(async () => {
    if (abortControllerRef.current) {
      setIsStopping(true); // 标记正在停止
      wasStoppedByUserRef.current = true; // 标记为用户主动停止
      
      // 先发送取消请求到后端（如果有会话ID）
      const sessionId = currentSessionIdRef.current;
      if (sessionId) {
        try {
          await ragAPI.cancelStream(sessionId);
        } catch (error) {
          console.warn('Failed to send cancel request to backend:', error);
        }
      }
      
      // 然后中止前端的fetch请求
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    setIsStreaming(false);
    isSendingRef.current = false;
    cleanupGlobalActiveStream(currentSessionIdRef.current);
  }, []);

  // 组件卸载时清理全局状态
  useEffect(() => {
    return () => {
      // 使用 ref 获取最新的会话ID，避免闭包陷阱
      cleanupGlobalActiveStream(currentSessionIdRef.current);
      // 中止任何正在进行的请求
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }
    };
  }, []);

  return {
    messages,
    isStreaming,
    isStopping, // 是否正在停止
    isLoading,
    sendMessage,
    regenerateLastMessage,
    clearMessages,
    stopGeneration, // 停止生成
    sessionId: currentSessionId // ✅ 返回统一的会话ID
  };
}

