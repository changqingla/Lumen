/**
 * 统计数据组件
 * 展示系统的各项统计数据
 */
import { useState, useEffect, useCallback } from 'react';
import { Users, Building2, Database, RefreshCw, AlertCircle } from 'lucide-react';
import { useToast } from '@/shared/hooks/useToast';
import { adminAPI } from '@/shared/api/client';
import { getErrorMessage } from '@/shared/utils/errorMessage';
import styles from './Statistics.module.css';

interface StatisticsData {
  users: {
    total: number;
    explorers: number;
    members: number;
    advancedMembers: number;
    admins: number;
  };
  organizations: {
    total: number;
    averageMembers: number;
  };
  knowledgeBases: {
    total: number;
    public: number;
    shared: number;
  };
}

export default function Statistics() {
  const toast = useToast();
  const [stats, setStats] = useState<StatisticsData | null>(null);
  const [loading, setLoading] = useState(false);
  const [errorMessage, setErrorMessage] = useState('');

  const loadStatistics = useCallback(async () => {
    setLoading(true);
    setErrorMessage('');
    try {
      const data = await adminAPI.getStatistics();
      setStats({
        users: {
          total: data.users.total,
          explorers: data.users.explorers,
          members: data.users.members,
          advancedMembers: data.users.advanced_members,
          admins: data.users.admins,
        },
        organizations: {
          total: data.organizations.total,
          averageMembers: data.organizations.average_members,
        },
        knowledgeBases: {
          total: data.knowledge_bases.total,
          public: data.knowledge_bases.public,
          shared: data.knowledge_bases.shared,
        },
      });
    } catch (error: unknown) {
      console.error('Failed to load statistics:', error);
      const message = getErrorMessage(error, '加载统计数据失败');
      setErrorMessage(message);
      toast.error(message);
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    void loadStatistics();
  }, [loadStatistics]);

  if (loading && !stats) {
    return (
      <div className={styles.loading}>加载中...</div>
    );
  }

  if (!stats) {
    return (
      <div className={styles.stateCard}>
        <AlertCircle size={20} />
        <div>
          <div className={styles.stateTitle}>统计数据暂时不可用</div>
          <div className={styles.stateDesc}>{errorMessage || '请稍后重试'}</div>
        </div>
        <button className={styles.refreshBtn} onClick={loadStatistics} disabled={loading}>
          <RefreshCw size={16} className={loading ? styles.spinning : ''} />
          重新加载
        </button>
      </div>
    );
  }

  const averageMembersLabel = Number.isInteger(stats.organizations.averageMembers)
    ? stats.organizations.averageMembers.toString()
    : stats.organizations.averageMembers.toFixed(1);

  return (
    <div className={styles.container}>
      <div className={styles.actions}>
        <button className={styles.refreshBtn} onClick={loadStatistics} disabled={loading}>
          <RefreshCw size={16} className={loading ? styles.spinning : ''} />
          刷新统计
        </button>
      </div>

      <div className={styles.cards}>
        {/* 用户统计 */}
        <div className={styles.card}>
          <div className={styles.cardHeader}>
            <div className={styles.cardIcon} style={{ background: 'linear-gradient(135deg, #6366f1, #8b5cf6)' }}>
              <Users size={24} />
            </div>
            <h3 className={styles.cardTitle}>用户统计</h3>
          </div>
          
          <div className={styles.mainStat}>
            <div className={styles.mainNumber}>{stats.users.total}</div>
            <div className={styles.mainLabel}>总用户数</div>
          </div>
          
          <div className={styles.breakdown}>
            <div className={styles.breakdownItem}>
              <span className={styles.breakdownLabel}>普通用户</span>
              <span className={styles.breakdownValue}>{stats.users.explorers}</span>
            </div>
            <div className={styles.breakdownItem}>
              <span className={styles.breakdownLabel}>白银会员</span>
              <span className={styles.breakdownValue}>{stats.users.members}</span>
            </div>
            <div className={styles.breakdownItem}>
              <span className={styles.breakdownLabel}>白金会员</span>
              <span className={styles.breakdownValue}>{stats.users.advancedMembers}</span>
            </div>
            <div className={styles.breakdownItem}>
              <span className={styles.breakdownLabel}>管理员</span>
              <span className={styles.breakdownValue}>{stats.users.admins}</span>
            </div>
          </div>
        </div>

        {/* 组织统计 */}
        <div className={styles.card}>
          <div className={styles.cardHeader}>
            <div className={styles.cardIcon} style={{ background: 'linear-gradient(135deg, #10b981, #059669)' }}>
              <Building2 size={24} />
            </div>
            <h3 className={styles.cardTitle}>组织统计</h3>
          </div>
          
          <div className={styles.mainStat}>
            <div className={styles.mainNumber}>{stats.organizations.total}</div>
            <div className={styles.mainLabel}>总组织数</div>
          </div>
          
          <div className={styles.breakdown}>
            <div className={styles.breakdownItem}>
              <span className={styles.breakdownLabel}>平均成员数</span>
              <span className={styles.breakdownValue}>{averageMembersLabel}</span>
            </div>
          </div>
        </div>

        {/* 知识库统计 */}
        <div className={styles.card}>
          <div className={styles.cardHeader}>
            <div className={styles.cardIcon} style={{ background: 'linear-gradient(135deg, #f59e0b, #d97706)' }}>
              <Database size={24} />
            </div>
            <h3 className={styles.cardTitle}>知识库统计</h3>
          </div>
          
          <div className={styles.mainStat}>
            <div className={styles.mainNumber}>{stats.knowledgeBases.total}</div>
            <div className={styles.mainLabel}>总知识库数</div>
          </div>
          
          <div className={styles.breakdown}>
            <div className={styles.breakdownItem}>
              <span className={styles.breakdownLabel}>公开分享</span>
              <span className={styles.breakdownValue}>{stats.knowledgeBases.public}</span>
            </div>
            <div className={styles.breakdownItem}>
              <span className={styles.breakdownLabel}>组织共享</span>
              <span className={styles.breakdownValue}>{stats.knowledgeBases.shared}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
