import React, { useState, useEffect, useCallback, useRef } from 'react';
import { createPortal } from 'react-dom';
import { api } from '@/shared/api/client';
import { getKnowledgeBaseAvatar } from '@/shared/utils/avatarUtils';
import { getFileIcon } from '@/shared/utils/fileIcons';
import { ChevronRight, Database, Loader2, Check } from 'lucide-react';
import styles from './KnowledgeBaseSelector.module.css';

// 类型定义
export interface KnowledgeBase {
  id: string;
  name: string;
  avatar?: string;
  contents?: number;
  description?: string;
}

export interface Document {
  id: string;
  name: string;
  size?: number;
  created_at?: string;
  status?: string;
}

export interface SelectionState {
  selectedKBs: string[];
  selectedDocIds: string[];
  docToKbMap: Record<string, string>;
}

export interface KnowledgeBaseSelectorProps {
  // 选中状态
  selectedKBs: string[];
  selectedDocIds: string[];
  docToKbMap: Record<string, string>;
  
  // 选择变化回调
  onSelectionChange: (selection: SelectionState) => void;
  
  // 是否显示选择器
  isOpen: boolean;
  onClose: () => void;
  
  // 定位相关
  position?: React.CSSProperties;
  
  // 是否显示收藏夹
  showFavorites?: boolean;
  
  // 是否禁用
  disabled?: boolean;
  
  // 自定义样式
  className?: string;
}

export function KnowledgeBaseSelector({
  selectedKBs,
  selectedDocIds,
  docToKbMap,
  onSelectionChange,
  isOpen,
  onClose,
  position,
  showFavorites = true,
  disabled = false,
  className
}: KnowledgeBaseSelectorProps) {
  // 内部状态
  const [myKBs, setMyKBs] = useState<KnowledgeBase[]>([]);
  const [favoriteKBs, setFavoriteKBs] = useState<KnowledgeBase[]>([]);
  const [loadingKBs, setLoadingKBs] = useState(false);
  const [expandedKBs, setExpandedKBs] = useState<Set<string>>(new Set());
  const [kbDocuments, setKbDocuments] = useState<Record<string, Document[]>>({});
  const [loadingKBDocs, setLoadingKBDocs] = useState<Set<string>>(new Set());
  
  const panelRef = useRef<HTMLDivElement>(null);

  // 加载知识库列表
  const loadKnowledgeBases = useCallback(async () => {
    if (loadingKBs) return;
    
    setLoadingKBs(true);
    try {
      const [myKBResponse, favoriteKBResponse] = await Promise.all([
        api.listKnowledgeBases(undefined, 1, 50),
        showFavorites ? api.listFavoriteKBs(1, 50) : Promise.resolve({ items: [] })
      ]);
      setMyKBs(myKBResponse.items || []);
      setFavoriteKBs(favoriteKBResponse.items || []);
    } catch (error) {
      console.error('Failed to load knowledge bases:', error);
    } finally {
      setLoadingKBs(false);
    }
  }, [loadingKBs, showFavorites]);

  // 加载知识库文档
  const loadKBDocuments = useCallback(async (kbId: string): Promise<Document[]> => {
    if (kbDocuments[kbId]) {
      return kbDocuments[kbId];
    }

    setLoadingKBDocs(prev => new Set(prev).add(kbId));
    try {
      const response = await api.listDocuments(kbId, 1, 100);
      const docs = response.items || [];
      
      setKbDocuments(prev => ({ ...prev, [kbId]: docs }));
      
      // 更新 docToKbMap
      const newMapping: Record<string, string> = {};
      docs.forEach((doc: Document) => {
        newMapping[doc.id] = kbId;
      });
      
      onSelectionChange({
        selectedKBs,
        selectedDocIds,
        docToKbMap: { ...docToKbMap, ...newMapping }
      });
      
      return docs;
    } catch (error) {
      console.error(`Failed to load documents for kb ${kbId}:`, error);
      return [];
    } finally {
      setLoadingKBDocs(prev => {
        const newSet = new Set(prev);
        newSet.delete(kbId);
        return newSet;
      });
    }
  }, [kbDocuments, selectedKBs, selectedDocIds, docToKbMap, onSelectionChange]);

  // 展开/折叠知识库
  const toggleKBExpand = useCallback(async (kbId: string) => {
    if (expandedKBs.has(kbId)) {
      setExpandedKBs(prev => {
        const newSet = new Set(prev);
        newSet.delete(kbId);
        return newSet;
      });
    } else {
      setExpandedKBs(prev => new Set(prev).add(kbId));
      await loadKBDocuments(kbId);
    }
  }, [expandedKBs, loadKBDocuments]);

  // 获取知识库选择状态（只考虑 ready 状态的文档）
  const getKBSelectionState = useCallback((kbId: string): 'all' | 'partial' | 'none' => {
    const kbDocs = kbDocuments[kbId] || [];
    // 只考虑 ready 状态的文档
    const readyDocs = kbDocs.filter(doc => doc.status === 'ready');
    if (readyDocs.length === 0) return 'none';
    
    const kbDocIds = readyDocs.map(doc => doc.id);
    const selectedCount = kbDocIds.filter(id => selectedDocIds.includes(id)).length;
    
    if (selectedCount === 0) return 'none';
    if (selectedCount === kbDocIds.length) return 'all';
    return 'partial';
  }, [kbDocuments, selectedDocIds]);

  // 切换知识库选择（全选/取消全选，只操作 ready 状态的文档）
  const toggleKBSelection = useCallback(async (kbId: string) => {
    if (disabled) return;
    
    let kbDocs = kbDocuments[kbId];
    if (!kbDocs) {
      if (!expandedKBs.has(kbId)) {
        setExpandedKBs(prev => new Set(prev).add(kbId));
      }
      kbDocs = await loadKBDocuments(kbId);
    }

    // 只操作 ready 状态的文档
    const readyDocs = kbDocs.filter(doc => doc.status === 'ready');
    const kbDocIds = readyDocs.map(doc => doc.id);
    if (kbDocIds.length === 0) return;

    const allSelected = kbDocIds.every(id => selectedDocIds.includes(id));
    
    let newSelectedKBs: string[];
    let newSelectedDocIds: string[];
    
    if (allSelected) {
      newSelectedDocIds = selectedDocIds.filter(id => !kbDocIds.includes(id));
      newSelectedKBs = selectedKBs.filter(id => id !== kbId);
    } else {
      newSelectedDocIds = [...selectedDocIds];
      kbDocIds.forEach(id => {
        if (!newSelectedDocIds.includes(id)) {
          newSelectedDocIds.push(id);
        }
      });
      newSelectedKBs = selectedKBs.includes(kbId) 
        ? selectedKBs 
        : [...selectedKBs, kbId];
    }
    
    // 更新 docToKbMap
    const newDocToKbMap = { ...docToKbMap };
    kbDocIds.forEach(id => {
      newDocToKbMap[id] = kbId;
    });
    
    onSelectionChange({
      selectedKBs: newSelectedKBs,
      selectedDocIds: newSelectedDocIds,
      docToKbMap: newDocToKbMap
    });
  }, [disabled, kbDocuments, expandedKBs, loadKBDocuments, selectedDocIds, selectedKBs, docToKbMap, onSelectionChange]);

  // 切换单个文档选择
  const toggleDocSelection = useCallback((docId: string, kbId: string) => {
    if (disabled) return;
    
    const isCurrentlySelected = selectedDocIds.includes(docId);
    
    let newSelectedKBs: string[];
    let newSelectedDocIds: string[];
    
    if (isCurrentlySelected) {
      newSelectedDocIds = selectedDocIds.filter(id => id !== docId);
      const hasOtherDocsFromKB = newSelectedDocIds.some(id => docToKbMap[id] === kbId);
      newSelectedKBs = hasOtherDocsFromKB 
        ? selectedKBs 
        : selectedKBs.filter(id => id !== kbId);
    } else {
      newSelectedDocIds = [...selectedDocIds, docId];
      newSelectedKBs = selectedKBs.includes(kbId) 
        ? selectedKBs 
        : [...selectedKBs, kbId];
    }
    
    onSelectionChange({
      selectedKBs: newSelectedKBs,
      selectedDocIds: newSelectedDocIds,
      docToKbMap: { ...docToKbMap, [docId]: kbId }
    });
  }, [disabled, selectedDocIds, selectedKBs, docToKbMap, onSelectionChange]);

  // 点击外部关闭
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (panelRef.current && !panelRef.current.contains(event.target as Node)) {
        onClose();
      }
    };

    if (isOpen) {
      document.addEventListener('mousedown', handleClickOutside);
    }

    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, [isOpen, onClose]);

  // 打开时加载知识库
  useEffect(() => {
    if (isOpen && myKBs.length === 0 && favoriteKBs.length === 0) {
      loadKnowledgeBases();
    }
  }, [isOpen, myKBs.length, favoriteKBs.length, loadKnowledgeBases]);

  // 渲染知识库项
  const renderKBItem = (kb: KnowledgeBase) => {
    const selectionState = getKBSelectionState(kb.id);
    const isExpanded = expandedKBs.has(kb.id);
    const isLoadingDocs = loadingKBDocs.has(kb.id);
    const docs = kbDocuments[kb.id] || [];

    return (
      <div key={kb.id} className={styles.kbTreeItem}>
        <div className={styles.kbItem}>
          <button
            className={`${styles.kbExpandBtn} ${isExpanded ? styles.expanded : ''}`}
            onClick={(e) => {
              e.stopPropagation();
              toggleKBExpand(kb.id);
            }}
          >
            <ChevronRight size={14} />
          </button>
          <input
            type="checkbox"
            checked={selectionState === 'all'}
            ref={(el) => {
              if (el) el.indeterminate = selectionState === 'partial';
            }}
            onChange={() => toggleKBSelection(kb.id)}
            className={styles.kbCheckbox}
            disabled={(kb.contents || 0) === 0 || disabled}
          />
          <div className={styles.kbItemContent} onClick={() => toggleKBExpand(kb.id)}>
            <img src={getKnowledgeBaseAvatar(kb)} alt={kb.name} className={styles.kbAvatar} />
            <span className={styles.kbName}>{kb.name}</span>
            <span className={styles.kbDocCount}>{kb.contents || 0} 篇</span>
          </div>
        </div>
        
        {isExpanded && (
          <div className={styles.docList}>
            {isLoadingDocs ? (
              <div className={styles.docLoading}>
                <Loader2 size={14} className={styles.spinner} />
                <span>加载中...</span>
              </div>
            ) : docs.filter(doc => doc.status === 'ready').length === 0 ? (
              <div className={styles.docEmpty}>暂无可用文档</div>
            ) : (
              docs.filter(doc => doc.status === 'ready').map((doc) => (
                <div key={doc.id} className={styles.docItem}>
                  <input
                    type="checkbox"
                    checked={selectedDocIds.includes(doc.id)}
                    onChange={() => toggleDocSelection(doc.id, kb.id)}
                    className={styles.docCheckbox}
                    disabled={disabled}
                  />
                  <img 
                    src={getFileIcon(doc.name)} 
                    alt="" 
                    className={styles.docIcon}
                  />
                  <span className={styles.docName}>{doc.name}</span>
                </div>
              ))
            )}
          </div>
        )}
      </div>
    );
  };

  if (!isOpen) return null;

  const totalSelected = selectedDocIds.length;

  const panelContent = (
    <div 
      ref={panelRef}
      className={`${styles.kbSelectorPanel} ${className || ''}`}
      style={position}
    >
      {totalSelected > 0 && (
        <div className={styles.kbSelectionStats}>
          <Check size={14} />
          <span>已选择 <strong>{totalSelected}</strong> 篇文档</span>
        </div>
      )}
      
      {loadingKBs ? (
        <div className={styles.kbSelectorEmpty}>
          <Loader2 size={20} className={styles.spinner} />
          <span>加载中...</span>
        </div>
      ) : (
        <>
          {/* 我的知识库 */}
          {myKBs.length > 0 && (
            <div className={styles.kbSection}>
              <div className={styles.kbSectionTitle}>我的知识库</div>
              <div className={styles.kbListContainer}>
                {myKBs.map(renderKBItem)}
              </div>
            </div>
          )}
          
          {/* 收藏的知识库 */}
          {showFavorites && favoriteKBs.length > 0 && (
            <div className={styles.kbSection}>
              <div className={styles.kbSectionTitle}>收藏的知识库</div>
              <div className={styles.kbListContainer}>
                {favoriteKBs.map(renderKBItem)}
              </div>
            </div>
          )}
          
          {/* 空状态 */}
          {myKBs.length === 0 && favoriteKBs.length === 0 && (
            <div className={styles.kbSelectorEmpty}>
              <Database size={24} />
              <span>暂无知识库</span>
            </div>
          )}
        </>
      )}
    </div>
  );

  // 使用 Portal 渲染到 body
  return createPortal(panelContent, document.body);
}
