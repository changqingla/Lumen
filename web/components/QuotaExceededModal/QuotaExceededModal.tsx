import { X, AlertTriangle, ArrowUpCircle, Mail } from 'lucide-react';
import styles from './QuotaExceededModal.module.css';

interface QuotaExceededModalProps {
  isOpen: boolean;
  onClose: () => void;
  onUpgrade?: () => void;
  userLevel: string;
  usedTokens: number;
  quotaLimit: number;
  resetDate: string;
}

/**
 * 格式化 Token 数量为可读格式
 */
function formatTokens(tokens: number): string {
  if (tokens >= 1_000_000) {
    return `${(tokens / 1_000_000).toFixed(1)}M`;
  }
  if (tokens >= 1_000) {
    return `${(tokens / 1_000).toFixed(1)}K`;
  }
  return tokens.toLocaleString();
}

/**
 * 格式化日期为本地化格式
 */
function formatDate(isoDate: string): string {
  const date = new Date(isoDate);
  return date.toLocaleDateString('zh-CN', {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

/**
 * 获取用户等级显示名称
 */
function getUserLevelName(level: string): string {
  switch (level) {
    case 'basic':
      return '普通用户';
    case 'member':
      return '白银会员';
    case 'premium':
      return '白金会员';
    default:
      return level;
  }
}

export default function QuotaExceededModal({
  isOpen,
  onClose,
  onUpgrade,
  userLevel,
  usedTokens,
  quotaLimit,
  resetDate,
}: QuotaExceededModalProps) {
  if (!isOpen) return null;

  const isPremium = userLevel === 'premium';
  const message = isPremium
    ? '模型用量已达上限，请联系管理员'
    : '模型用量已达上限，请升级会员';

  const usagePercent = Math.min(100, (usedTokens / quotaLimit) * 100);

  const handleUpgrade = () => {
    if (onUpgrade) {
      onUpgrade();
    }
    onClose();
  };

  return (
    <div className={styles.overlay} onClick={onClose}>
      <div className={styles.modal} onClick={(e) => e.stopPropagation()}>
        {/* Header */}
        <div className={styles.header}>
          <div className={styles.titleRow}>
            <AlertTriangle className={styles.warningIcon} size={24} />
            <h2 className={styles.title}>配额已用尽</h2>
          </div>
          <button className={styles.closeBtn} onClick={onClose}>
            <X size={18} />
          </button>
        </div>

        {/* Content */}
        <div className={styles.content}>
          <p className={styles.message}>{message}</p>

          {/* Quota Stats */}
          <div className={styles.statsContainer}>
            <div className={styles.statItem}>
              <span className={styles.statLabel}>当前等级</span>
              <span className={styles.statValue}>{getUserLevelName(userLevel)}</span>
            </div>
            <div className={styles.statItem}>
              <span className={styles.statLabel}>已使用</span>
              <span className={styles.statValue}>{formatTokens(usedTokens)}</span>
            </div>
            <div className={styles.statItem}>
              <span className={styles.statLabel}>配额上限</span>
              <span className={styles.statValue}>{formatTokens(quotaLimit)}</span>
            </div>
            <div className={styles.statItem}>
              <span className={styles.statLabel}>重置时间</span>
              <span className={styles.statValue}>{formatDate(resetDate)}</span>
            </div>
          </div>

          {/* Progress Bar */}
          <div className={styles.progressContainer}>
            <div className={styles.progressBar}>
              <div
                className={styles.progressFill}
                style={{ width: `${usagePercent}%` }}
              />
            </div>
            <span className={styles.progressText}>{usagePercent.toFixed(0)}%</span>
          </div>

          {/* Action Buttons */}
          <div className={styles.actions}>
            {!isPremium ? (
              <button className={styles.upgradeBtn} onClick={handleUpgrade}>
                <ArrowUpCircle size={18} />
                升级会员
              </button>
            ) : (
              <div className={styles.contactInfo}>
                <Mail size={18} />
                <span>请联系管理员获取更多配额</span>
              </div>
            )}
            <button className={styles.closeAction} onClick={onClose}>
              我知道了
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
