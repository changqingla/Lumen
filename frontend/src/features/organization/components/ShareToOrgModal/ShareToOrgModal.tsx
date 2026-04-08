import React, { useCallback, useEffect, useState } from 'react';
import { X, Building2 } from 'lucide-react';
import styles from './ShareToOrgModal.module.css';
import { organizationAPI, type OrganizationListItem } from '@/shared/api/client';
import { useToast } from '@/shared/hooks/useToast';
import defaultOrgAvatar from '@/assets/default-team.svg';

interface ShareToOrgModalProps {
  isOpen: boolean;
  onClose: () => void;
  onConfirm: (orgIds: string[]) => Promise<void>;
  currentlySharedOrgs?: string[]; // 当前已共享的组织ID
}

const getErrorMessage = (error: unknown, fallback: string): string => {
  if (error instanceof Error && error.message) {
    return error.message;
  }
  return fallback;
};

export default function ShareToOrgModal({ 
  isOpen, 
  onClose, 
  onConfirm,
  currentlySharedOrgs = []
}: ShareToOrgModalProps) {
  const [orgs, setOrgs] = useState<OrganizationListItem[]>([]);
  const [selectedOrgs, setSelectedOrgs] = useState<Set<string>>(new Set());
  const [unshareOrgs, setUnshareOrgs] = useState<Set<string>>(new Set()); // 要取消共享的组织
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const toast = useToast();

  const loadOrganizations = useCallback(async () => {
    try {
      setLoading(true);
      const result = await organizationAPI.list();
      const allOrgs = [...(result.created || []), ...(result.joined || [])];
      setOrgs(allOrgs);
    } catch (error) {
      console.error('Failed to load organizations:', error);
      toast.error('加载组织列表失败');
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    if (isOpen) {
      loadOrganizations();
      setSelectedOrgs(new Set());
      setUnshareOrgs(new Set());
    }
  }, [isOpen, loadOrganizations]);

  const handleToggleOrg = (orgId: string) => {
    const isCurrentlyShared = currentlySharedOrgs.includes(orgId);
    
    if (isCurrentlyShared) {
      // 如果已经共享，点击则添加到"取消共享"列表
      const newUnshare = new Set(unshareOrgs);
      if (newUnshare.has(orgId)) {
        newUnshare.delete(orgId); // 取消"取消共享"
      } else {
        newUnshare.add(orgId); // 标记为要取消共享
      }
      setUnshareOrgs(newUnshare);
    } else {
      // 如果未共享，正常添加/移除到选择列表
    const newSelected = new Set(selectedOrgs);
    if (newSelected.has(orgId)) {
      newSelected.delete(orgId);
    } else {
      newSelected.add(orgId);
    }
    setSelectedOrgs(newSelected);
    }
  };

  const handleSubmit = async () => {
    if (selectedOrgs.size === 0 && unshareOrgs.size === 0) {
      toast.error('请至少选择一个操作');
      return;
    }

    try {
      setSubmitting(true);
      
      // Build final org list: current + new - removed
      const finalOrgIds = new Set([
        ...currentlySharedOrgs.filter(id => !unshareOrgs.has(id)), // Keep current except unshared
        ...Array.from(selectedOrgs) // Add newly selected
      ]);
      
      await onConfirm(Array.from(finalOrgIds));
      
      if (selectedOrgs.size > 0 && unshareOrgs.size > 0) {
        toast.success('共享设置已更新！');
      } else if (selectedOrgs.size > 0) {
      toast.success('共享成功！');
      } else {
        toast.success('已取消共享！');
      }
      
      onClose();
    } catch (error) {
      toast.error(getErrorMessage(error, '操作失败'));
    } finally {
      setSubmitting(false);
    }
  };

  if (!isOpen) return null;

  return (
    <div className={styles.overlay} onClick={onClose}>
      <div className={styles.modal} onClick={(e) => e.stopPropagation()}>
        <div className={styles.header}>
          <h2 className={styles.title}>共享到组织</h2>
          <button className={styles.closeBtn} onClick={onClose}>
            <X size={20} />
          </button>
        </div>

        <div className={styles.body}>
          {loading ? (
            <div className={styles.empty}>
              <div className={styles.emptyText}>加载中...</div>
            </div>
          ) : orgs.length === 0 ? (
            <div className={styles.empty}>
              <div className={styles.emptyIcon}>
                <Building2 size={24} />
              </div>
              <div className={styles.emptyText}>还没有加入任何组织</div>
              <div className={styles.emptyHint}>创建或加入组织后即可共享知识库</div>
            </div>
          ) : (
            <div className={styles.orgList}>
              {orgs.map((org) => {
                const isShared = currentlySharedOrgs.includes(org.id);
                const isSelected = selectedOrgs.has(org.id);
                const isUnsharing = unshareOrgs.has(org.id);
                
                return (
                  <div
                    key={org.id}
                    className={`${styles.orgItem} ${isSelected ? styles.selected : ''} ${isShared && !isUnsharing ? styles.shared : ''} ${isUnsharing ? styles.unsharing : ''}`}
                    onClick={() => handleToggleOrg(org.id)}
                  >
                    <div className={styles.orgAvatar}>
                      <img 
                        src={org.avatar || defaultOrgAvatar} 
                        alt={org.name} 
                      />
                    </div>
                    <div className={styles.orgInfo}>
                      <div className={styles.orgName}>{org.name}</div>
                      <div className={styles.orgMeta}>
                        {org.member_count} 人 · {org.is_owner ? '创建者' : '成员'}
                      </div>
                    </div>
                    
                    {/* 状态徽章放在右侧 */}
                    <div className={styles.statusBadges}>
                      {isShared && !isUnsharing && (
                      <span className={styles.sharedBadge}>已共享</span>
                    )}
                      {isUnsharing && (
                        <span className={styles.unshareBadge}>将取消</span>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        <div className={styles.footer}>
          <div className={styles.selectedCount}>
            {selectedOrgs.size > 0 && `新增 ${selectedOrgs.size} 个`}
            {selectedOrgs.size > 0 && unshareOrgs.size > 0 && ' · '}
            {unshareOrgs.size > 0 && `取消 ${unshareOrgs.size} 个`}
            {selectedOrgs.size === 0 && unshareOrgs.size === 0 && '请选择组织'}
          </div>
          <div className={styles.actions}>
            <button
              className={`${styles.btn} ${styles.btnSecondary}`}
              onClick={onClose}
              disabled={submitting}
            >
              取消
            </button>
            <button
              className={`${styles.btn} ${styles.btnPrimary}`}
              onClick={handleSubmit}
              disabled={submitting || (selectedOrgs.size === 0 && unshareOrgs.size === 0)}
            >
              {submitting ? '处理中...' : '确认'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
