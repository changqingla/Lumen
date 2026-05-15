/**
 * 管理员后台页面
 * 提供激活码管理、用户管理、统计数据等功能
 */
import { useState } from 'react';
import { Shield, Key, Users, BarChart3 } from 'lucide-react';
import {
  ActivationCodeManagement,
  Statistics,
  UserManagement,
} from '@/features/admin/components';
import styles from './AdminPanelPage.module.css';

type Tab = 'codes' | 'users' | 'statistics';

export default function AdminPanelPage() {
  const [activeTab, setActiveTab] = useState<Tab>('codes');

  const tabs = [
    { id: 'codes' as Tab, label: '激活码管理', icon: Key },
    { id: 'users' as Tab, label: '用户管理', icon: Users },
    { id: 'statistics' as Tab, label: '统计数据', icon: BarChart3 },
  ];

  return (
    <div className={styles.page}>
      <div className={styles.main}>
        <div className={styles.header}>
          <div className={styles.headerContent}>
            <div className={styles.headerTitle}>
              <Shield size={32} />
              <h1>管理员后台</h1>
            </div>
            <p className={styles.headerDesc}>系统管理与数据统计</p>
          </div>
        </div>

        <div className={styles.tabsContainer}>
          <div className={styles.tabs}>
            {tabs.map((tab) => {
              const Icon = tab.icon;
              return (
                <button
                  key={tab.id}
                  className={`${styles.tab} ${activeTab === tab.id ? styles.active : ''}`}
                  onClick={() => setActiveTab(tab.id)}
                >
                  <Icon size={18} />
                  <span>{tab.label}</span>
                </button>
              );
            })}
          </div>
        </div>

        <div className={styles.content}>
          {activeTab === 'codes' && <ActivationCodeManagement />}
          {activeTab === 'users' && <UserManagement />}
          {activeTab === 'statistics' && <Statistics />}
        </div>
      </div>
    </div>
  );
}
