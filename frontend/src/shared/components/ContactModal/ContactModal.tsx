/**
 * 联系方式弹窗组件
 */
import { X, Mail, Copy, Check, Send } from 'lucide-react';
import { useState } from 'react';
import styles from './ContactModal.module.css';

interface ContactModalProps {
  isOpen: boolean;
  onClose: () => void;
}

export default function ContactModal({ isOpen, onClose }: ContactModalProps) {
  const [copied, setCopied] = useState(false);
  const email = 'ht20201031@163.com';

  if (!isOpen) return null;

  const handleCopyEmail = async () => {
    try {
      await navigator.clipboard.writeText(email);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (error) {
      console.error('Failed to copy email:', error);
      // 降级方案：使用传统方法复制
      const textArea = document.createElement('textarea');
      textArea.value = email;
      textArea.style.position = 'fixed';
      textArea.style.left = '-999999px';
      document.body.appendChild(textArea);
      textArea.select();
      try {
        document.execCommand('copy');
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
      } catch (err) {
        console.error('Fallback copy failed:', err);
      }
      document.body.removeChild(textArea);
    }
  };

  const handleSendEmail = () => {
    window.location.href = `mailto:${email}`;
  };

  return (
    <div className={styles.overlay} onClick={onClose}>
      <div className={styles.modal} onClick={(e) => e.stopPropagation()}>
        <button className={styles.closeBtn} onClick={onClose}>
          <X size={24} />
        </button>
        
        <div className={styles.content}>
          <div className={styles.heroSection}>
            <div className={styles.iconContainer}>
              <Mail size={48} className={styles.heroIcon} strokeWidth={1.5} />
              <div className={styles.iconBlob} />
            </div>
            <h2 className={styles.title}>联系我们</h2>
            <p className={styles.subtitle}>有任何问题或建议？随时发邮件给我们。</p>
          </div>

          <div className={styles.emailCard}>
            <span className={styles.emailText}>{email}</span>
            <div className={styles.emailActions}>
              <button 
                className={`${styles.actionBtn} ${copied ? styles.copied : ''}`} 
                onClick={handleCopyEmail}
                title="复制邮箱"
              >
                {copied ? <Check size={20} /> : <Copy size={20} />}
              </button>
            </div>
          </div>

          <div className={styles.mainActions}>
            <button className={styles.primaryBtn} onClick={handleSendEmail}>
              <Send size={18} />
              <span>发送邮件</span>
            </button>
          </div>

          <p className={styles.footerText}>
            我们会尽快回复您的邮件，感谢您的反馈！
          </p>
        </div>
      </div>
    </div>
  );
}
