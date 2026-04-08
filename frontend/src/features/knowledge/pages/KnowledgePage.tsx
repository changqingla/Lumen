/**
 * 论文广场页面
 * 显示公开知识库、支持分类浏览、精选列表
 */
import React, { useEffect, useRef, useState } from 'react';
import { Search, Database, Users, User, Menu, Flame, ChevronDown, ChevronUp } from 'lucide-react';
import Sidebar from '@/app/components/Sidebar/Sidebar';
import {
  KnowledgeSidebar,
  CreateKnowledgeModal,
  EditKnowledgeModal,
} from '@/features/knowledge';
import { api, kbAPI, type KnowledgeBaseListItem } from '@/shared/api/client';
import { useNavigate } from 'react-router-dom';
import { useToast } from '@/shared/hooks/useToast';
import { useChatSessions } from '@/features/chat/hooks/useChatSessions';
import { CATEGORY_ICONS } from '@/constants/categories';
import { getKnowledgeBaseAvatar, hasCustomKnowledgeBaseAvatar } from '@/shared/utils/avatarUtils';
import type { KnowledgeBaseFormData } from '@/features/knowledge/types/forms';
import styles from './KnowledgePage.module.css';

// 第一行显示的 5 个分类（共6个，包括精选）
const FIRST_ROW_CATEGORIES = ["工学", "理学", "法学", "文学", "教育学"];

// 第二行显示的 8 个分类
const SECOND_ROW_CATEGORIES = ["经济学","历史学", "哲学", "农学", "医学", "管理学", "艺术学", "其它"];
const ALL_CATEGORIES = ['精选', ...FIRST_ROW_CATEGORIES, ...SECOND_ROW_CATEGORIES];
const ACTIVE_CATEGORY_STORAGE_KEY = 'knowledge_active_category';
const MORE_CATEGORIES_STORAGE_KEY = 'knowledge_show_more_categories';

const getErrorMessage = (error: unknown, fallback: string): string => {
  if (error instanceof Error && error.message) {
    return error.message;
  }
  return fallback;
};

export default function Knowledge() {
  const navigate = useNavigate();
  const toast = useToast();
  const { chatSessions, refreshSessions } = useChatSessions();
  
  // UI State
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);
  const [isMobile, setIsMobile] = useState(false);
  
  // Modal State
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false);
  const [isEditModalOpen, setIsEditModalOpen] = useState(false);
  const [kbToEdit, setKbToEdit] = useState<KnowledgeBaseListItem | null>(null);
  
  // Data State
  const [myKnowledgeBases, setMyKnowledgeBases] = useState<KnowledgeBaseListItem[]>([]);
  const [publicKbs, setPublicKbs] = useState<KnowledgeBaseListItem[]>([]);
  const [loading, setLoading] = useState(false);
  const publicKbsRequestRef = useRef(0);
  
  // Filter State
  const [query, setQuery] = useState('');
  const [activeCategory, setActiveCategory] = useState(() => {
    if (typeof window === 'undefined') return '精选';
    try {
      const savedCategory = localStorage.getItem(ACTIVE_CATEGORY_STORAGE_KEY);
      if (savedCategory && ALL_CATEGORIES.includes(savedCategory)) {
        return savedCategory;
      }
    } catch {
      // ignore localStorage read failures
    }
    return '精选';
  });
  const [showMoreCategories, setShowMoreCategories] = useState(() => {
    if (typeof window === 'undefined') return false;
    try {
      if (localStorage.getItem(MORE_CATEGORIES_STORAGE_KEY) === '1') {
        return true;
      }
      const savedCategory = localStorage.getItem(ACTIVE_CATEGORY_STORAGE_KEY);
      return Boolean(savedCategory && SECOND_ROW_CATEGORIES.includes(savedCategory));
    } catch {
      return false;
    }
  });

  useEffect(() => {
    const check = () => {
      const isNarrowViewport = window.innerWidth <= 768;
      const isTouchDevice = window.matchMedia('(pointer: coarse)').matches;
      setIsMobile(isNarrowViewport && isTouchDevice);
    };
    check();
    window.addEventListener('resize', check);
    return () => window.removeEventListener('resize', check);
  }, []);

  useEffect(() => {
    try {
      localStorage.setItem(ACTIVE_CATEGORY_STORAGE_KEY, activeCategory);
    } catch {
      // ignore localStorage write failures
    }
  }, [activeCategory]);

  const loadKnowledgeBases = React.useCallback(async () => {
    try {
      const response = await kbAPI.listKnowledgeBases();
      setMyKnowledgeBases(response.items);
    } catch (error) {
      console.error('Failed to load knowledge bases:', error);
    }
  }, []);

  const loadPublicKBs = React.useCallback(async () => {
    const requestId = publicKbsRequestRef.current + 1;
    publicKbsRequestRef.current = requestId;
    setLoading(true);
    try {
      if (activeCategory === '精选') {
        const { items } = await kbAPI.listFeatured(1, 30);
        if (requestId === publicKbsRequestRef.current) {
          setPublicKbs(items);
        }
      } else {
        // 使用新的 plaza API，后端会根据用户权限自动过滤
        const { items } = await kbAPI.getPlaza(
          activeCategory,
          query || undefined,
          1,
          20
        );
        if (requestId === publicKbsRequestRef.current) {
          setPublicKbs(items);
        }
      }
    } catch (error) {
      console.error('加载知识广场失败:', error);
      if (requestId === publicKbsRequestRef.current) {
        setPublicKbs([]);
      }
    } finally {
      if (requestId === publicKbsRequestRef.current) {
        setLoading(false);
      }
    }
  }, [activeCategory, query]);

  useEffect(() => {
    loadKnowledgeBases();
  }, [loadKnowledgeBases]);

  useEffect(() => {
    loadPublicKBs();
  }, [loadPublicKBs]);

  const handleCreateKB = async (data: KnowledgeBaseFormData) => {
    try {
      await kbAPI.createKnowledgeBase(data.name, data.description, data.category);
      await loadKnowledgeBases();
      toast.success('知识库创建成功！');
    } catch (error) {
      toast.error(getErrorMessage(error, '创建知识库失败'));
    }
  };

  const handleEditKB = (kb: KnowledgeBaseListItem) => {
    setKbToEdit(kb);
    setIsEditModalOpen(true);
  };

  const handleSaveKB = async (data: KnowledgeBaseFormData) => {
    if (!kbToEdit) return;
    try {
      await kbAPI.updateKnowledgeBase(kbToEdit.id, data);
      await loadKnowledgeBases();
      toast.success('知识库已更新');
      setIsEditModalOpen(false);
      setKbToEdit(null);
    } catch (error) {
      toast.error(getErrorMessage(error, '更新失败'));
    }
  };

  const handleDeleteKB = async (kbId: string) => {
    try {
      await kbAPI.deleteKnowledgeBase(kbId);
      await loadKnowledgeBases();
      toast.success('知识库已删除');
    } catch (error) {
      toast.error(getErrorMessage(error, '删除失败'));
    }
  };

  const handleCategoryClick = (category: string) => {
    if (SECOND_ROW_CATEGORIES.includes(category)) {
      setShowMoreCategories(true);
      try {
        localStorage.setItem(MORE_CATEGORIES_STORAGE_KEY, '1');
      } catch {
        // ignore localStorage write failures
      }
    }
    setActiveCategory(category);
  };

  const handleToggleMoreCategories = () => {
    setShowMoreCategories(prev => {
      const next = !prev;
      try {
        localStorage.setItem(MORE_CATEGORIES_STORAGE_KEY, next ? '1' : '0');
      } catch {
        // ignore localStorage write failures
      }
      return next;
    });
  };

  const handleKBClick = (kbId: string) => {
    navigate(`/knowledge/${kbId}`);
  };

  // 聊天处理函数
  const handleNewChat = () => {
    navigate('/');
  };

  const handleSelectChat = (chatId: string) => {
    navigate(`/?chatId=${chatId}`);
  };

  const handleDeleteChat = async (chatId: string) => {
    try {
      await api.deleteChatSession(chatId);
      await refreshSessions();
      toast.success('对话已删除');
    } catch (error) {
      console.error('Failed to delete chat:', error);
      toast.error('删除对话失败');
    }
  };

  return (
    <div className={styles.page}>
      {isMobile && isSidebarOpen && (
        <div className={styles.overlay} onClick={() => setIsSidebarOpen(false)} />
      )}

      <div className={`${styles.sidebarContainer} ${isMobile && isSidebarOpen ? styles.open : ''}`}>
        <Sidebar 
          onNewChat={handleNewChat}
          onSelectChat={handleSelectChat}
          onDeleteChat={handleDeleteChat}
          chats={chatSessions}
        />
      </div>

      {isMobile && !isSidebarOpen && (
        <button
          className={styles.mobileMenuButton}
          onClick={() => setIsSidebarOpen(true)}
          aria-label="打开侧边栏"
        >
          <Menu size={20} />
        </button>
      )}

      {/* Modals */}
      <CreateKnowledgeModal
        isOpen={isCreateModalOpen}
        onClose={() => setIsCreateModalOpen(false)}
        onSubmit={handleCreateKB}
      />

      {kbToEdit && (
        <EditKnowledgeModal
          isOpen={isEditModalOpen}
          onClose={() => {
            setIsEditModalOpen(false);
            setKbToEdit(null);
          }}
          onSave={handleSaveKB}
          initialData={{
            name: kbToEdit.name,
            description: kbToEdit.description,
            category: kbToEdit.category || '其它'
          }}
        />
      )}

      <div className={styles.main}>
        <div className={styles.contentArea}>
          {/* Knowledge Sidebar */}
          <KnowledgeSidebar
            knowledgeBases={myKnowledgeBases}
            onCreateClick={() => setIsCreateModalOpen(true)}
            onEditClick={handleEditKB}
            onDeleteClick={handleDeleteKB}
          />

          {/* Knowledge Square */}
          <section className={styles.hubMain}>
            <header className={styles.hubHeader}>
              <h1 className={styles.hubTitle}>知识广场</h1>
              <p className={styles.hubSubtitle}>发现高质量公开知识库，按学科快速筛选</p>
            </header>

            {/* Search */}
            <div className={styles.searchWrap}>
              <Search size={18} className={styles.searchIcon} />
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    loadPublicKBs();
                  }
                }}
                className={styles.search}
                placeholder="试试搜索感兴趣的知识库"
              />
            </div>

            {/* Category Tags - 主分类 */}
            <div className={`${styles.tags} ${styles.tagsMain}`}>
              <button
                className={`${styles.tag} ${activeCategory === '精选' ? styles.tagActive : ''}`}
                onClick={() => handleCategoryClick('精选')}
              >
                <Flame size={14} />
                精选
              </button>
              
              {FIRST_ROW_CATEGORIES.map(cat => {
                const Icon = CATEGORY_ICONS[cat];
                return (
                  <button
                    key={cat}
                    className={`${styles.tag} ${activeCategory === cat ? styles.tagActive : ''}`}
                    onClick={() => handleCategoryClick(cat)}
                  >
                    {Icon && <Icon size={14} />} {cat}
                  </button>
                );
              })}

              <button
                className={styles.moreCategoriesBtn}
                onClick={handleToggleMoreCategories}
                aria-expanded={showMoreCategories}
                aria-label={showMoreCategories ? '收起更多分类' : '展开更多分类'}
              >
                {showMoreCategories ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                <span>{showMoreCategories ? '收起' : '更多分类'}</span>
              </button>
            </div>

            {showMoreCategories && (
              <div className={`${styles.tags} ${styles.tagsSecondary}`}>
                {SECOND_ROW_CATEGORIES.map(cat => {
                  const Icon = CATEGORY_ICONS[cat];
                  return (
                    <button
                      key={cat}
                      className={`${styles.tag} ${activeCategory === cat ? styles.tagActive : ''}`}
                      onClick={() => handleCategoryClick(cat)}
                    >
                      {Icon && <Icon size={14} />} {cat}
                    </button>
                  );
                })}
              </div>
            )}

            {/* Knowledge Base List */}
            <div className={styles.feed}>
              {loading ? (
                <div className={styles.loadingState}>加载中...</div>
              ) : publicKbs.length === 0 ? (
                <div className={styles.emptyState}>
                  <div className={styles.emptyText}>暂无公开知识库</div>
                  <div className={styles.emptyHint}>快去创建并公开你的第一个知识库吧</div>
                </div>
              ) : (
                publicKbs.map(kb => {
                  const CategoryIcon = CATEGORY_ICONS[kb.category];
                  const hasCustomAvatar = hasCustomKnowledgeBaseAvatar(kb);
                  return (
                    <div 
                      key={kb.id} 
                      className={styles.feedItem}
                      onClick={() => handleKBClick(kb.id)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' || e.key === ' ') {
                          e.preventDefault();
                          handleKBClick(kb.id);
                        }
                      }}
                      role="button"
                      tabIndex={0}
                    >
                      <div className={styles.feedIcon}>
                        <img
                          src={getKnowledgeBaseAvatar(kb)}
                          alt={kb.name}
                          className={`${styles.kbAvatar} ${hasCustomAvatar ? styles.kbAvatarCustom : styles.kbAvatarDefault}`}
                        />
                      </div>
                      <div className={styles.feedBody}>
                        <div className={styles.feedHeader}>
                          <div className={styles.feedHeaderTop}>
                            <div className={styles.titleGroup}>
                              <div className={styles.feedTitle}>{kb.name}</div>
                              <div className={styles.badges}>
                                {kb.category && (
                                  <div className={styles.categoryBadge}>
                                    {CategoryIcon && <CategoryIcon size={12} />}
                                    <span>{kb.category}</span>
                                  </div>
                                )}
                                {(kb.is_admin_recommended || kb.from_organization) && (
                                  <div className={styles.sourceTag}>
                                    {kb.is_admin_recommended
                                      ? '来自：Lumen官方'
                                      : `组织：${kb.organization_name}`}
                                  </div>
                                )}
                              </div>
                            </div>
                          </div>
                        </div>
                        <div className={styles.feedDesc}>{kb.description || '暂无描述'}</div>
                        <div className={styles.feedMeta}>
                          <div className={styles.stats}>
                            <span className={styles.metaChip}>
                              <Users size={12} /> {kb.subscribersCount || 0} 订阅
                            </span>
                            <span className={styles.metaChip}>
                              <Database size={12} /> {kb.contents || 0} 文档
                            </span>
                          </div>
                          <div className={styles.feedActions}>
                            {kb.creator_name && (
                              <div className={styles.creatorInfo}>
                                {kb.creator_avatar ? (
                                  <img src={kb.creator_avatar} alt={kb.creator_name} className={styles.creatorAvatar} />
                                ) : (
                                  <div className={styles.creatorAvatar} style={{display: 'flex', alignItems: 'center', justifyContent: 'center', background: '#f1f5f9'}}>
                                     <User size={12} color="#64748b" />
                                  </div>
                                )}
                                <span className={styles.creatorName}>{kb.creator_name}</span>
                              </div>
                            )}
                          </div>
                        </div>
                      </div>
                    </div>
                  );
                })
              )}
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}
