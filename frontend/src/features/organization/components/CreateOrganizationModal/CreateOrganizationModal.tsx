import React, { useState } from 'react';
import { X, Info, CheckCircle, Copy } from 'lucide-react';
import styles from './CreateOrganizationModal.module.css';
import { organizationAPI } from '@/shared/api/client';
import { useToast } from '@/shared/hooks/useToast';

interface CreateOrganizationModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSuccess?: () => void;
  userLevel: string;
}

export default function CreateOrganizationModal({ 
  isOpen, 
  onClose, 
  onSuccess,
  userLevel 
}: CreateOrganizationModalProps) {
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [creating, setCreating] = useState(false);
  const [success, setSuccess] = useState(false);
  const [orgCode, setOrgCode] = useState('');
  const toast = useToast();

  const maxMembers = userLevel === 'premium' || userLevel === 'admin' ? 500 : 100;

  const handleClose = () => {
    setName('');
    setDescription('');
    setSuccess(false);
    setOrgCode('');
    onClose();
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    
    if (!name.trim()) {
      toast.error('请输入组织名称');
      return;
    }
    
    if (name.trim().length < 3) {
      toast.error('组织名称至少需要 3 个字符');
      return;
    }

    try {
      setCreating(true);
      const result = await organizationAPI.create({
        name: name.trim(),
        description: description.trim() || undefined,
      });
      
      setOrgCode(result.org_code);
      setSuccess(true);
      
      if (onSuccess) {
        onSuccess();
      }
    } catch (error: unknown) {
      toast.error(error instanceof Error ? error.message : '创建失败');
    } finally {
      setCreating(false);
    }
  };

  const handleCopyCode = async () => {
    try {
      await navigator.clipboard.writeText(orgCode);
      toast.success('组织码已复制');
    } catch {
      toast.error('复制失败');
    }
  };

  if (!isOpen) return null;

  return (
    <div className={styles.overlay} onClick={handleClose}>
      <div className={styles.modal} onClick={(e) => e.stopPropagation()}>
        {!success ? (
          <>
            <div className={styles.header}>
              <h2 className={styles.title}>创建组织</h2>
              <button className={styles.closeBtn} onClick={handleClose}>
                <X size={20} />
              </button>
            </div>

            <form onSubmit={handleSubmit}>
              <div className={styles.body}>
                <div className={styles.formGroup}>
                  <label className={styles.label}>
                    组织名称
                    <span className={styles.required}>*</span>
                  </label>
                  <input
                    className={styles.input}
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder="例如：AI研究小组"
                    maxLength={50}
                    autoFocus
                  />
                  <div className={styles.helperText}>3-50 个字符</div>
                </div>

                <div className={styles.formGroup}>
                  <label className={styles.label}>组织描述</label>
                  <textarea
                    className={`${styles.input} ${styles.textarea}`}
                    value={description}
                    onChange={(e) => setDescription(e.target.value)}
                    placeholder="简单介绍一下这个组织..."
                    maxLength={200}
                  />
                  <div className={styles.helperText}>
                    {description.length}/200 字符
                  </div>
                </div>

                <div className={styles.infoBox}>
                  <Info size={16} />
                  <div>
                    <div style={{ fontWeight: 500, marginBottom: 4 }}>创建后您将获得：</div>
                    <div>• 组织码：用于邀请成员加入</div>
                    <div>• 成员上限：{maxMembers} 人</div>
                    <div>• 管理权限：可移除成员、修改组织信息</div>
                  </div>
                </div>
              </div>

              <div className={styles.footer}>
                <button
                  type="button"
                  className={`${styles.btn} ${styles.btnSecondary}`}
                  onClick={handleClose}
                  disabled={creating}
                >
                  取消
                </button>
                <button
                  type="submit"
                  className={`${styles.btn} ${styles.btnPrimary}`}
                  disabled={creating || !name.trim()}
                >
                  {creating ? '创建中...' : '创建组织'}
                </button>
              </div>
            </form>
          </>
        ) : (
          <>
            <div className={styles.header}>
              <h2 className={styles.title}>创建成功</h2>
              <button className={styles.closeBtn} onClick={handleClose}>
                <X size={20} />
              </button>
            </div>

            <div className={styles.successCard}>
              <div className={styles.successIcon}>
                <CheckCircle size={30} />
              </div>
              <h3 className={styles.successTitle}>组织创建成功！</h3>
              <p className={styles.successText}>
                分享组织码给其他人，他们即可加入您的组织
              </p>

              <div className={styles.orgCodeBox}>
                <div className={styles.orgCodeLabel}>组织码</div>
                <div className={styles.orgCodeValue}>{orgCode}</div>
                <button className={styles.copyBtn} onClick={handleCopyCode}>
                  <Copy size={14} />
                  复制组织码
                </button>
              </div>

              <button
                className={`${styles.btn} ${styles.btnPrimary}`}
                onClick={handleClose}
                style={{ width: '100%' }}
              >
                完成
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
