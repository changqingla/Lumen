import React, { useState } from 'react';
import { ArrowUp, Square } from 'lucide-react';
import styles from './SendStopButton.module.css';

export interface SendStopButtonProps {
  isStreaming: boolean;
  disabled: boolean;
  onSend: () => void;
  onStop: () => void;
  hasContent: boolean;
}

/**
 * Unified Send/Stop button component that switches between send and stop states
 * based on streaming status.
 *
 * - Send state: neutral solid button + ArrowUp icon
 * - Stop state: muted emphasis button + Square icon
 */
export default function SendStopButton({
  isStreaming,
  disabled,
  onSend,
  onStop,
  hasContent,
}: SendStopButtonProps) {
  const [isClicked, setIsClicked] = useState(false);

  const handleClick = () => {
    // Trigger click animation
    setIsClicked(true);
    setTimeout(() => setIsClicked(false), 150);

    if (isStreaming) {
      onStop();
    } else {
      onSend();
    }
  };

  // Button is disabled when:
  // - In send mode: no content or explicitly disabled
  // - In stop mode: never disabled (user should always be able to stop)
  const isButtonDisabled = !isStreaming && (!hasContent || disabled);

  return (
    <button
      className={`${styles.button} ${isStreaming ? styles.stopState : styles.sendState} ${isClicked ? styles.clicked : ''}`}
      onClick={handleClick}
      disabled={isButtonDisabled}
      aria-label={isStreaming ? '停止生成' : '发送消息'}
      title={isStreaming ? '停止生成' : '发送消息'}
    >
      {isStreaming ? (
        <Square size={16} strokeWidth={3} />
      ) : (
        <ArrowUp size={18} strokeWidth={3} />
      )}
    </button>
  );
}
