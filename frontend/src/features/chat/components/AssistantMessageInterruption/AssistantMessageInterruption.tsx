import type { ChatInterruption } from '@/shared/api/client';
import styles from './AssistantMessageInterruption.module.css';

interface AssistantMessageInterruptionProps {
  interruption?: ChatInterruption | null;
}

const trimTrailingPunctuation = (value: string): string => (
  value.replace(/[。.!！？\s]+$/u, '').trim()
);

export default function AssistantMessageInterruption({
  interruption,
}: AssistantMessageInterruptionProps) {
  const reason = trimTrailingPunctuation(interruption?.reason || '');
  if (!reason) {
    return null;
  }

  return (
    <p className={styles.notice} role="status" aria-live="polite">
      任务未完成，已提前终止：{reason}。请重试。
    </p>
  );
}
