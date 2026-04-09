import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import {
  type GuestLoginPromptDetail,
  subscribeGuestLoginPrompt,
} from '@/shared/lib/guest-mode';

const DEFAULT_PROMPT: Required<GuestLoginPromptDetail> = {
  title: '登录后继续',
  message: '当前是游客试用模式，这个功能需要登录后才能继续使用。',
  confirmText: '去登录',
};

export default function GuestLoginPrompt() {
  const navigate = useNavigate();
  const [isOpen, setIsOpen] = useState(false);
  const [prompt, setPrompt] = useState<Required<GuestLoginPromptDetail>>(DEFAULT_PROMPT);

  useEffect(() => subscribeGuestLoginPrompt((detail) => {
    setPrompt({
      title: detail.title || DEFAULT_PROMPT.title,
      message: detail.message || DEFAULT_PROMPT.message,
      confirmText: detail.confirmText || DEFAULT_PROMPT.confirmText,
    });
    setIsOpen(true);
  }), []);

  if (!isOpen) {
    return null;
  }

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 10000,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: 'rgba(15, 23, 42, 0.22)',
        padding: '16px',
      }}
    >
      <div
        style={{
          width: '100%',
          maxWidth: '420px',
          borderRadius: '20px',
          border: '1px solid #e5e7eb',
          background: '#ffffff',
          color: '#111827',
          boxShadow: '0 20px 50px rgba(15, 23, 42, 0.12)',
          padding: '28px',
        }}
      >
        <div style={{ fontSize: '28px', fontWeight: 700, marginBottom: '10px' }}>
          {prompt.title}
        </div>
        <div style={{ color: '#4b5563', lineHeight: 1.7, marginBottom: '24px' }}>
          {prompt.message}
        </div>
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '12px' }}>
          <button
            onClick={() => setIsOpen(false)}
            style={{
              border: '1px solid #d1d5db',
              background: '#ffffff',
              color: '#374151',
              borderRadius: '12px',
              padding: '10px 18px',
              cursor: 'pointer',
              fontWeight: 500,
            }}
          >
            取消
          </button>
          <button
            onClick={() => {
              setIsOpen(false);
              navigate('/auth?modal=login');
            }}
            style={{
              border: 'none',
              background: '#111827',
              color: '#fff',
              borderRadius: '12px',
              padding: '10px 18px',
              fontWeight: 700,
              cursor: 'pointer',
            }}
          >
            {prompt.confirmText}
          </button>
        </div>
      </div>
    </div>
  );
}
