import type { ChatToolTrace } from '@/shared/api/client';
import styles from './ToolTraceList.module.css';

interface ToolTraceListProps {
  traces: ChatToolTrace[];
}

const TOOL_RESULT_PREVIEW_LIMIT = 200;

const formatPayload = (value: unknown, fallback: string) => {
  if (value === undefined || value === null) {
    return fallback;
  }
  if (typeof value === 'string') {
    const normalized = value.trim();
    if (!normalized) {
      return fallback;
    }
    try {
      return JSON.stringify(JSON.parse(normalized), null, 2);
    } catch {
      return normalized;
    }
  }
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
};

const truncatePreview = (value: string, limit = TOOL_RESULT_PREVIEW_LIMIT) => {
  const chars = Array.from(value);
  if (chars.length <= limit) {
    return value;
  }
  return `${chars.slice(0, limit).join('')}...`;
};

const getTraceState = (trace: ChatToolTrace) => {
  const normalizedStatus = (trace.status || '').trim().toLowerCase();
  if (['success', 'completed', 'done'].includes(normalizedStatus)) {
    return { label: '完成', className: styles.stateSuccess, running: false };
  }
  if (['error', 'failed', 'timed_out', 'timeout', 'cancelled', 'canceled', 'interrupted'].includes(normalizedStatus)) {
    if (normalizedStatus.includes('time')) {
      return { label: '超时', className: styles.stateError, running: false };
    }
    if (normalizedStatus === 'interrupted') {
      return { label: '中止', className: styles.stateError, running: false };
    }
    return { label: '失败', className: styles.stateError, running: false };
  }
  if (trace.success === false || (trace.error && trace.error.trim())) {
    return { label: '失败', className: styles.stateError, running: false };
  }
  if (trace.success === true) {
    return { label: '完成', className: styles.stateSuccess, running: false };
  }
  if (trace.result !== undefined) {
    return { label: '完成', className: styles.stateSuccess, running: false };
  }
  return { label: '执行中', className: styles.stateRunning, running: true };
};

export default function ToolTraceList({ traces }: ToolTraceListProps) {
  if (!Array.isArray(traces) || traces.length === 0) {
    return null;
  }

  const renderTraceItem = (trace: ChatToolTrace, index: number, standalone: boolean) => {
    const state = getTraceState(trace);
    const argsContent = formatPayload(trace.args, '(无参数)');
    const resultContentRaw = trace.error
      ? trace.error
      : formatPayload(trace.result, '(无结果)');
    const resultContent = truncatePreview(resultContentRaw);
    const traceKey = `${trace.call_id || trace.name}-${trace.iteration || 0}-${index}`;

    return (
      <details
        key={traceKey}
        className={`${styles.traceItem} ${standalone ? styles.traceStandalone : ''}`.trim()}
      >
        <summary className={styles.traceHeader}>
          <div className={styles.traceTitle}>
            <span className={styles.toolName}>{trace.name}</span>
          </div>
          <span className={`${styles.traceState} ${state.className}`}>
            {state.running && <span className={styles.tracePulse} aria-hidden />}
            {state.label}
          </span>
        </summary>

        <div className={styles.traceBody}>
          <details className={styles.foldPanel}>
            <summary className={styles.foldSummary}>参数</summary>
            <pre className={styles.codeBlock}>{argsContent}</pre>
          </details>

          <details className={styles.foldPanel}>
            <summary className={styles.foldSummary}>{trace.error ? '错误' : '结果'}</summary>
            <pre className={styles.codeBlock}>{resultContent}</pre>
          </details>
        </div>
      </details>
    );
  };

  if (traces.length === 1) {
    return renderTraceItem(traces[0], 0, true);
  }

  return (
    <div className={styles.traceList}>
      {traces.map((trace, index) => renderTraceItem(trace, index, false))}
    </div>
  );
}
