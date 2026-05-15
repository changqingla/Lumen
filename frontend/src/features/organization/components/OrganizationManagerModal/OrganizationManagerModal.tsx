import React, { useCallback, useEffect, useRef, useState } from 'react';
import { X, Copy, Plus, Users, User, LogOut, RefreshCw, Trash2, Building2, UserPlus, Edit2 } from 'lucide-react';
import styles from './OrganizationManagerModal.module.css';
import { organizationAPI, type OrganizationDetailResponse, type OrganizationListItem } from '@/shared/api/client';
import { useToast } from '@/shared/hooks/useToast';
import { copyTextToClipboard } from '@/shared/utils/clipboard';
import {
  CreateOrganizationModal,
  JoinOrganizationModal,
} from '@/features/organization/components';
import ConfirmModal from '@/shared/components/ConfirmModal/ConfirmModal';
import defaultAvatar from '@/assets/default-avatar.svg';
import defaultOrgAvatar from '@/assets/default-team.svg';

interface OrganizationManagerModalProps {
  isOpen: boolean;
  onClose: () => void;
  userLevel: string;
}

const getErrorMessage = (error: unknown, fallback: string): string => {
  if (error instanceof Error && error.message) {
    return error.message;
  }
  return fallback;
};

export default function OrganizationManagerModal({
  isOpen,
  onClose,
  userLevel
}: OrganizationManagerModalProps) {
  const [createdOrgs, setCreatedOrgs] = useState<OrganizationListItem[]>([]);
  const [joinedOrgs, setJoinedOrgs] = useState<OrganizationListItem[]>([]);
  const [selectedOrgId, setSelectedOrgId] = useState<string | null>(null);
  const [orgDetail, setOrgDetail] = useState<OrganizationDetailResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [activeTab, setActiveTab] = useState<'members' | 'settings'>('members');
  const [confirmAction, setConfirmAction] = useState<string | null>(null);
  const [isRegenerating, setIsRegenerating] = useState(false);
  
  // Editing states
  const [isEditingName, setIsEditingName] = useState(false);
  const [isEditingDesc, setIsEditingDesc] = useState(false);
  const [tempName, setTempName] = useState('');
  const [tempDesc, setTempDesc] = useState('');
  const nameInputRef = useRef<HTMLInputElement>(null);
  const descInputRef = useRef<HTMLTextAreaElement>(null);
  
  // Modals for creation/joining
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false);
  const [isJoinModalOpen, setIsJoinModalOpen] = useState(false);
  
  // Confirm modal for member removal
  const [isRemoveConfirmOpen, setIsRemoveConfirmOpen] = useState(false);
  const [memberToRemove, setMemberToRemove] = useState<{id: string, name: string} | null>(null);
  const [isRemoving, setIsRemoving] = useState(false);
  const orgListRequestRef = useRef(0);
  const orgDetailRequestRef = useRef(0);
  
  const toast = useToast();

  useEffect(() => {
    if (orgDetail) {
      setTempName(orgDetail.name);
      setTempDesc(orgDetail.description || '');
    }
  }, [orgDetail]);

  useEffect(() => {
    if (isEditingName && nameInputRef.current) {
      nameInputRef.current.focus();
      nameInputRef.current.select();
    }
  }, [isEditingName]);

  useEffect(() => {
    if (isEditingDesc && descInputRef.current) {
      descInputRef.current.focus();
      descInputRef.current.select();
    }
  }, [isEditingDesc]);

  const loadOrganizations = useCallback(async () => {
    const requestId = orgListRequestRef.current + 1;
    orgListRequestRef.current = requestId;
    try {
      setLoading(true);
      const data = await organizationAPI.list();
      if (requestId !== orgListRequestRef.current) {
        return;
      }
      setCreatedOrgs(data.created);
      setJoinedOrgs(data.joined);

      // Auto-select first org if none selected
      setSelectedOrgId((prevSelectedOrgId) => {
        if (prevSelectedOrgId) {
          return prevSelectedOrgId;
        }
        if (data.created.length > 0) {
          return data.created[0].id;
        }
        if (data.joined.length > 0) {
          return data.joined[0].id;
        }
        return null;
      });
    } catch (error) {
      console.error('Failed to load organizations:', error);
      if (requestId === orgListRequestRef.current) {
        toast.error('加载组织列表失败');
      }
    } finally {
      if (requestId === orgListRequestRef.current) {
        setLoading(false);
      }
    }
  }, [toast]);

  const loadOrgDetail = useCallback(async (id: string) => {
    const requestId = orgDetailRequestRef.current + 1;
    orgDetailRequestRef.current = requestId;
    try {
      setLoadingDetail(true);
      const data = await organizationAPI.get(id);
      if (requestId === orgDetailRequestRef.current) {
        setOrgDetail(data);
      }
    } catch (error) {
      console.error('Failed to load org detail:', error);
      if (requestId === orgDetailRequestRef.current) {
        toast.error('加载组织详情失败');
      }
    } finally {
      if (requestId === orgDetailRequestRef.current) {
        setLoadingDetail(false);
      }
    }
  }, [toast]);

  useEffect(() => {
    if (isOpen) {
      loadOrganizations();
    }
  }, [isOpen, loadOrganizations]);

  useEffect(() => {
    if (selectedOrgId) {
      loadOrgDetail(selectedOrgId);
      setActiveTab('members'); // Reset tab on switch
    }
  }, [selectedOrgId, loadOrgDetail]);

  const handleCopyCode = async () => {
    if (orgDetail?.org_code) {
      try {
        await copyTextToClipboard(orgDetail.org_code);
        toast.success('组织码已复制');
      } catch (error) {
        console.error('复制组织码失败:', error);
        toast.error('复制失败');
      }
    }
  };

  const handleRegenerateCode = async () => {
    if (!orgDetail || isRegenerating) return;
    
    try {
      setIsRegenerating(true);
      const result = await organizationAPI.regenerateCode(orgDetail.id);
      setOrgDetail(prev => prev ? { ...prev, org_code: result.org_code } : null);
      toast.success('组织码已更新！');
    } catch (error) {
      toast.error(getErrorMessage(error, '操作失败'));
    } finally {
      setIsRegenerating(false);
    }
  };

  const handleNameSave = async () => {
    if (!orgDetail || !tempName.trim() || tempName === orgDetail.name) {
      setIsEditingName(false);
      setTempName(orgDetail?.name || '');
      return;
    }

    if (tempName.length < 2) {
      toast.error('组织名称至少需要2个字符');
      return;
    }

    try {
      await organizationAPI.update(orgDetail.id, { name: tempName });
      setOrgDetail({ ...orgDetail, name: tempName });
      toast.success('组织名称已更新');
      loadOrganizations(); // Refresh list
    } catch (error) {
      toast.error(getErrorMessage(error, '更新失败'));
      setTempName(orgDetail.name);
    } finally {
      setIsEditingName(false);
    }
  };

  const handleDescSave = async () => {
    if (!orgDetail || tempDesc === orgDetail.description) {
      setIsEditingDesc(false);
      setTempDesc(orgDetail?.description || '');
      return;
    }

    try {
      await organizationAPI.update(orgDetail.id, { description: tempDesc });
      setOrgDetail({ ...orgDetail, description: tempDesc });
      toast.success('组织描述已更新');
    } catch (error) {
      toast.error(getErrorMessage(error, '更新失败'));
      setTempDesc(orgDetail.description || '');
    } finally {
      setIsEditingDesc(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent, type: 'name' | 'desc') => {
    if (e.key === 'Enter' && type === 'name') {
      handleNameSave();
    } else if (e.key === 'Escape') {
      if (type === 'name') {
        setIsEditingName(false);
        setTempName(orgDetail?.name || '');
      } else {
        setIsEditingDesc(false);
        setTempDesc(orgDetail?.description || '');
      }
    }
  };

  const handleRemoveMemberClick = (memberId: string, memberName: string) => {
    setMemberToRemove({ id: memberId, name: memberName });
    setIsRemoveConfirmOpen(true);
  };

  const handleRemoveMemberConfirm = async () => {
    if (!orgDetail || !memberToRemove) return;

    try {
      setIsRemoving(true);
      await organizationAPI.removeMember(orgDetail.id, memberToRemove.id);
      toast.success('成员已移除');
      setIsRemoveConfirmOpen(false);
      setMemberToRemove(null);
      loadOrgDetail(orgDetail.id); // Refresh detail
    } catch (error) {
      toast.error(getErrorMessage(error, '移除失败'));
    } finally {
      setIsRemoving(false);
    }
  };

  const handleDissolveOrg = async () => {
    if (!orgDetail) return;
    
    if (confirmAction !== 'dissolve') {
      setConfirmAction('dissolve');
      setTimeout(() => setConfirmAction(null), 3000);
      return;
    }
    
    try {
      await organizationAPI.delete(orgDetail.id);
      toast.success('组织已解散');
      setSelectedOrgId(null);
      setOrgDetail(null);
      loadOrganizations(); // Reload list
      setConfirmAction(null);
    } catch (error) {
      toast.error(getErrorMessage(error, '解散失败'));
    }
  };

  const handleLeaveOrg = async () => {
    if (!orgDetail) return;
    
    if (confirmAction !== 'leave') {
      setConfirmAction('leave');
      setTimeout(() => setConfirmAction(null), 3000);
      return;
    }

    try {
      await organizationAPI.leave(orgDetail.id);
      toast.success('已退出组织');
      setSelectedOrgId(null);
      setOrgDetail(null);
      loadOrganizations();
      setConfirmAction(null);
    } catch (error) {
      toast.error(getErrorMessage(error, '退出失败'));
    }
  };

  if (!isOpen) return null;

  return (
    <div className={styles.overlay} onClick={onClose}>
      <div className={styles.modal} onClick={e => e.stopPropagation()}>
        {/* Left Sidebar */}
        <div className={styles.sidebar}>
          <div className={styles.sidebarTitle}>
            <Users size={20} />
            组织管理
          </div>

          <div className={styles.orgList}>
            {createdOrgs.length > 0 && (
              <>
                <div className={styles.sectionTitle}>我创建的</div>
                {createdOrgs.map(org => (
                  <div 
                    key={org.id} 
                    className={`${styles.orgItem} ${selectedOrgId === org.id ? styles.active : ''}`}
                    onClick={() => setSelectedOrgId(org.id)}
                  >
                    <div className={styles.orgAvatarSmall}>
                      <img src={defaultOrgAvatar} alt={org.name} style={{ width: '100%', height: '100%', objectFit: 'cover', borderRadius: '8px' }} />
                    </div>
                    <div className={styles.orgNameSmall}>{org.name}</div>
                  </div>
                ))}
              </>
            )}

            {joinedOrgs.length > 0 && (
              <>
                <div className={styles.sectionTitle}>我加入的</div>
                {joinedOrgs.map(org => (
                  <div 
                    key={org.id} 
                    className={`${styles.orgItem} ${selectedOrgId === org.id ? styles.active : ''}`}
                    onClick={() => setSelectedOrgId(org.id)}
                  >
                    <div className={styles.orgAvatarSmall}>
                      <img src={defaultOrgAvatar} alt={org.name} style={{ width: '100%', height: '100%', objectFit: 'cover', borderRadius: '8px' }} />
                    </div>
                    <div className={styles.orgNameSmall}>{org.name}</div>
                  </div>
                ))}
              </>
            )}
          </div>

          {userLevel !== 'basic' && (
            <button 
              className={styles.addOrgBtn}
              onClick={() => setIsCreateModalOpen(true)}
            >
              <Plus size={16} />
              创建新组织
            </button>
          )}
          <button 
            className={styles.addOrgBtn}
            style={{ marginTop: '8px' }}
            onClick={() => setIsJoinModalOpen(true)}
          >
            <User size={16} />
            加入组织
          </button>
        </div>

        {/* Right Content */}
        <div className={styles.content}>
          <button className={styles.closeBtn} onClick={onClose}>
            <X size={20} />
          </button>

          {!loading && createdOrgs.length === 0 && joinedOrgs.length === 0 ? (
            <div className={styles.emptyContainer}>
              <div className={styles.emptyIconWrapper}>
                <Building2 size={40} />
              </div>
              <h2 className={styles.emptyTitle}>开启团队协作之旅</h2>
              <p className={styles.emptyDesc}>
                {userLevel === 'basic' 
                  ? '加入现有团队，开始高效的信息共享。' 
                  : '创建您的第一个组织，或者加入现有团队，开始高效的信息共享。'}
              </p>
              <div className={styles.emptyActions}>
                {userLevel !== 'basic' && (
                <button 
                  className={styles.primaryBtn}
                  onClick={() => setIsCreateModalOpen(true)}
                >
                  <Plus size={18} />
                  创建组织
                </button>
                )}
                <button 
                  className={styles.secondaryBtn}
                  onClick={() => setIsJoinModalOpen(true)}
                >
                  <UserPlus size={18} />
                  加入组织
                </button>
              </div>
            </div>
          ) : (loadingDetail || !orgDetail) ? (
            <div className={styles.loading}>
              {loading ? '加载中...' : '请选择一个组织'}
            </div>
          ) : (
            <>
              {/* Header */}
              <div className={styles.orgHeader}>
                <div className={styles.headerTop}>
                  <div className={styles.orgAvatarLarge}>
                    <img src={defaultOrgAvatar} alt={orgDetail.name} style={{ width: '100%', height: '100%', objectFit: 'cover', borderRadius: '20px' }} />
                  </div>
                  <div className={styles.orgInfo}>
                    {/* Editable Name */}
                    {(orgDetail.role === 'owner' || createdOrgs.some(o => o.id === orgDetail.id)) ? (
                      isEditingName ? (
                        <div className={styles.nameEditWrapper}>
                          <input
                            ref={nameInputRef}
                            className={styles.nameInputEditing}
                            value={tempName}
                            onChange={(e) => setTempName(e.target.value)}
                            onBlur={handleNameSave}
                            onKeyDown={(e) => handleKeyDown(e, 'name')}
                            maxLength={50}
                          />
                          <div className={styles.nameEditIndicator} />
                        </div>
                      ) : (
                        <div 
                          className={styles.nameDisplay} 
                          onClick={() => setIsEditingName(true)}
                        >
                          <h1 className={styles.orgName}>{orgDetail.name}</h1>
                          <Edit2 size={16} className={styles.editIcon} />
                        </div>
                      )
                    ) : (
                    <h1 className={styles.orgName}>{orgDetail.name}</h1>
                    )}

                    {/* Editable Description */}
                    {(orgDetail.role === 'owner' || createdOrgs.some(o => o.id === orgDetail.id)) ? (
                      isEditingDesc ? (
                        <div className={styles.descEditWrapper}>
                          <textarea
                            ref={descInputRef}
                            className={styles.descInputEditing}
                            value={tempDesc}
                            onChange={(e) => setTempDesc(e.target.value)}
                            onBlur={handleDescSave}
                            onKeyDown={(e) => handleKeyDown(e, 'desc')}
                            maxLength={200}
                            rows={2}
                          />
                        </div>
                      ) : (
                        <div 
                          className={styles.descDisplay} 
                          onClick={() => setIsEditingDesc(true)}
                        >
                          <p className={styles.orgDesc}>
                            {orgDetail.description || '点击添加描述...'}
                          </p>
                          <Edit2 size={14} className={styles.editIconSmall} />
                        </div>
                      )
                    ) : (
                    <p className={styles.orgDesc}>
                      {orgDetail.description || '暂无描述'}
                    </p>
                    )}
                    
                    {(orgDetail.role === 'owner' || createdOrgs.some(o => o.id === orgDetail.id)) && (
                      <div className={styles.codeBox}>
                        <span className={styles.codeLabel}>组织码</span>
                        <span className={styles.codeValue}>{orgDetail.org_code}</span>
                        <button className={styles.copyBtn} onClick={handleCopyCode}>
                          <Copy size={16} />
                        </button>
                      </div>
                    )}
                  </div>
                </div>
              </div>

              {/* Tabs */}
              <div className={styles.tabs}>
                <button 
                  className={`${styles.tab} ${activeTab === 'members' ? styles.active : ''}`}
                  onClick={() => setActiveTab('members')}
                >
                  成员列表 ({orgDetail.member_count})
                </button>
                <button 
                  className={`${styles.tab} ${activeTab === 'settings' ? styles.active : ''}`}
                  onClick={() => setActiveTab('settings')}
                >
                  组织设置
                </button>
              </div>

              {/* Content */}
              <div className={styles.tabContent}>
                {activeTab === 'members' && (
                  <div className={styles.memberList}>
                    <div className={styles.memberHeader}>
                      <div>成员</div>
                      <div>角色</div>
                      <div>加入时间</div>
                    </div>
                    
                    {orgDetail.members && orgDetail.members.length > 0 ? (
                      orgDetail.members.map(member => (
                        <div key={member.id} className={styles.memberRow}>
                          <div className={styles.memberUser}>
                            <img 
                              src={member.user_avatar || defaultAvatar} 
                              alt={member.user_name} 
                              className={styles.memberAvatar} 
                            />
                            <div>
                              <div className={styles.memberName}>{member.user_name}</div>
                              <div className={styles.memberEmail}>{member.user_email}</div>
                            </div>
                          </div>
                          <div>
                            <span className={`${styles.roleBadge} ${member.role === 'owner' ? styles.owner : styles.member}`}>
                              {member.role === 'owner' ? '所有者' : '成员'}
                            </span>
                          </div>
                          <div style={{ display: 'flex', gap: '8px', fontSize: '13px', color: '#64748b', alignItems: 'center' }}>
                            <span>{new Date(member.joined_at).toLocaleDateString()}</span>
                            {((orgDetail.role === 'owner' || createdOrgs.some(o => o.id === orgDetail.id)) && member.role !== 'owner') && (
                              <button
                                className={styles.removeMemberBtn}
                                onClick={(e) => {
                                  e.stopPropagation();
                                  handleRemoveMemberClick(member.user_id, member.user_name);
                                }}
                                title="移除成员"
                              >
                                <Trash2 size={14} />
                              </button>
                            )}
                          </div>
                        </div>
                      ))
                    ) : (
                      <div style={{ padding: '32px', textAlign: 'center', color: '#94a3b8', fontSize: '14px' }}>
                        暂无成员显示
                      </div>
                    )}
                  </div>
                )}

                {activeTab === 'settings' && (
                  <div className={styles.settingsContent}>
                    {(orgDetail.role === 'owner' || createdOrgs.some(o => o.id === orgDetail.id)) ? (
                      <div className={styles.settingSection}>
                        <h3 className={styles.settingTitle}>管理操作</h3>
                        
                        <div className={styles.settingCard}>
                          <div className={styles.settingRow}>
                            <div>
                              <div className={styles.settingName}>重新生成组织码</div>
                              <div className={styles.settingDesc}>旧的组织码将立即失效</div>
                            </div>
                            <button 
                              className={styles.actionBtnOutline}
                              onClick={handleRegenerateCode}
                              disabled={isRegenerating}
                              style={{ opacity: isRegenerating ? 0.6 : 1 }}
                            >
                              <RefreshCw size={14} style={{ animation: isRegenerating ? 'spin 1s linear infinite' : 'none' }} />
                              {isRegenerating ? '生成中...' : '重新生成'}
                            </button>
                          </div>
                        </div>

                        <div className={styles.settingCardDanger}>
                          <div className={styles.settingRow}>
                            <div>
                              <div className={styles.settingNameDanger}>解散组织</div>
                              <div className={styles.settingDesc}>此操作不可恢复，所有成员将被移除</div>
                            </div>
                            <button 
                              className={styles.actionBtnDanger}
                              onClick={handleDissolveOrg}
                            >
                              <Trash2 size={14} />
                              {confirmAction === 'dissolve' ? '确认解散？' : '解散组织'}
                            </button>
                          </div>
                        </div>
                      </div>
                    ) : (
                      <div className={styles.settingSection}>
                        <h3 className={styles.settingTitle}>个人操作</h3>
                        <div className={styles.settingCardDanger}>
                          <div className={styles.settingRow}>
                            <div>
                              <div className={styles.settingNameDanger}>退出组织</div>
                              <div className={styles.settingDesc}>您将失去访问该组织所有资源的权限</div>
                            </div>
                            <button 
                              className={styles.actionBtnDanger}
                              onClick={handleLeaveOrg}
                            >
                              <LogOut size={14} />
                              {confirmAction === 'leave' ? '确认退出？' : '退出组织'}
                            </button>
                          </div>
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

      <CreateOrganizationModal 
        isOpen={isCreateModalOpen}
        onClose={() => setIsCreateModalOpen(false)}
        userLevel={userLevel}
        onSuccess={() => {
          setIsCreateModalOpen(false);
          loadOrganizations();
        }}
      />

      <JoinOrganizationModal
        isOpen={isJoinModalOpen}
        onClose={() => setIsJoinModalOpen(false)}
        onSuccess={() => {
          setIsJoinModalOpen(false);
          loadOrganizations();
        }}
      />

      <ConfirmModal
        isOpen={isRemoveConfirmOpen}
        type="danger"
        title="移除成员"
        message={`确定要将成员 ${memberToRemove?.name} 移除出组织吗？该操作无法撤销。`}
        confirmText="确认移除"
        cancelText="取消"
        onConfirm={handleRemoveMemberConfirm}
        onCancel={() => {
          setIsRemoveConfirmOpen(false);
          setMemberToRemove(null);
        }}
        loading={isRemoving}
      />
    </div>
  );
}
