import React, { useState, useEffect } from 'react';
import { X, AlertCircle } from 'lucide-react';
import styles from './JoinOrganizationModal.module.css';
import { organizationAPI } from '@/shared/api/client';
import { useToast } from '@/shared/hooks/useToast';

interface JoinOrganizationModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSuccess?: () => void;
}

const getErrorMessage = (error: unknown, fallback: string): string => {
  if (error instanceof Error && error.message) {
    return error.message;
  }
  return fallback;
};

export default function JoinOrganizationModal({ 
  isOpen, 
  onClose, 
  onSuccess 
}: JoinOrganizationModalProps) {
  const [orgCode, setOrgCode] = useState('');
  const [joining, setJoining] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const toast = useToast();

  useEffect(() => {
    if (!isOpen) {
      setOrgCode('');
      setError(null);
    }
  }, [isOpen]);

  const handleCodeChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const code = e.target.value.toUpperCase().replace(/[^A-Z0-9-]/g, '');
    setOrgCode(code);
    setError(null);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    
    if (!orgCode.trim()) {
      setError('请输入组织码');
      return;
    }

    try {
      setJoining(true);
      setError(null);
      await organizationAPI.join(orgCode);
      toast.success('加入成功！');
      
      if (onSuccess) {
        onSuccess();
      }
      
      onClose();
    } catch (error) {
      setError(getErrorMessage(error, '加入失败'));
    } finally {
      setJoining(false);
    }
  };

  if (!isOpen) return null;

  return (
    <div className={styles.overlay} onClick={onClose}>
      <div className={styles.modal} onClick={(e) => e.stopPropagation()}>
        <div className={styles.header}>
          <h2 className={styles.title}>加入组织</h2>
          <button className={styles.closeBtn} onClick={onClose}>
            <X size={20} />
          </button>
        </div>

        <form onSubmit={handleSubmit}>
          <div className={styles.body}>
            <div className={styles.formGroup}>
              <label className={styles.label}>组织码</label>
              <input
                className={styles.input}
                value={orgCode}
                onChange={handleCodeChange}
                placeholder="ORG-XXXXXXXX"
                maxLength={20}
                autoFocus
              />
              <div className={styles.helperText}>
                请输入组织管理员提供的组织码
              </div>
            </div>

            {error && (
              <div className={styles.warningBox}>
                <AlertCircle size={16} />
                <span>{error}</span>
              </div>
            )}
          </div>

          <div className={styles.footer}>
            <button
              type="button"
              className={`${styles.btn} ${styles.btnSecondary}`}
              onClick={onClose}
              disabled={joining}
            >
              取消
            </button>
            <button
              type="submit"
              className={`${styles.btn} ${styles.btnPrimary}`}
              disabled={joining || !orgCode.trim()}
            >
              {joining ? (
                <>
                  <span className={styles.loadingSpinner}></span>
                  <span style={{ marginLeft: 8 }}>加入中...</span>
                </>
              ) : (
                '确认加入'
              )}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
