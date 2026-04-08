import { useEffect, useMemo, useRef, useState } from 'react';
import { Check, ChevronDown } from 'lucide-react';

import ModelProviderIcon from '@/shared/components/ModelProviderIcon';
import type { ChatModelOption } from '@/shared/api/client';
import { filterModelsByVisionRequirement, resolvePreferredModelName } from '@/features/chat/hooks/useChatModels';

import styles from './ChatModelSelector.module.css';

interface ChatModelSelectorProps {
  models: ChatModelOption[];
  value?: string;
  onChange: (modelName: string) => void;
  disabled?: boolean;
  requireVision?: boolean;
  defaultModelName?: string;
}

export default function ChatModelSelector({
  models,
  value,
  onChange,
  disabled = false,
  requireVision = false,
  defaultModelName,
}: ChatModelSelectorProps) {
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const [isOpen, setIsOpen] = useState(false);
  const [panelStyle, setPanelStyle] = useState<{
    left: number;
    bottom: number;
    width: number;
    maxHeight: number;
  } | null>(null);

  const availableModels = useMemo(() => {
    return filterModelsByVisionRequirement(models, requireVision);
  }, [models, requireVision]);

  const selectedModelName = useMemo(
    () => resolvePreferredModelName(models, value, { defaultModelName, requireVision }),
    [defaultModelName, models, requireVision, value],
  );

  const selectedModel = useMemo(
    () => availableModels.find((item) => item.name === selectedModelName) || availableModels[0],
    [availableModels, selectedModelName],
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
      const panelWidth = Math.min(220, viewportWidth - horizontalPadding * 2);
      const left = Math.min(
        Math.max(horizontalPadding, rect.left),
        viewportWidth - panelWidth - horizontalPadding,
      );
      const availableAbove = Math.min(280, Math.max(160, rect.top - horizontalPadding));
      setPanelStyle({
        left,
        bottom: viewportHeight - rect.top + gap,
        width: panelWidth,
        maxHeight: availableAbove,
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

  const getModelLabel = (item?: ChatModelOption) => {
    const displayName = item?.display_name?.trim();
    if (displayName) {
      return displayName;
    }
    return item?.name || '';
  };

  const triggerLabel = getModelLabel(selectedModel) || (requireVision ? '选择视觉模型' : '选择模型');
  const emptyLabel = requireVision
    ? '当前没有可用的视觉模型'
    : '当前没有可用的模型';

  return (
    <div className={styles.root}>
      <button
        type="button"
        className={styles.trigger}
        ref={triggerRef}
        onClick={() => setIsOpen((prev) => !prev)}
        disabled={disabled || availableModels.length === 0}
        title={requireVision ? '当前已附加图片，只显示支持视觉的模型' : '选择模型'}
      >
        {selectedModel ? (
          <ModelProviderIcon
            iconKey={selectedModel.provider_icon_key}
            alt={selectedModel.provider_display_name || triggerLabel}
            size={16}
          />
        ) : null}
        <span className={styles.triggerLabel}>{triggerLabel}</span>
        {requireVision && <span className={styles.triggerHint}>视觉</span>}
        <ChevronDown size={14} className={styles.triggerIcon} />
      </button>

      {isOpen && (
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
                maxHeight: `${panelStyle.maxHeight}px`,
              }
              : undefined
          }
        >
          <div className={styles.panelHeader}>
            <h3 className={styles.panelTitle}>选择模型</h3>
          </div>

          <div className={styles.options}>
            {availableModels.length === 0 ? (
              <div className={styles.empty}>{emptyLabel}</div>
            ) : (
              availableModels.map((item) => {
                const isSelected = item.name === selectedModel?.name;
                return (
                  <button
                    key={item.name}
                    type="button"
                    className={`${styles.option} ${isSelected ? styles.optionSelected : ''}`}
                    onMouseDown={(event) => {
                      event.stopPropagation();
                    }}
                    onClick={() => {
                      onChange(item.name);
                      setIsOpen(false);
                    }}
                  >
                    <ModelProviderIcon
                      iconKey={item.provider_icon_key}
                      alt={item.provider_display_name || getModelLabel(item)}
                      size={18}
                    />
                    <div className={styles.optionBody}>
                      <span className={styles.optionTitle}>{getModelLabel(item)}</span>
                    </div>
                    {isSelected && <Check size={16} className={styles.check} />}
                  </button>
                );
              })
            )}
          </div>
        </div>
      )}
    </div>
  );
}
