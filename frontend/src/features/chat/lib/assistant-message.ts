import type { ChatArtifact, ChatInterruption, ChatToolTrace } from '@/shared/api/client';
import {
  groupAssistantTupleMessages,
  type AssistantTupleMessage,
} from '@/features/chat/lib/assistant-flow';

interface AssistantRenderableMessage {
  content?: string;
  thinking?: string;
  interruption?: ChatInterruption | null;
  artifacts?: ChatArtifact[];
  toolTraces?: ChatToolTrace[];
  assistantTupleMessages?: AssistantTupleMessage[];
}

export const getAssistantRenderableText = (
  message: AssistantRenderableMessage,
): string => {
  const directContent = message.content || '';
  if (directContent.trim()) {
    return directContent;
  }

  const tupleMessages = message.assistantTupleMessages || [];
  if (!tupleMessages.length) {
    return '';
  }

  return groupAssistantTupleMessages(tupleMessages)
    .filter((block) => block.type === 'content')
    .map((block) => block.content)
    .join('');
};

export const hasAssistantVisiblePayload = (
  message: AssistantRenderableMessage,
): boolean => (
  Boolean((message.thinking || '').trim())
  || Boolean(getAssistantRenderableText(message).trim())
  || Boolean(message.interruption?.reason?.trim())
  || Boolean((message.artifacts || []).length > 0)
  || Boolean((message.toolTraces || []).length > 0)
  || Boolean((message.assistantTupleMessages || []).length > 0)
);

export const hasAssistantActionBar = (
  message: AssistantRenderableMessage,
): boolean => (
  Boolean(getAssistantRenderableText(message).trim())
  || Boolean(message.interruption?.reason?.trim())
  || Boolean((message.artifacts || []).length > 0)
);
