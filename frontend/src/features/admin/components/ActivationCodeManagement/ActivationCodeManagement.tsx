/**
 * 激活码管理组件
 * 提供激活码的生成、查看、作废功能
 */
import React, { useState, useEffect, useCallback } from 'react';
import { Plus, Search, Copy, RefreshCw } from 'lucide-react';
import { adminAPI } from '@/shared/api/client';
import { useToast } from '@/shared/hooks/useToast';
import { getErrorMessage } from '@/shared/utils/errorMessage';
import styles from './ActivationCodeManagement.module.css';

interface ActivationCode {
  code: string;
  type: 'member' | 'premium';
  max_uses: number;
  used_count: number;
  expires_at: string | null;
  created_at: string;
  is_active: boolean;
}

export default function ActivationCodeManagement() {
  const toast = useToast();
  const [codes, setCodes] = useState<ActivationCode[]>([]);
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false);

  // 创建表单
  const [formData, setFormData] = useState({
    type: 'member' as 'member' | 'premium',
    maxUses: 1,
    durationDays: 30, // 会员时长（天）
    codeExpiresIn: 0, // 激活码有效期（天），0表示永久
  });

  const loadCodes = useCallback(async () => {
    setLoading(true);
    try {
      const data = await adminAPI.listCodes(undefined, undefined, 1, 100);
      const formattedCodes = data.items.map(item => ({
        code: item.code,
        type: item.type as 'member' | 'premium',
        max_uses: item.max_usage,
        used_count: item.used_count,
        expires_at: item.expires_at,
        created_at: item.created_at,
        is_active: item.is_active,
      }));
      setCodes(formattedCodes);
    } catch (error: unknown) {
      console.error('Failed to load codes:', error);
      toast.error(getErrorMessage(error, '加载激活码失败'));
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    void loadCodes();
  }, [loadCodes]);

  const handleCreateCode = async () => {
    setSubmitting(true);
    try {
      await adminAPI.generateCode({
        type: formData.type,
        max_usage: formData.maxUses,
        duration_days: formData.durationDays || undefined, // 会员时长，0或空表示永久
        code_expires_in_days: formData.codeExpiresIn || undefined, // 激活码有效期
      });
      setIsCreateModalOpen(false);
      toast.success('激活码已生成');
      await loadCodes();
    } catch (error: unknown) {
      console.error('Failed to create code:', error);
      toast.error(getErrorMessage(error, '生成激活码失败'));
    } finally {
      setSubmitting(false);
    }
  };

  const handleRevokeCode = async (code: string) => {
    if (!confirm(`确定要作废激活码 ${code} 吗？`)) return;
    
    try {
      await adminAPI.deactivateCode(code);
      toast.success('激活码已作废');
      await loadCodes();
    } catch (error: unknown) {
      console.error('Failed to revoke code:', error);
      toast.error(getErrorMessage(error, '作废激活码失败'));
    }
  };

  const copyCode = async (e: React.MouseEvent<HTMLButtonElement>, code: string) => {
    e.preventDefault();
    e.stopPropagation();
    
    try {
      // 尝试使用 Clipboard API
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(code);
        toast.success('激活码已复制到剪贴板');
      } else {
        // 降级方案：使用传统方法
        const textArea = document.createElement('textarea');
        textArea.value = code;
        textArea.style.position = 'fixed';
        textArea.style.left = '-999999px';
        document.body.appendChild(textArea);
        textArea.select();
        document.execCommand('copy');
        document.body.removeChild(textArea);
        toast.success('激活码已复制到剪贴板');
      }
    } catch (error) {
      console.error('复制失败:', error);
      toast.error('复制失败，请手动复制');
    }
  };

  const formatDate = (value: string | null) => {
    if (!value) return '永久有效';
    return new Date(value).toLocaleString('zh-CN', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    });
  };

  const getCodeStatus = (code: ActivationCode) => {
    if (!code.is_active) {
      return { text: '已作废', className: styles.statusInactive };
    }
    if (code.used_count >= code.max_uses) {
      return { text: '已用尽', className: styles.statusWarning };
    }
    if (code.expires_at && new Date(code.expires_at).getTime() <= Date.now()) {
      return { text: '已过期', className: styles.statusWarning };
    }
    return { text: '生效中', className: styles.statusActive };
  };

  const filteredCodes = codes.filter((code) =>
    code.code.toLowerCase().includes(searchQuery.toLowerCase())
  );

  return (
    <div className={styles.container}>
      <div className={styles.toolbar}>
        <div className={styles.searchWrap}>
          <Search size={18} />
          <input
            type="text"
            placeholder="搜索激活码..."
            className={styles.searchInput}
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
          />
        </div>
        
        <button
          className={styles.createBtn}
          onClick={() => setIsCreateModalOpen(true)}
        >
          <Plus size={18} />
          生成激活码
        </button>

        <button
          className={styles.refreshBtn}
          onClick={loadCodes}
          disabled={loading}
        >
          <RefreshCw size={16} className={loading ? styles.spinning : ''} />
          刷新
        </button>
      </div>

      {loading ? (
        <div className={styles.loading}>加载中...</div>
      ) : (
        <div className={styles.table}>
          <div className={styles.tableHeader}>
            <div className={styles.col1}>激活码</div>
            <div className={styles.col2}>类型</div>
            <div className={styles.col3}>使用情况</div>
            <div className={styles.col4}>有效期</div>
            <div className={styles.col5}>状态</div>
            <div className={styles.col6}>操作</div>
          </div>
          
          {filteredCodes.map((code) => {
            const status = getCodeStatus(code);

            return (
              <div key={code.code} className={styles.tableRow}>
                <div className={styles.col1}>
                  <code className={styles.codeValue}>{code.code}</code>
                  <button
                    className={styles.copyIcon}
                    onClick={(e) => copyCode(e, code.code)}
                    aria-label={`复制激活码 ${code.code}`}
                  >
                    <Copy size={14} />
                  </button>
                </div>
                
                <div className={styles.col2}>
                  <span className={`${styles.badge} ${styles[code.type]}`}>
                    {code.type === 'member' ? '白银会员' : '白金会员'}
                  </span>
                </div>
                
                <div className={styles.col3}>
                  {code.used_count} / {code.max_uses}
                </div>
                
                <div className={styles.col4}>
                  {formatDate(code.expires_at)}
                </div>
                
                <div className={styles.col5}>
                  <span className={status.className}>{status.text}</span>
                </div>

                <div className={styles.col6}>
                  <button
                    className={styles.revokeBtn}
                    onClick={() => handleRevokeCode(code.code)}
                    disabled={!code.is_active}
                  >
                    作废
                  </button>
                </div>
              </div>
            );
          })}
          
          {filteredCodes.length === 0 && (
            <div className={styles.empty}>未找到匹配的激活码</div>
          )}
        </div>
      )}

      {/* 创建激活码弹窗 */}
      {isCreateModalOpen && (
        <div className={styles.modalOverlay} onClick={() => setIsCreateModalOpen(false)}>
          <div className={styles.modal} onClick={(e) => e.stopPropagation()}>
            <h2 className={styles.modalTitle}>生成激活码</h2>
            
            <div className={styles.formGroup}>
              <label>类型</label>
              <select
                value={formData.type}
                onChange={(e) => setFormData({ ...formData, type: e.target.value as 'member' | 'premium' })}
                className={styles.select}
              >
                <option value="member">白银会员</option>
                <option value="premium">白金会员</option>
              </select>
            </div>
            
            <div className={styles.formGroup}>
              <label>最大使用次数</label>
              <input
                type="number"
                min="1"
                value={formData.maxUses}
                onChange={(e) => setFormData({ ...formData, maxUses: parseInt(e.target.value) })}
                className={styles.input}
              />
            </div>
            
            <div className={styles.formGroup}>
              <label>会员时长（天）</label>
              <input
                type="number"
                min="0"
                value={formData.durationDays}
                onChange={(e) => setFormData({ ...formData, durationDays: parseInt(e.target.value) || 0 })}
                className={styles.input}
                placeholder="0表示永久会员"
              />
              <span className={styles.hint}>激活后会员有效天数，0表示永久</span>
            </div>
            
            <div className={styles.formGroup}>
              <label>激活码有效期（天）</label>
              <input
                type="number"
                min="0"
                value={formData.codeExpiresIn}
                onChange={(e) => setFormData({ ...formData, codeExpiresIn: parseInt(e.target.value) || 0 })}
                className={styles.input}
                placeholder="0表示永久有效"
              />
              <span className={styles.hint}>激活码本身的有效期，0表示永久可用</span>
            </div>
            
            <div className={styles.modalActions}>
              <button className={styles.btnCancel} onClick={() => setIsCreateModalOpen(false)}>
                取消
              </button>
              <button className={styles.btnConfirm} onClick={handleCreateCode} disabled={submitting}>
                {submitting ? '生成中...' : '生成'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
