import React from 'react';
import { AlertTriangle, ArrowUpCircle, Mail, X } from 'lucide-react';
import styles from './QuotaExceededMessage.module.css';

interface QuotaExceededMessageProps {
  userLevel: string;
  usedTokens: number;
  quotaLimit: number;
  resetDate: string;
  onClose: () => void;
  onUpgrade?: () => void;
}

// 格式化 token 数量
const formatTokens = (tokens: number): string => {
  if (tokens >= 1000000) {
    return `${(tokens / 1000000).toFixed(1)}M`;
  } else if (tokens >= 1000) {
    return `${(tokens / 1000).toFixed(1)}K`;
  }
  return tokens.toString();
};

// 格式化日期
const formatDate = (dateStr: string): string => {
  if (!dateStr) return '未知';
  try {
    const date = new Date(dateStr);
    return date.toLocaleDateString('zh-CN', {
      year: 'numeric',
      month: 'long',
      day: 'numeric',
    });
  } catch {
    return dateStr;
  }
};

export default function QuotaExceededMessage({
  userLevel,
  usedTokens,
  quotaLimit,
  resetDate,
  onClose,
  onUpgrade,
}: QuotaExceededMessageProps) {
  const isPremium = userLevel === 'premium';
  const message = isPremium
    ? '模型用量已达上限，请联系管理员'
    : '模型用量已达上限，请升级会员';

  return (
    <div className={styles.container}>
      <div className={styles.header}>
        <div className={styles.iconWrapper}>
          <AlertTriangle size={20} />
        </div>
        <span className={styles.title}>配额已用尽</span>
        <button className={styles.closeButton} onClick={onClose}>
          <X size={16} />
        </button>
      </div>
      
      <div className={styles.content}>
        <p className={styles.message}>{message}</p>
        
        <div className={styles.stats}>
          <div className={styles.statItem}>
            <span className={styles.statLabel}>已使用</span>
            <span className={styles.statValue}>{formatTokens(usedTokens)}</span>
          </div>
          <div className={styles.statDivider}>/</div>
          <div className={styles.statItem}>
            <span className={styles.statLabel}>配额上限</span>
            <span className={styles.statValue}>{formatTokens(quotaLimit)}</span>
          </div>
        </div>
        
        <p className={styles.resetInfo}>
          配额将于 <strong>{formatDate(resetDate)}</strong> 重置
        </p>
      </div>
      
      <div className={styles.actions}>
        {isPremium ? (
          <a href="mailto:support@example.com" className={styles.actionButton}>
            <Mail size={16} />
            <span>联系管理员</span>
          </a>
        ) : (
          <button className={styles.actionButton} onClick={onUpgrade}>
            <ArrowUpCircle size={16} />
            <span>升级会员</span>
          </button>
        )}
      </div>
    </div>
  );
}
