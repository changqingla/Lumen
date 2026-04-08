import { useEffect, useMemo, useRef, useState } from 'react';
import { Check, ChevronDown } from 'lucide-react';

import { CHAT_UI_MODE_LABELS, type ChatUIMode } from '@/shared/contracts/chat-ui-mode';

import styles from './ChatUIModeSwitch.module.css';

interface ChatUIModeSwitchProps {
  value: ChatUIMode;
  onChange: (mode: ChatUIMode) => void;
  disabled?: boolean;
  className?: string;
}

const options: ChatUIMode[] = ['normal', 'plan'];

export default function ChatUIModeSwitch({
  value,
  onChange,
  disabled = false,
  className,
}: ChatUIModeSwitchProps) {
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const [isOpen, setIsOpen] = useState(false);
  const [panelStyle, setPanelStyle] = useState<{
    left: number;
    bottom: number;
    width: number;
  } | null>(null);

  const triggerLabel = useMemo(
    () => CHAT_UI_MODE_LABELS[value],
    [value],
  );

  useEffect(() => {
    if (!isOpen) {
      return;
    }

    const handlePointerDown = (event: MouseEvent) => {
      const target = event.target as Node | null;
      if (!target) {
        return;
      }
      const clickedTrigger = triggerRef.current?.contains(target) ?? false;
      const clickedPanel = panelRef.current?.contains(target) ?? false;
      if (!clickedTrigger && !clickedPanel) {
        setIsOpen(false);
      }
    };

    document.addEventListener('mousedown', handlePointerDown);
    return () => {
      document.removeEventListener('mousedown', handlePointerDown);
    };
  }, [isOpen]);

  useEffect(() => {
    if (!isOpen) {
      return;
    }

    const updatePanelPosition = () => {
      const trigger = triggerRef.current;
      if (!trigger) {
        return;
      }

      const rect = trigger.getBoundingClientRect();
      const viewportWidth = window.innerWidth;
      const viewportHeight = window.innerHeight;
      const horizontalPadding = 16;
      const gap = 8;
      const panelWidth = 160;
      const left = Math.min(
        Math.max(horizontalPadding, rect.left),
        viewportWidth - panelWidth - horizontalPadding,
      );

      setPanelStyle({
        left,
        bottom: viewportHeight - rect.top + gap,
        width: panelWidth,
      });
    };

    updatePanelPosition();
    window.addEventListener('resize', updatePanelPosition);
    window.addEventListener('scroll', updatePanelPosition, true);
    return () => {
      window.removeEventListener('resize', updatePanelPosition);
      window.removeEventListener('scroll', updatePanelPosition, true);
    };
  }, [isOpen]);

  return (
    <div className={`${styles.root} ${className || ''}`.trim()}>
      <button
        type="button"
        className={styles.trigger}
        ref={triggerRef}
        onClick={() => setIsOpen((prev) => !prev)}
        disabled={disabled}
        title="选择推理模式"
      >
        <span className={styles.triggerLabel}>{triggerLabel}</span>
        <ChevronDown size={14} className={styles.triggerIcon} />
      </button>

      {isOpen ? (
        <div
          className={styles.panel}
          ref={panelRef}
          onMouseDown={(event) => {
            event.stopPropagation();
          }}
          style={
            panelStyle
              ? {
                  left: `${panelStyle.left}px`,
                  bottom: `${panelStyle.bottom}px`,
                  width: `${panelStyle.width}px`,
                }
              : undefined
          }
        >
          <div className={styles.panelHeader}>
            <h3 className={styles.panelTitle}>选择推理模式</h3>
          </div>

          <div className={styles.options}>
            {options.map((option) => {
              const isSelected = option === value;
              return (
                <button
                  key={option}
                  type="button"
                  className={`${styles.option} ${isSelected ? styles.optionSelected : ''}`.trim()}
                  onMouseDown={(event) => {
                    event.stopPropagation();
                  }}
                  onClick={() => {
                    onChange(option);
                    setIsOpen(false);
                  }}
                >
                  <span className={styles.optionTitle}>{CHAT_UI_MODE_LABELS[option]}</span>
                  {isSelected ? <Check size={16} className={styles.check} /> : null}
                </button>
              );
            })}
          </div>
        </div>
      ) : null}
    </div>
  );
}
