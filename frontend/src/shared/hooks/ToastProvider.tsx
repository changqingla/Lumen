import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  CircleAlert,
  CircleCheck,
  Info,
  TriangleAlert,
  X,
} from 'lucide-react';
import { ToastContext, type Toast, type ToastType } from './toastContext';

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const timersRef = useRef(new Map<string, number>());

  useEffect(() => () => {
    timersRef.current.forEach((timer) => window.clearTimeout(timer));
    timersRef.current.clear();
  }, []);

  const addToast = useCallback((type: ToastType, message: string) => {
    const id = Math.random().toString(36).substr(2, 9);
    setToasts((prev) => [...prev, { id, type, message }]);

    const timer = window.setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
      timersRef.current.delete(id);
    }, 3000);
    timersRef.current.set(id, timer);
  }, []);

  const removeToast = useCallback((id: string) => {
    const timer = timersRef.current.get(id);
    if (timer !== undefined) {
      window.clearTimeout(timer);
      timersRef.current.delete(id);
    }
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const success = useCallback((message: string) => addToast('success', message), [addToast]);
  const error = useCallback((message: string) => addToast('error', message), [addToast]);
  const warning = useCallback((message: string) => addToast('warning', message), [addToast]);
  const info = useCallback((message: string) => addToast('info', message), [addToast]);
  const toastActions = useMemo(
    () => ({ addToast, removeToast, success, error, warning, info }),
    [addToast, removeToast, success, error, warning, info],
  );

  return (
    <ToastContext.Provider value={toastActions}>
      {children}
      <ToastContainer toasts={toasts} onClose={removeToast} />
    </ToastContext.Provider>
  );
}

function ToastContainer({ toasts, onClose }: { toasts: Toast[]; onClose: (id: string) => void }) {
  if (toasts.length === 0) return null;

  return (
    <div style={{
      position: 'fixed',
      top: '20px',
      right: '20px',
      zIndex: 9999,
      display: 'flex',
      flexDirection: 'column',
      gap: '10px',
      width: 'min(400px, calc(100vw - 40px))',
    }} aria-live="polite" aria-relevant="additions">
      {toasts.map((toast) => (
        <ToastItem key={toast.id} toast={toast} onClose={onClose} />
      ))}
    </div>
  );
}

function ToastItem({ toast, onClose }: { toast: Toast; onClose: (id: string) => void }) {
  const variants = {
    success: { bg: '#10b981', Icon: CircleCheck, label: '成功' },
    error: { bg: '#ef4444', Icon: CircleAlert, label: '错误' },
    warning: { bg: '#f59e0b', Icon: TriangleAlert, label: '警告' },
    info: { bg: '#3b82f6', Icon: Info, label: '提示' },
  };

  const { bg, Icon, label } = variants[toast.type];

  return (
    <div role={toast.type === 'error' ? 'alert' : 'status'} style={{
      background: bg,
      color: 'white',
      padding: '12px 16px',
      borderRadius: '8px',
      boxShadow: '0 4px 12px rgba(0, 0, 0, 0.15)',
      display: 'flex',
      alignItems: 'center',
      gap: '10px',
      width: '100%',
      minWidth: 0,
      overflowWrap: 'anywhere',
      animation: 'slideIn 0.3s ease-out',
    }}>
      <Icon size={18} aria-hidden="true" />
      <span style={{ flex: 1, fontSize: '14px' }}>{toast.message}</span>
      <button
        type="button"
        onClick={() => onClose(toast.id)}
        aria-label={`关闭${label}通知`}
        title="关闭通知"
        style={{
          background: 'rgba(255, 255, 255, 0.2)',
          border: 'none',
          color: 'white',
          width: '32px',
          height: '32px',
          borderRadius: '50%',
          cursor: 'pointer',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          flexShrink: 0,
        }}
      >
        <X size={14} aria-hidden="true" />
      </button>
    </div>
  );
}
