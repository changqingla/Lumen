import React from 'react';
import { AlertTriangle, Info, CheckCircle, XCircle } from 'lucide-react';
import styles from './ConfirmModal.module.css';

export type ConfirmModalType = 'warning' | 'danger' | 'info' | 'success';

interface ConfirmModalProps {
  isOpen: boolean;
  title: string;
  message: string;
  type?: ConfirmModalType;
  confirmText?: string;
  cancelText?: string;
  onConfirm: () => void | Promise<void>;
  onCancel: () => void;
  loading?: boolean;
}

export default function ConfirmModal({
  isOpen,
  title,
  message,
  type = 'warning',
  confirmText = '确认',
  cancelText = '取消',
  onConfirm,
  onCancel,
  loading = false,
}: ConfirmModalProps) {
  if (!isOpen) return null;

  const handleConfirm = async () => {
    await onConfirm();
  };

  const getIcon = () => {
    switch (type) {
      case 'danger':
        return <XCircle size={48} className={styles.modalIcon} />;
      case 'success':
        return <CheckCircle size={48} className={styles.modalIcon} />;
      case 'info':
        return <Info size={48} className={styles.modalIcon} />;
      default:
        return <AlertTriangle size={48} className={styles.modalIcon} />;
    }
  };

  return (
    <div className={styles.overlay} onClick={onCancel}>
      <div className={styles.modal} onClick={(e) => e.stopPropagation()}>
        <div className={`${styles.iconContainer} ${styles[type]}`}>
          {getIcon()}
          <div className={styles.iconBlob} />
        </div>

        <h2 className={styles.title}>{title}</h2>
        <p className={styles.message}>{message}</p>

        <div className={styles.actions}>
          <button
            className={styles.btnCancel}
            onClick={onCancel}
            disabled={loading}
          >
            {cancelText}
          </button>
          <button
            className={`${styles.btnConfirm} ${styles[`btn-${type}`]}`}
            onClick={handleConfirm}
            disabled={loading}
          >
            {loading ? (
              <>
                <span className={styles.spinner} />
                <span>处理中...</span>
              </>
            ) : (
              confirmText
            )}
          </button>
        </div>
      </div>
    </div>
  );
}
