import React, { useCallback, useEffect, useRef, useState } from 'react';
import { User, Building2, Crown, X, Edit2 } from 'lucide-react';
import styles from './ProfileModal.module.css';
import AvatarUpload from '@/shared/components/AvatarUpload/AvatarUpload';
import { authAPI, organizationAPI } from '@/shared/api/client';
import { useToast } from '@/shared/hooks/useToast';
import defaultOrgAvatar from '@/assets/default-team.svg';

interface UserProfile {
  id: string;
  name: string;
  email: string;
  avatar: string | null;
  user_level: string;
  is_admin: boolean;
  membership_expires_at: string | null;
}

interface OrganizationSummary {
  id: string;
  name: string;
  avatar: string | null;
  is_owner: boolean;
}

interface ProfileModalProps {
  isOpen: boolean;
  onClose: () => void;
  initialTab?: 'profile' | 'organization';
}

type TabType = 'profile' | 'organization';
type CurrentUserResponse = Awaited<ReturnType<typeof authAPI.getMe>>;
type ActivationResponse = Awaited<ReturnType<typeof authAPI.activate>>;
type OrganizationListResponse = Awaited<ReturnType<typeof organizationAPI.list>>;

function getErrorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

function normalizeOrganizations(orgsData: OrganizationListResponse): OrganizationSummary[] {
  return [...(orgsData.created || []), ...(orgsData.joined || [])].map((org) => ({
    id: String(org.id),
    name: String(org.name),
    avatar: typeof org.avatar === 'string' ? org.avatar : null,
    is_owner: Boolean(org.is_owner),
  }));
}

function toUserProfile(userData: CurrentUserResponse): UserProfile {
  return {
    ...userData,
    membership_expires_at: userData.membership_expires_at ?? null,
  };
}

function toActivatedUserProfile(result: ActivationResponse): UserProfile {
  return {
    ...result,
    membership_expires_at: result.member_expires_at,
  };
}

export default function ProfileModal({ isOpen, onClose, initialTab = 'profile' }: ProfileModalProps) {
  const [activeTab, setActiveTab] = useState<TabType>(initialTab);

  useEffect(() => {
    if (isOpen) {
      setActiveTab(initialTab);
    }
  }, [isOpen, initialTab]);
  const [user, setUser] = useState<UserProfile | null>(null);
  const [loading, setLoading] = useState(true);
  
  // Name Editing State
  const [isEditingName, setIsEditingName] = useState(false);
  const [tempName, setTempName] = useState('');
  const nameInputRef = useRef<HTMLInputElement>(null);

  // Membership State
  const [activationCode, setActivationCode] = useState('');
  const [activating, setActivating] = useState(false);
  
  // Organization State
  const [organizations, setOrganizations] = useState<OrganizationSummary[]>([]);
  
  const toast = useToast();

  useEffect(() => {
    if (user) {
      setTempName(user.name);
    }
  }, [user]);

  useEffect(() => {
    if (isEditingName && nameInputRef.current) {
      nameInputRef.current.focus();
    }
  }, [isEditingName]);

  const loadFromCache = useCallback(() => {
    try {
      const cached = localStorage.getItem('userProfile');
      if (cached) {
        const parsed = JSON.parse(cached);
        setUser(parsed);
        setLoading(false); // 缓存加载完成，立即显示
      }
    } catch (error) {
      console.error('Failed to load from cache:', error);
    }
  }, []);

  const loadProfile = useCallback(async () => {
    try {
      if (!localStorage.getItem('userProfile')) {
        setLoading(true);
      }
      
      const [userData, orgsData] = await Promise.all([
        authAPI.getMe(),
        organizationAPI.list().catch(() => ({ created: [], joined: [] })),
      ]);
      
      const adaptedData = toUserProfile(userData);
      setUser(adaptedData);
      
      // 同步更新 localStorage，确保 Sidebar 显示最新信息
      localStorage.setItem('userProfile', JSON.stringify(adaptedData));
      
      // 触发 storage 事件，通知 Sidebar 更新
      window.dispatchEvent(new StorageEvent('storage', {
        key: 'userProfile',
        newValue: JSON.stringify(adaptedData),
      }));
      
      setOrganizations(normalizeOrganizations(orgsData));
    } catch (error) {
      console.error('Failed to load profile:', error);
      toast.error('加载个人信息失败');
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    if (isOpen) {
      loadFromCache();
      void loadProfile();
      setActiveTab('profile');
    }
  }, [isOpen, loadFromCache, loadProfile]);

  const handleNameSave = async () => {
    if (!tempName.trim() || tempName === user?.name) {
      setIsEditingName(false);
      setTempName(user?.name || '');
      return;
    }

    if (tempName.length < 2) {
      toast.error('昵称太短了');
      return;
    }

    if (tempName.length > 8) {
      toast.error('昵称不能超过8个字符');
      return;
    }

    try {
      const updated = await authAPI.updateProfile({ name: tempName });
      setUser(prev => prev ? ({ ...prev, name: updated.name }) : null);
      toast.success('昵称已更新');
      
      const saved = localStorage.getItem('userProfile');
      if (saved) {
        const parsed = JSON.parse(saved);
        const newProfile = { ...parsed, name: updated.name };
        localStorage.setItem('userProfile', JSON.stringify(newProfile));
        
        // 触发 storage 事件，通知 Sidebar 更新
        window.dispatchEvent(new StorageEvent('storage', {
          key: 'userProfile',
          newValue: JSON.stringify(newProfile),
        }));
      }
    } catch (error: unknown) {
      toast.error(getErrorMessage(error, '更新失败'));
      setTempName(user?.name || '');
    } finally {
      setIsEditingName(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      handleNameSave();
    } else if (e.key === 'Escape') {
      setIsEditingName(false);
      setTempName(user?.name || '');
    }
  };

  const handleAvatarUpload = async (file: File) => {
    if (!user) return;
    try {
      const { url } = await authAPI.uploadAvatar(file);
      setUser({ ...user, avatar: url });
      
      // 同步更新 localStorage，让 Sidebar 实时显示新头像
      const saved = localStorage.getItem('userProfile');
      if (saved) {
        const parsed = JSON.parse(saved);
        localStorage.setItem('userProfile', JSON.stringify({ ...parsed, avatar: url }));
      }
      
      // 触发 storage 事件，通知 Sidebar 更新
      window.dispatchEvent(new StorageEvent('storage', {
        key: 'userProfile',
        newValue: JSON.stringify({ ...JSON.parse(saved || '{}'), avatar: url }),
      }));
      
      toast.success('头像已更新');
    } catch (error: unknown) {
      toast.error(getErrorMessage(error, '头像上传失败'));
      throw error;
    }
  };

  const handleActivate = async () => {
    if (!activationCode.trim()) return;
    
    try {
      setActivating(true);
      const result = await authAPI.activate(activationCode);
      
      // 激活成功，更新用户信息
      const adaptedData = toActivatedUserProfile(result);
      setUser(adaptedData);
      
      // 同步更新 localStorage
      localStorage.setItem('userProfile', JSON.stringify(adaptedData));
      window.dispatchEvent(new StorageEvent('storage', {
        key: 'userProfile',
        newValue: JSON.stringify(adaptedData),
      }));
      
      // 显示成功提示并清空输入框
      toast.success('🎉 激活成功！您已成为' + (result.user_level === 'premium' ? '白金会员' : '白银会员'));
      setActivationCode('');
      
      // 刷新组织列表（会员权限可能变化）
      const orgsData = await organizationAPI.list().catch(() => ({ created: [], joined: [] }));
      setOrganizations(normalizeOrganizations(orgsData));
    } catch (error: unknown) {
      toast.error(getErrorMessage(error, '激活失败'));
    } finally {
      setActivating(false);
    }
  };

  if (!isOpen) return null;

  return (
    <div className={styles.overlay} onClick={onClose}>
      <div className={styles.modal} onClick={(e) => e.stopPropagation()}>
        <button className={styles.closeBtn} onClick={onClose}>
          <X size={18} />
        </button>

        {loading || !user ? (
          <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <span style={{ color: '#94a3b8' }}>加载中...</span>
          </div>
        ) : (
          <>
            {/* Top Banner (Flowing Aurora) */}
            <div className={styles.banner} />

            {/* Profile Header (Floating over banner) */}
            <div className={styles.profileHeader}>
              <div className={styles.avatarWrapper}>
                <AvatarUpload 
                  currentAvatar={user.avatar} 
                  onUpload={handleAvatarUpload}
                  size={110}
                  showTips={false}
                />
              </div>
              
              <div className={styles.nameContainer}>
                {isEditingName ? (
                  <div className={styles.nameEditWrapper}>
                    <input
                      ref={nameInputRef}
                      className={styles.nameInputEditing}
                      value={tempName}
                      onChange={(e) => setTempName(e.target.value)}
                      onBlur={handleNameSave}
                      onKeyDown={handleKeyDown}
                      maxLength={8}
                      autoFocus
                    />
                    <div className={styles.nameEditIndicator} />
                  </div>
                ) : (
                  <div 
                    className={styles.nameDisplay} 
                    onClick={() => setIsEditingName(true)}
                  >
                    <span className={styles.nameText}>{user.name}</span>
                    <Edit2 size={14} className={styles.editIcon} />
                  </div>
                )}
              </div>
            </div>

            {/* Navigation Pills */}
            <div className={styles.navContainer}>
              <div className={styles.nav}>
                <button 
                  className={`${styles.navItem} ${activeTab === 'profile' ? styles.active : ''}`}
                  onClick={() => setActiveTab('profile')}
                >
                  <User size={14} />
                  我的权益
                </button>
                <button 
                  className={`${styles.navItem} ${activeTab === 'organization' ? styles.active : ''}`}
                  onClick={() => setActiveTab('organization')}
                >
                  <Building2 size={14} />
                  我的组织
                </button>
              </div>
            </div>

            {/* Content Area */}
            <div className={styles.content}>
              {activeTab === 'profile' && (
                <div className={`${styles.membershipCard} ${
                  !user.is_admin && user.user_level === 'basic' 
                    ? styles.cardBasic 
                    : styles.cardPremium
                }`}>
                  <div className={styles.cardTop}>
                    <div>
                      <div className={styles.cardLabel}>当前身份</div>
                      <div className={styles.cardValue}>
                        {user.is_admin ? '管理员' : 
                         user.user_level === 'premium' ? '白金会员' :
                         user.user_level === 'member' ? '白银会员' : '普通用户'}
                      </div>
                      {(user.user_level === 'member' || user.user_level === 'premium') && (
                        <div className={styles.expiryDate}>
                          {user.membership_expires_at 
                            ? `有效期至 ${new Date(user.membership_expires_at).toLocaleDateString()}`
                            : '永久有效'}
                        </div>
                      )}
                    </div>
                    <div className={styles.cardIcon}>
                      <Crown size={20} className={styles.crownIcon} />
                    </div>
                  </div>

                  {!user.is_admin && user.user_level === 'basic' ? (
                    <div className={styles.cardBottom}>
                      <div className={styles.activationLabel}>
                        <Crown size={12} className={styles.activationIcon} />
                        会员激活
                      </div>
                      <div className={styles.redeemInputWrapper}>
                        <input 
                          className={styles.redeemInput}
                          placeholder="输入激活码解锁权益..."
                          value={activationCode}
                          onChange={(e) => setActivationCode(e.target.value)}
                        />
                        <button 
                          className={styles.redeemBtn}
                          onClick={handleActivate}
                          disabled={activating || !activationCode.trim()}
                        >
                          {activating ? 'Checking...' : '立即兑换'}
                        </button>
                      </div>
                    </div>
                  ) : (
                    <div className={styles.cardBottom}>
                      <div className={styles.cardLabel} style={{ marginBottom: '12px', fontSize: '12px' }}>会员权益</div>
                      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px' }}>
                        {(user.is_admin ? [
                          '创建组织无限制', '加入组织无限制', '全局知识库共享', '用户与系统管理'
                        ] : user.user_level === 'premium' ? [
                          '创建 2 个组织', '加入 10 个组织', '组织成员上限 500 人', '更高级的模型调用'
                        ] : [
                          '创建 1 个组织', '加入 3 个组织', '组织成员上限 100 人', '更高级的模型调用'
                        ]).map((benefit, index) => (
                          <div key={index} style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '11px', opacity: 0.9 }}>
                            <div style={{ width: '4px', height: '4px', borderRadius: '50%', background: 'white' }} />
                            {benefit}
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )}

              {activeTab === 'organization' && (
                <div className={styles.orgDashboard}>
                  {organizations.length > 0 ? (
                    <div className={styles.orgListPreview}>
                      <div className={styles.sectionTitle} style={{ padding: '0 4px', marginBottom: '12px' }}>
                        已加入的组织 ({organizations.length})
                      </div>
                      {organizations.map(org => (
                        <div key={org.id} className={styles.orgPreviewItem}>
                          <div className={styles.orgAvatarSmall}>
                            <img src={org.avatar || defaultOrgAvatar} alt="" />
                          </div>
                          <div className={styles.orgInfo}>
                            <div className={styles.orgName}>{org.name}</div>
                            <div className={styles.orgRole}>
                              {org.is_owner ? '创建者' : '成员'}
                            </div>
                          </div>
                        </div>
                      ))}
                      <div className={styles.viewAllLink}>
                        请在侧边栏「组织管理」中进行操作
                      </div>
                    </div>
                  ) : (
                    <div className={styles.emptyState}>
                      <div className={styles.emptyIcon}>
                        <Building2 size={32} />
                      </div>
                      <div className={styles.emptyText}>
                        {user?.user_level === 'basic' 
                          ? "快去组织模块加入一个新的组织，开始丰富的信息共享之旅吧" 
                          : "快去组织模块创建或者加入一个新的组织，开始团队协作之旅吧"}
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
