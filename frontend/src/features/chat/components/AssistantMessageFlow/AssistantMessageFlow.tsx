import { useMemo } from 'react';
import type { ChatToolTrace } from '@/shared/api/client';
import {
  buildProcessingBlockTraces,
  groupAssistantTupleMessages,
  type AssistantTupleMessage,
} from '@/features/chat/lib/assistant-flow';
import OptimizedMarkdown from '@/shared/components/OptimizedMarkdown';
import ThinkingTrace from '../ThinkingTrace/ThinkingTrace';
import ToolTraceList from '../ToolTraceList/ToolTraceList';
import styles from './AssistantMessageFlow.module.css';

const EMPTY_TOOL_TRACES: ChatToolTrace[] = [];

interface AssistantMessageFlowProps {
  tupleMessages?: AssistantTupleMessage[];
  content?: string;
  thinking?: string;
  toolTraces?: ChatToolTrace[];
  contentClassName: string;
  isStreaming?: boolean;
}

interface StreamingMarkdownContentProps {
  body: string;
  showCaret: boolean;
}

function StreamingMarkdownContent({ body, showCaret }: StreamingMarkdownContentProps) {
  const displayBody = showCaret ? `${body}▌` : body;

  return (
    <div className={styles.streamingContent}>
      <div className={styles.streamingPlainText}>
        {displayBody}
      </div>
    </div>
  );
}

const isVisibleToolTrace = (trace: ChatToolTrace): boolean => (
  trace.name !== 'ask_clarification'
);

export default function AssistantMessageFlow({
  tupleMessages,
  content,
  thinking,
  toolTraces,
  contentClassName,
  isStreaming = false,
}: AssistantMessageFlowProps) {
  const normalizedToolTraces = toolTraces ?? EMPTY_TOOL_TRACES;
  const visibleToolTraces = useMemo(
    () => normalizedToolTraces.filter(isVisibleToolTrace),
    [normalizedToolTraces],
  );

  const tupleBlocks = useMemo(() => {
    if (!tupleMessages || tupleMessages.length === 0) {
      return [];
    }
    return groupAssistantTupleMessages(tupleMessages);
  }, [tupleMessages]);
  const hasTupleBlocks = tupleBlocks.length > 0;
  const hasTupleContentBlocks = tupleBlocks.some(
    (block) => block.type === 'content' && Boolean((block.content || '').trim()),
  );

  const tupleProcessingTraces = useMemo(() => (
    tupleBlocks
      .filter((block) => block.type === 'processing')
      .flatMap((block) => buildProcessingBlockTraces(block.messages, visibleToolTraces))
      .filter(isVisibleToolTrace)
  ), [tupleBlocks, visibleToolTraces]);

  const isTraceRunning = (trace: ChatToolTrace): boolean => {
    if (trace.success === true || trace.success === false) {
      return false;
    }
    if (trace.error && trace.error.trim()) {
      return false;
    }
    if (trace.result !== undefined) {
      return false;
    }

    const normalizedStatus = (trace.status || '').trim().toLowerCase();
    if (!normalizedStatus) {
      return true;
    }

    if (
      [
        'success',
        'completed',
        'done',
        'error',
        'failed',
        'interrupted',
        'cancelled',
        'canceled',
        'awaiting_input',
        'needs_user_input',
        'waiting_for_input',
      ].includes(normalizedStatus)
    ) {
      return false;
    }
    return true;
  };

  const hasVisibleContent = (
    Boolean((thinking || '').trim())
    || tupleBlocks.some((block) => block.type === 'content' && Boolean((block.content || '').trim()))
    || Boolean((content || '').trim())
  );
  const tracesForStatus = hasTupleBlocks
    ? tupleProcessingTraces
    : visibleToolTraces;
  const runningTraces = tracesForStatus.filter((trace) => isTraceRunning(trace));

  const activityMode: 'thinking' | 'tool' | 'writing' = (() => {
    if (runningTraces.length > 0) {
      return 'tool';
    }
    if (hasVisibleContent) {
      return 'writing';
    }
    return 'thinking';
  })();

  const renderStreamingStatus = () => (
    isStreaming ? (
      <div
        className={`${styles.liveStatus} ${styles[`mode${activityMode[0].toUpperCase()}${activityMode.slice(1)}`]}`}
        role="status"
        aria-live="polite"
        aria-label="AI 正在处理中"
      >
        <span className={styles.dot} aria-hidden />
        <span className={styles.dot} aria-hidden />
        <span className={styles.dot} aria-hidden />
      </div>
    ) : null
  );

  const renderContent = (
    key: string,
    body: string,
    showCursor = false,
  ) => (
    <div key={key} className={contentClassName}>
      {(() => {
        const renderedBody = body;

        return isStreaming ? (
          <StreamingMarkdownContent body={renderedBody} showCaret={showCursor} />
        ) : (
          <OptimizedMarkdown preserveRaw deferImages>{renderedBody}</OptimizedMarkdown>
        );
      })()}
    </div>
  );

  if (hasTupleBlocks) {
    return (
      <>
        {thinking ? (
          <div className={styles.thinkingBlock}>
            <ThinkingTrace content={thinking} />
          </div>
        ) : null}
        {tupleBlocks.map((block, blockIndex) => {
          if (block.type === 'content') {
            if (!block.content) {
              return null;
            }
            return renderContent(
              `${block.id}-${blockIndex}`,
              block.content,
              activityMode === 'writing' && blockIndex === tupleBlocks.length - 1,
            );
          }

          const traces = buildProcessingBlockTraces(block.messages, visibleToolTraces)
            .filter(isVisibleToolTrace);
          if (traces.length === 0) {
            return null;
          }

          return (
            <ToolTraceList
              key={`${block.id}-traces`}
              traces={traces}
            />
          );
        })}
        {!hasTupleContentBlocks && content ? (
          renderContent(
            'tuple-fallback-content',
            content,
            activityMode === 'writing',
          )
        ) : null}
        {renderStreamingStatus()}
      </>
    );
  }

  return (
    <>
      {thinking ? (
        <div className={styles.thinkingBlock}>
          <ThinkingTrace content={thinking} />
        </div>
      ) : null}
      {visibleToolTraces.length > 0 && (
        <ToolTraceList traces={visibleToolTraces} />
      )}
      {content && (
        renderContent(
          'fallback-content',
          content,
          activityMode === 'writing',
        )
      )}
      {renderStreamingStatus()}
    </>
  );
}
