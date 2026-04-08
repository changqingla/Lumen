import { useState, useEffect, useCallback } from 'react';
import { api, type ChatSessionConfig } from '@/shared/api/client';

export interface ChatSession {
  id: string;
  title: string;
  lastMessage: string;
  timestamp: string; // 显示用的相对时间（如"2小时前"）
  createdAt: string; // ISO日期字符串，用于分类逻辑
  config?: ChatSessionConfig;
}

export function useChatSessions() {
  const [chatSessions, setChatSessions] = useState<ChatSession[]>([]);
  const [loading, setLoading] = useState(false);

  const loadChatSessions = useCallback(async () => {
    try {
      setLoading(true);
      const response = await api.listChatSessions(1, 50);
      setChatSessions(response.sessions);
    } catch (error) {
      console.error('Failed to load chat sessions:', error);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadChatSessions();
  }, [loadChatSessions]);

  return {
    chatSessions,
    loading,
    refreshSessions: loadChatSessions
  };
}
