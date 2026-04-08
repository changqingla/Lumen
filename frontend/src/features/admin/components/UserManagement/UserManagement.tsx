/**
 * 用户管理组件
 * 提供用户列表查看和管理员权限设置
 */
import React, { useState, useEffect } from 'react';
import { Search, Shield, ShieldOff, Crown, Award, User, RefreshCw } from 'lucide-react';
import { useToast } from '@/shared/hooks/useToast';
import { adminAPI } from '@/shared/api/client';
import styles from './UserManagement.module.css';

interface User {
  id: string;
  name: string;
  email: string;
  user_level: string;
  is_admin: boolean;
  created_at: string;
  last_active_at: string | null;
  weekly_token_total: number;
}

export default function UserManagement() {
  const toast = useToast();
  const [users, setUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');

  useEffect(() => {
    loadUsers();
  }, []);

  const loadUsers = async () => {
    setLoading(true);
    try {
      const data = await adminAPI.listUsers(1, 100);
      setUsers(data.items);
    } catch (error: any) {
      console.error('Failed to load users:', error);
      toast.error(error.message || '加载用户列表失败');
    } finally {
      setLoading(false);
    }
  };

  const handleSetAdmin = async (userId: string, isAdmin: boolean) => {
    const action = isAdmin ? '设置' : '取消';
    if (!confirm(`确定要${action}该用户的管理员权限吗？`)) return;
    
    try {
      if (isAdmin) {
        await adminAPI.setUserAdmin(userId);
      } else {
        await adminAPI.removeUserAdmin(userId);
      }
      toast.success(`${action}管理员成功`);
      loadUsers();
    } catch (error: any) {
      console.error('Failed to set admin:', error);
      toast.error(error.message || `${action}管理员失败`);
    }
  };

  const getUserRole = (user: User) => {
    if (user.is_admin) return { text: '管理员', icon: Shield, color: '#dc2626' };
    if (user.user_level === 'premium') return { text: '白金会员', icon: Crown, color: '#9333ea' };
    if (user.user_level === 'member') return { text: '白银会员', icon: Award, color: '#2563eb' };
    return { text: '普通用户', icon: User, color: '#64748b' };
  };

  const getActivityState = (lastActiveAt: string | null) => {
    if (!lastActiveAt) {
      return {
        label: '未活跃',
        tone: styles.activityIdle,
        detail: '还没有发送过消息',
      };
    }

    const lastActiveDate = new Date(lastActiveAt);
    const now = new Date();
    const diffMs = now.getTime() - lastActiveDate.getTime();
    const diffMinutes = Math.max(0, Math.floor(diffMs / (1000 * 60)));
    const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
    const startOfTargetDay = new Date(
      lastActiveDate.getFullYear(),
      lastActiveDate.getMonth(),
      lastActiveDate.getDate()
    ).getTime();
    const dayDiff = Math.round((startOfToday - startOfTargetDay) / (1000 * 60 * 60 * 24));

    const exactTime = lastActiveDate.toLocaleString('zh-CN', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    });

    if (diffMinutes <= 10) {
      return {
        label: '刚刚活跃',
        tone: styles.activityHot,
        detail: exactTime,
      };
    }

    if (dayDiff === 0) {
      return {
        label: '今日活跃',
        tone: styles.activityToday,
        detail: lastActiveDate.toLocaleTimeString('zh-CN', {
          hour: '2-digit',
          minute: '2-digit',
        }),
      };
    }

    if (dayDiff <= 7) {
      return {
        label: '近 7 天活跃',
        tone: styles.activityRecent,
        detail: exactTime,
      };
    }

    return {
      label: '长期未活跃',
      tone: styles.activityIdle,
      detail: exactTime,
    };
  };

  const formatTokenTotal = (total: number) => `${total.toLocaleString('zh-CN')} tokens`;

  const filteredUsers = users.filter((user) =>
    user.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
    user.email.toLowerCase().includes(searchQuery.toLowerCase())
  );

  return (
    <div className={styles.container}>
      <div className={styles.toolbar}>
        <div className={styles.searchWrap}>
          <Search size={18} />
          <input
            type="text"
            placeholder="搜索用户..."
            className={styles.searchInput}
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
          />
        </div>

        <button className={styles.refreshBtn} onClick={loadUsers} disabled={loading}>
          <RefreshCw size={16} className={loading ? styles.spinning : ''} />
          刷新
        </button>
      </div>

      {loading ? (
        <div className={styles.loading}>加载中...</div>
      ) : (
        <div className={styles.table}>
          <div className={styles.tableHeader}>
            <div className={styles.col1}>用户</div>
            <div className={styles.col2}>身份等级</div>
            <div className={styles.col3}>最近活跃</div>
            <div className={styles.col4}>近 7 日 Token</div>
            <div className={styles.col5}>注册时间</div>
            <div className={styles.col6}>操作</div>
          </div>
          
          {filteredUsers.map((user) => {
            const role = getUserRole(user);
            const RoleIcon = role.icon;
            const activity = getActivityState(user.last_active_at);
            
            return (
              <div key={user.id} className={styles.tableRow}>
                <div className={styles.col1}>
                  <div className={styles.userInfo}>
                    <div className={styles.avatar}>
                      {user.name[0].toUpperCase()}
                    </div>
                    <div>
                      <div className={styles.userName}>{user.name}</div>
                      <div className={styles.userEmail}>{user.email}</div>
                    </div>
                  </div>
                </div>
                
                <div className={styles.col2}>
                  <span className={styles.roleBadge} style={{ color: role.color }}>
                    <RoleIcon size={14} />
                    {role.text}
                  </span>
                </div>
                
                <div className={styles.col3}>
                  <div className={styles.activityCell}>
                    <span className={`${styles.activityBadge} ${activity.tone}`}>
                      {activity.label}
                    </span>
                    <span className={styles.activityDetail}>{activity.detail}</span>
                  </div>
                </div>

                <div className={styles.col4}>
                  <span className={styles.tokenValue}>{formatTokenTotal(user.weekly_token_total)}</span>
                </div>

                <div className={styles.col5}>
                  {new Date(user.created_at).toLocaleDateString('zh-CN')}
                </div>
                
                <div className={styles.col6}>
                  <button
                    className={user.is_admin ? styles.removeAdminBtn : styles.setAdminBtn}
                    onClick={() => handleSetAdmin(user.id, !user.is_admin)}
                  >
                    {user.is_admin ? (
                      <>
                        <ShieldOff size={14} />
                        取消管理员
                      </>
                    ) : (
                      <>
                        <Shield size={14} />
                        设为管理员
                      </>
                    )}
                  </button>
                </div>
              </div>
            );
          })}
          
          {filteredUsers.length === 0 && (
            <div className={styles.empty}>未找到用户</div>
          )}
        </div>
      )}
    </div>
  );
}
