/**
 * 知识库侧边栏组件
 * 显示"我的知识库"和"订阅的知识库"列表
 */
import React, { useEffect, useState, useImperativeHandle, forwardRef } from 'react';
import { Plus, Database, MoreVertical, Edit2, Trash2 } from 'lucide-react';
import { useNavigate, useLocation } from 'react-router-dom';
import { kbAPI, type KnowledgeBaseListItem } from '@/shared/api/client';
import { getKnowledgeBaseAvatar } from '@/shared/utils/avatarUtils';
import styles from './KnowledgeSidebar.module.css';

type KnowledgeSidebarItem = Pick<KnowledgeBaseListItem, 'id' | 'name' | 'avatar' | 'contents'>;

interface KnowledgeSidebarProps {
  knowledgeBases: KnowledgeSidebarItem[];
  onCreateClick: () => void;
  onEditClick?: (kb: KnowledgeSidebarItem) => void;
  onDeleteClick?: (kbId: string) => void;
  onDocumentDrop?: (docId: string, sourceKbId: string, targetKbId: string) => void;
  currentKbId?: string;
}

export interface KnowledgeSidebarRef {
  refreshSubscriptions: () => Promise<void>;
}

const KnowledgeSidebar = forwardRef<KnowledgeSidebarRef, KnowledgeSidebarProps>(({
  knowledgeBases,
  onCreateClick,
  onEditClick,
  onDeleteClick,
  onDocumentDrop,
  currentKbId
}, ref) => {
  const navigate = useNavigate();
  const location = useLocation();
  const [menuOpen, setMenuOpen] = useState<string | null>(null);
  const [subscriptions, setSubscriptions] = useState<KnowledgeSidebarItem[]>([]);
  const [dragOverKbId, setDragOverKbId] = useState<string | null>(null);

  useEffect(() => {
    loadSubscriptions();

    const handleSubscriptionChange = (event: StorageEvent) => {
      if (event.key === 'kb_subscription_changed') {
        loadSubscriptions();
      }
    };

    const handleCustomEvent = () => {
      loadSubscriptions();
    };

    window.addEventListener('storage', handleSubscriptionChange);
    window.addEventListener('kb_subscription_changed', handleCustomEvent);

    return () => {
      window.removeEventListener('storage', handleSubscriptionChange);
      window.removeEventListener('kb_subscription_changed', handleCustomEvent);
    };
  }, []);

  const loadSubscriptions = async () => {
    try {
      const response = await kbAPI.listSubscriptions();
      setSubscriptions((response.items || []) as KnowledgeSidebarItem[]);
    } catch (error) {
      console.error('Failed to load subscriptions:', error);
    }
  };

  useImperativeHandle(ref, () => ({
    refreshSubscriptions: loadSubscriptions
  }));

  const handleKBClick = (kbId: string) => {
    navigate(`/knowledge/${kbId}`);
    setMenuOpen(null);
  };

  const handleMenuClick = (event: React.MouseEvent, kbId: string) => {
    event.stopPropagation();
    setMenuOpen(menuOpen === kbId ? null : kbId);
  };

  const handleEdit = (event: React.MouseEvent, kb: KnowledgeSidebarItem) => {
    event.stopPropagation();
    setMenuOpen(null);
    if (onEditClick) {
      onEditClick(kb);
    }
  };

  const handleDelete = (event: React.MouseEvent, kbId: string) => {
    event.stopPropagation();
    setMenuOpen(null);
    if (onDeleteClick) {
      onDeleteClick(kbId);
    }
  };

  const isActive = (kbId: string) => location.pathname === `/knowledge/${kbId}`;

  const handleDragOver = (event: React.DragEvent, kbId: string) => {
    event.preventDefault();
    if (kbId === currentKbId) return;
    event.dataTransfer.dropEffect = 'move';
    setDragOverKbId(kbId);
  };

  const handleDragLeave = () => {
    setDragOverKbId(null);
  };

  const handleDrop = (event: React.DragEvent, targetKbId: string) => {
    event.preventDefault();
    setDragOverKbId(null);

    if (targetKbId === currentKbId) return;

    const docId = event.dataTransfer.getData('docId');
    const sourceKbId = event.dataTransfer.getData('sourceKbId');

    if (docId && sourceKbId && onDocumentDrop) {
      onDocumentDrop(docId, sourceKbId, targetKbId);
    }
  };

  return (
    <div className={styles.sidebar}>
      <div className={styles.section}>
        <div className={styles.sectionHeader}>
          <h3 className={styles.sectionTitle}>我的知识库</h3>
          <button className={styles.addBtn} onClick={onCreateClick} title="新建知识库">
            <Plus size={16} />
          </button>
        </div>

        <div className={styles.list}>
          {knowledgeBases.length === 0 ? (
            <div className={styles.empty}>
              <Database size={24} className={styles.emptyIcon} />
              <p className={styles.emptyText}>还没有知识库</p>
              <button className={styles.emptyBtn} onClick={onCreateClick}>
                创建第一个
              </button>
            </div>
          ) : (
            knowledgeBases.map((kb) => (
              <div
                key={kb.id}
                className={`${styles.item} ${isActive(kb.id) ? styles.active : ''} ${dragOverKbId === kb.id ? styles.dragOver : ''}`}
                onClick={() => handleKBClick(kb.id)}
                onDragOver={(event) => handleDragOver(event, kb.id)}
                onDragLeave={handleDragLeave}
                onDrop={(event) => handleDrop(event, kb.id)}
              >
                <img src={getKnowledgeBaseAvatar(kb)} alt={kb.name} className={styles.avatar} />
                <div className={styles.itemBody}>
                  <div className={styles.itemName}>{kb.name}</div>
                  <div className={styles.itemMeta}>{kb.contents || 0} 文档</div>
                </div>
                <button
                  className={styles.menuBtn}
                  onClick={(event) => handleMenuClick(event, kb.id)}
                >
                  <MoreVertical size={14} />
                </button>

                {menuOpen === kb.id && (
                  <div className={styles.menu}>
                    <button className={styles.menuItem} onClick={(event) => handleEdit(event, kb)}>
                      <Edit2 size={14} />
                      <span>编辑</span>
                    </button>
                    <button
                      className={`${styles.menuItem} ${styles.danger}`}
                      onClick={(event) => handleDelete(event, kb.id)}
                    >
                      <Trash2 size={14} />
                      <span>删除</span>
                    </button>
                  </div>
                )}
              </div>
            ))
          )}
        </div>
      </div>

      <div className={styles.section}>
        <div className={styles.sectionHeader}>
          <h3 className={styles.sectionTitle}>订阅的知识库</h3>
        </div>

        <div className={styles.list}>
          {subscriptions.length === 0 ? (
            <div className={styles.empty}>
              <Database size={24} className={styles.emptyIcon} />
              <p className={styles.emptyText}>还没有订阅知识库</p>
              <button className={styles.emptyBtn} onClick={() => navigate('/knowledge')}>
                去论文广场看看
              </button>
            </div>
          ) : (
            subscriptions.map((kb) => (
              <div
                key={kb.id}
                className={`${styles.item} ${isActive(kb.id) ? styles.active : ''}`}
                onClick={() => handleKBClick(kb.id)}
              >
                <img src={getKnowledgeBaseAvatar(kb)} alt={kb.name} className={styles.avatar} />
                <div className={styles.itemBody}>
                  <div className={styles.itemName}>{kb.name}</div>
                  <div className={styles.itemMeta}>{kb.contents || 0} 文档</div>
                </div>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
});

KnowledgeSidebar.displayName = 'KnowledgeSidebar';

export default KnowledgeSidebar;
