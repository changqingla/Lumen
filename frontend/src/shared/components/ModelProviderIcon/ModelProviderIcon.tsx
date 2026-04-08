import type { CSSProperties } from 'react';

import { Bot, Link2 } from 'lucide-react';

import anthropicIcon from '@/assets/model-providers/anthropic.svg';
import baiduIcon from '@/assets/model-providers/baidu.svg';
import ctyunIcon from '@/assets/model-providers/ctyun.svg';
import deepseekIcon from '@/assets/model-providers/deepseek.svg';
import geminiIcon from '@/assets/model-providers/gemini.svg';
import infiniaiIcon from '@/assets/model-providers/infiniai.svg';
import kimiIcon from '@/assets/model-providers/kimi.svg';
import lingyiIcon from '@/assets/model-providers/lingyi.svg';
import minimaxIcon from '@/assets/model-providers/minimax.png';
import modelscopeIcon from '@/assets/model-providers/modelscope.svg';
import novitaIcon from '@/assets/model-providers/novita.svg';
import openaiIcon from '@/assets/model-providers/openai.svg';
import openrouterIcon from '@/assets/model-providers/openrouter.svg';
import poeIcon from '@/assets/model-providers/poe.svg';
import ppioIcon from '@/assets/model-providers/ppio.svg';
import qwenIcon from '@/assets/model-providers/qwen.svg';
import siliconflowIcon from '@/assets/model-providers/siliconflow.png';
import stepfunIcon from '@/assets/model-providers/stepfun.svg';
import tencentIcon from '@/assets/model-providers/tencent.svg';
import volcengineIcon from '@/assets/model-providers/volcengine.svg';
import xaiIcon from '@/assets/model-providers/xai.svg';
import zhipuIcon from '@/assets/model-providers/zhipu.svg';

import styles from './ModelProviderIcon.module.css';

const PROVIDER_ICON_MAP: Record<string, string> = {
  anthropic: anthropicIcon,
  baidu: baiduIcon,
  ctyun: ctyunIcon,
  deepseek: deepseekIcon,
  gemini: geminiIcon,
  infiniai: infiniaiIcon,
  kimi: kimiIcon,
  lingyi: lingyiIcon,
  minimax: minimaxIcon,
  modelscope: modelscopeIcon,
  novita: novitaIcon,
  openai: openaiIcon,
  openrouter: openrouterIcon,
  poe: poeIcon,
  ppio: ppioIcon,
  qwen: qwenIcon,
  siliconflow: siliconflowIcon,
  stepfun: stepfunIcon,
  tencent: tencentIcon,
  volcengine: volcengineIcon,
  xai: xaiIcon,
  zhipu: zhipuIcon,
};

interface ModelProviderIconProps {
  iconKey?: string | null;
  alt?: string;
  size?: number;
  className?: string;
}

export default function ModelProviderIcon({
  iconKey,
  alt = '模型供应商',
  size = 18,
  className,
}: ModelProviderIconProps) {
  const normalizedKey = String(iconKey || '').trim().toLowerCase();
  const iconSrc = normalizedKey ? PROVIDER_ICON_MAP[normalizedKey] : undefined;
  const isCustom = normalizedKey === 'custom';

  return (
    <span
      className={[styles.icon, className].filter(Boolean).join(' ')}
      style={{ '--provider-icon-size': `${size}px` } as CSSProperties}
      aria-hidden={!iconSrc}
    >
      {iconSrc ? (
        <img src={iconSrc} alt={alt} className={styles.image} />
      ) : isCustom ? (
        <Link2 size={Math.max(14, size - 2)} className={styles.fallback} aria-hidden="true" />
      ) : (
        <Bot size={Math.max(14, size - 2)} className={styles.fallback} aria-hidden="true" />
      )}
    </span>
  );
}
