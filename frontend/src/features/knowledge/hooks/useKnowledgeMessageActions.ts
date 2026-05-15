import { useCallback, useState } from 'react';
import type { ToastContextType } from '@/shared/hooks/toastContext';
import { copyTextToClipboard } from '@/shared/utils/clipboard';
import { saveConversationToNoteById } from '@/shared/utils/noteUtils';
import type { Message as NoteMessage } from '@/shared/utils/noteUtils';

interface UseKnowledgeMessageActionsOptions<Message extends NoteMessage> {
  messages: Message[];
  isGuestMode: boolean;
  promptLogin: (options: { title: string; message: string; confirmText: string }) => void;
  toast: ToastContextType;
}

export function useKnowledgeMessageActions<Message extends NoteMessage>({
  messages,
  isGuestMode,
  promptLogin,
  toast,
}: UseKnowledgeMessageActionsOptions<Message>) {
  const [likedMessages, setLikedMessages] = useState<Set<string>>(new Set());
  const [dislikedMessages, setDislikedMessages] = useState<Set<string>>(new Set());
  const [savedToNotes, setSavedToNotes] = useState<Set<string>>(new Set());
  const [copiedMessages, setCopiedMessages] = useState<Set<string>>(new Set());

  const copyMessage = useCallback(async (content: string, messageId: string) => {
    try {
      await copyTextToClipboard(content);
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
  }, [toast]);

  const likeMessage = useCallback((messageId: string) => {
    setLikedMessages(prev => {
      const next = new Set(prev);
      if (next.has(messageId)) {
        next.delete(messageId);
      } else {
        next.add(messageId);
        setDislikedMessages(current => {
          const disliked = new Set(current);
          disliked.delete(messageId);
          return disliked;
        });
      }
      return next;
    });
  }, []);

  const dislikeMessage = useCallback((messageId: string) => {
    setDislikedMessages(prev => {
      const next = new Set(prev);
      if (next.has(messageId)) {
        next.delete(messageId);
      } else {
        next.add(messageId);
        setLikedMessages(current => {
          const liked = new Set(current);
          liked.delete(messageId);
          return liked;
        });
      }
      return next;
    });
  }, []);

  const saveMessageToNotes = useCallback(async (messageId: string) => {
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
  }, [isGuestMode, messages, promptLogin, savedToNotes, toast]);

  return {
    copiedMessages,
    dislikedMessages,
    likedMessages,
    savedToNotes,
    copyMessage,
    dislikeMessage,
    likeMessage,
    saveMessageToNotes,
  };
}
