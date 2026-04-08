import React from 'react';
import { User, Award, Crown, Shield } from 'lucide-react';
import styles from './UserBadge.module.css';

type UserLevel = 'basic' | 'member' | 'premium' | 'admin';
type BadgeSize = 'small' | 'medium' | 'large';

interface UserBadgeProps {
  level: UserLevel;
  size?: BadgeSize;
  showIcon?: boolean;
}

const levelConfig = {
  basic: {
    label: '普通用户',
    icon: User,
    className: styles.basic,
  },
  member: {
    label: '白银会员',
    icon: Award,
    className: styles.member,
  },
  premium: {
    label: '白金会员',
    icon: Crown,
    className: styles.premium,
  },
  admin: {
    label: '管理员',
    icon: Shield,
    className: styles.admin,
  },
};

export default function UserBadge({ 
  level, 
  size = 'medium', 
  showIcon = true 
}: UserBadgeProps) {
  const config = levelConfig[level];
  const Icon = config.icon;

  return (
    <span className={`${styles.badge} ${config.className} ${styles[size]}`}>
      {showIcon && <Icon size={size === 'small' ? 10 : size === 'large' ? 14 : 12} className={styles.icon} />}
      {config.label}
    </span>
  );
}

