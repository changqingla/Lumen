import React, { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Plus, Sun, Moon, Headphones, LogOut, Book, Star, Notebook, ChevronsLeft, ChevronsRight, MoreVertical, Trash2, User, Building2, CreditCard, Settings as SettingsIcon, MessageSquareX, Bot } from 'lucide-react';
import styles from './Sidebar.module.css';
import { useTheme } from '@/shared/hooks/useTheme';
import { useGuestMode } from '@/shared/hooks/useGuestMode';
import { useToast } from '@/shared/hooks/useToast';
import ContactModal from '@/shared/components/ContactModal/ContactModal';
import UserBadge from '@/shared/components/UserBadge/UserBadge';
import ProfileModal from '@/shared/components/ProfileModal/ProfileModal';
import { OrganizationManagerModal } from '@/features/organization';
import ConfirmModal from '@/shared/components/ConfirmModal/ConfirmModal';
import ModelConfigModal from '@/shared/components/ModelConfigModal/ModelConfigModal';
import { dispatchAuthSessionReset } from '@/shared/lib/auth-runtime';
import { api } from '@/shared/api/client';
import defaultAvatar from '@/assets/default-avatar.svg';

interface Chat {
  id: string;
  title: string;
  lastMessage: string;
  timestamp: string; // 显示用的相对时间
  createdAt: string; // ISO日期字符串，用于分类
}

interface SidebarProps {
  onNewChat: () => void;
  onSelectChat: (chatId: string) => void;
  onDeleteChat?: (chatId: string) => void;  // 添加删除回调
  onClearAllChats?: () => void | Promise<void>;  // 清除所有对话后的回调
  selectedChatId?: string;
  collapsed?: boolean;
  onToggleCollapse?: () => void;
  chats?: Chat[];  // 添加 chats 属性
}

type UserLevel = 'basic' | 'member' | 'premium' | 'admin';

const normalizeUserLevel = (value: unknown): UserLevel => {
  if (value === 'member' || value === 'premium' || value === 'admin') {
    return value;
  }
  return 'basic';
};

export default function Sidebar({ onNewChat, onSelectChat, onDeleteChat, onClearAllChats, selectedChatId, collapsed: controlledCollapsed, onToggleCollapse, chats = [] }: SidebarProps) {
  const navigate = useNavigate();
  const { isDark, toggleTheme } = useTheme();
  const { isGuestMode, promptLogin } = useGuestMode();
  const toast = useToast();
  const [internalCollapsed, setInternalCollapsed] = useState(false);
  const [menuOpenChatId, setMenuOpenChatId] = useState<string | null>(null);
  
  // 使用外部控制的 collapsed 或内部状态
  const collapsed = controlledCollapsed !== undefined ? controlledCollapsed : internalCollapsed;
  const [isProfileOpen, setIsProfileOpen] = useState(false);
  const [isProfileModalOpen, setIsProfileModalOpen] = useState(false);
  const [profileInitialTab, setProfileInitialTab] = useState<'profile' | 'organization'>('profile');
  const [isOrgManagerOpen, setIsOrgManagerOpen] = useState(false);
  const [isContactModalOpen, setIsContactModalOpen] = useState(false);
  const [isModelConfigOpen, setIsModelConfigOpen] = useState(false);
  const [isClearChatsModalOpen, setIsClearChatsModalOpen] = useState(false);
  const [isClearingChats, setIsClearingChats] = useState(false);
  const profileButtonRef = useRef<HTMLButtonElement | null>(null);
  const profilePopoverRef = useRef<HTMLDivElement | null>(null);
  const [profile, setProfile] = useState<{ 
    name: string; 
    email: string;
    avatar?: string | null;
    user_level?: string;
    is_admin?: boolean;
  }>({
    name: '用户',
    email: 'user@example.com',
    avatar: null,
    user_level: 'basic',
    is_admin: false,
  });

  useEffect(() => {
    try {
      const saved = localStorage.getItem('userProfile');
      if (saved) {
        const parsed = JSON.parse(saved);
        if (parsed) {
          setProfile({
            name: parsed.name || '用户',
            email: parsed.email || '',
            avatar: parsed.avatar || null,
            user_level: parsed.user_level || 'basic',
            is_admin: parsed.is_admin || false,
          });
        }
      }
    } catch (error) {
      console.warn('Failed to read cached user profile:', error);
    }
  }, []);

  useEffect(() => {
    if (!isGuestMode) {
      return;
    }

    setProfile({
      name: '游客',
      email: '',
      avatar: null,
      user_level: 'basic',
      is_admin: false,
    });
  }, [isGuestMode]);

  // 监听 localStorage 变化，实时更新用户信息
  useEffect(() => {
    const handleStorageChange = (e: StorageEvent) => {
      if (e.key === 'userProfile' && e.newValue) {
        try {
          const parsed = JSON.parse(e.newValue);
          setProfile({
            name: parsed.name || '用户',
            email: parsed.email || '',
            avatar: parsed.avatar || null,
            user_level: parsed.user_level || 'basic',
            is_admin: parsed.is_admin || false,
          });
        } catch (error) {
          console.warn('Failed to parse updated user profile:', error);
        }
      }
    };

    window.addEventListener('storage', handleStorageChange);
    return () => window.removeEventListener('storage', handleStorageChange);
  }, []);

  // 监听全局事件：从其他组件（如 QuotaExceededModal）打开 ProfileModal
  useEffect(() => {
    const handleOpenProfileModal = () => {
      setProfileInitialTab('profile');
      setIsProfileModalOpen(true);
      setIsProfileOpen(false); // 关闭头像弹出菜单
    };

    window.addEventListener('openProfileModal', handleOpenProfileModal);
    return () => {
      window.removeEventListener('openProfileModal', handleOpenProfileModal);
    };
  }, []);

  const handleOrgManagerClick = () => {
    setIsOrgManagerOpen(true);
    setIsProfileOpen(false);
  };

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      const target = e.target as Node;
      if (
        isProfileOpen &&
        profilePopoverRef.current &&
        !profilePopoverRef.current.contains(target) &&
        profileButtonRef.current &&
        !profileButtonRef.current.contains(target)
      ) {
        setIsProfileOpen(false);
      }
    };
    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setIsProfileOpen(false);
    };
    document.addEventListener('mousedown', handleClickOutside);
    document.addEventListener('keydown', handleEscape);
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
      document.removeEventListener('keydown', handleEscape);
    };
  }, [isProfileOpen]);

  const handleChatClick = (chatId: string) => {
    onSelectChat(chatId);
    // 不跳转路由，停留在主页显示对话内容
  };

  const handleNewChatClick = () => {
    if (isGuestMode) {
      promptLogin({
        title: '登录后可新建对话',
        message: '游客模式下仅支持浏览页面和发送 3 条消息，新建对话需要先登录。',
        confirmText: '去登录',
      });
      return;
    }

    try {
      // ✅ 清除 localStorage 中保存的会话ID，确保跳转到首页时显示空白的新对话界面
      // 这样可以避免从其他页面点击"新建对话"时，自动恢复首页的历史会话
      localStorage.removeItem('home_last_session_id');
    } catch (error) {
      console.error('Failed to remove session ID from localStorage:', error);
    }

    try {
      onNewChat();
    } finally {
      navigate('/');
    }
  };

  const handleDeleteChat = (e: React.MouseEvent, chatId: string) => {
    e.stopPropagation(); // 阻止触发聊天项的点击事件
    if (isGuestMode) {
      promptLogin({
        title: '登录后可管理对话',
        message: '删除历史对话需要先登录。',
        confirmText: '去登录',
      });
      return;
    }
    if (onDeleteChat) {
      onDeleteChat(chatId);
    }
    setMenuOpenChatId(null);
  };

  const toggleMenu = (e: React.MouseEvent, chatId: string) => {
    e.stopPropagation();
    setMenuOpenChatId(menuOpenChatId === chatId ? null : chatId);
  };

  // 清除所有对话
  const handleClearAllChats = async () => {
    if (isGuestMode) {
      promptLogin({
        title: '登录后可清空对话',
        message: '游客模式下暂不支持批量管理对话，登录后可继续操作。',
        confirmText: '去登录',
      });
      return;
    }

    setIsClearingChats(true);
    try {
      await api.deleteAllChatSessions();
      toast.success('已清除所有对话');
      setIsClearChatsModalOpen(false);
      setIsProfileOpen(false);
      // 调用回调通知父组件刷新
      if (onClearAllChats) {
        await onClearAllChats();
      }
    } catch (error) {
      console.error('Failed to clear all chats:', error);
      toast.error('清除对话失败');
    } finally {
      setIsClearingChats(false);
    }
  };

  // 点击外部关闭菜单
  useEffect(() => {
    const handleClickOutside = () => setMenuOpenChatId(null);
    if (menuOpenChatId) {
      document.addEventListener('click', handleClickOutside);
      return () => document.removeEventListener('click', handleClickOutside);
    }
  }, [menuOpenChatId]);

  return (
    <div className={`${styles.sidebar} ${collapsed ? styles.collapsed : ''}`}>
      {/* Collapse control row */}
      <div className={styles.headerTop}>
        <button 
          className={styles.collapseBtn} 
          onClick={() => {
            if (onToggleCollapse) {
              onToggleCollapse();
            } else {
              setInternalCollapsed(v => !v);
            }
          }} 
          aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        >
          {collapsed ? <ChevronsRight size={16} /> : <ChevronsLeft size={16} />}
        </button>
      </div>

      {/* New Chat row */}
      <div className={styles.header}>
        <button className={styles.newChatButton} onClick={handleNewChatClick}>
          <Plus size={16} />
          <span className={styles.label}>New chat</span>
        </button>
      </div>

      {/* Quick Links under New Chat */}
      <div className={styles.contentSection}>
        <button type="button" className={styles.contentItem} onClick={() => navigate('/knowledge')}>
          <Book size={16} />
          <span className={styles.label}>知识库</span>
        </button>
        <button type="button" className={styles.contentItem} onClick={() => navigate('/favorites')}>
          <Star size={16} />
          <span className={styles.label}>收藏</span>
        </button>
        <button type="button" className={styles.contentItem} onClick={() => navigate('/notes')}>
          <Notebook size={16} />
          <span className={styles.label}>笔记</span>
        </button>
      </div>

      {/* Chat List */}
      <div className={styles.chatList}>
        {chats.length > 0 && (() => {
          const now = new Date();
          const sevenDaysAgo = new Date(now.getTime() - 7 * 24 * 60 * 60 * 1000);
          
          // 根据 createdAt 分类
          const recentChats = chats.filter(c => {
            const createdDate = new Date(c.createdAt);
            return createdDate >= sevenDaysAgo;
          });
          
          const olderChats = chats.filter(c => {
            const createdDate = new Date(c.createdAt);
            return createdDate < sevenDaysAgo;
          });
          
          return (
            <>
              {recentChats.length > 0 && (
                <>
                  <div className={styles.sectionTitle}>近七天</div>
                  {recentChats.map((chat) => (
          <div
            key={chat.id}
            className={`${styles.chatItem} ${selectedChatId === chat.id ? styles.selected : ''}`}
          >
            <button
              type="button"
              className={styles.chatSelectButton}
              onClick={() => handleChatClick(chat.id)}
              aria-pressed={selectedChatId === chat.id}
            >
              <div className={styles.chatContent}>
                <div className={styles.chatTitle}>{chat.title}</div>
              </div>
            </button>
            <div className={styles.chatActions}>
              <button 
                type="button"
                className={styles.menuButton}
                onClick={(e) => toggleMenu(e, chat.id)}
                title="更多操作"
                aria-label={`打开对话 ${chat.title} 的更多操作`}
                aria-haspopup="menu"
                aria-expanded={menuOpenChatId === chat.id}
              >
                <MoreVertical size={16} />
              </button>
              {menuOpenChatId === chat.id && (
                <div className={styles.chatMenu} role="menu">
                  <button 
                    type="button"
                    className={styles.menuItem}
                    onClick={(e) => handleDeleteChat(e, chat.id)}
                    role="menuitem"
                  >
                    <Trash2 size={14} />
                    <span>删除对话</span>
                  </button>
                </div>
              )}
            </div>
          </div>
        ))}
                </>
              )}
              
              {olderChats.length > 0 && (
                <>
                  <div className={styles.sectionTitle}>更早</div>
                  {olderChats.map((chat) => (
              <div
                key={chat.id}
                className={`${styles.chatItem} ${selectedChatId === chat.id ? styles.selected : ''}`}
              >
                <button
                  type="button"
                  className={styles.chatSelectButton}
                  onClick={() => handleChatClick(chat.id)}
                  aria-pressed={selectedChatId === chat.id}
                >
                  <div className={styles.chatContent}>
                    <div className={styles.chatTitle}>{chat.title}</div>
                  </div>
                </button>
                <div className={styles.chatActions}>
                  <button 
                    type="button"
                    className={styles.menuButton}
                    onClick={(e) => toggleMenu(e, chat.id)}
                    title="更多操作"
                    aria-label={`打开对话 ${chat.title} 的更多操作`}
                    aria-haspopup="menu"
                    aria-expanded={menuOpenChatId === chat.id}
                  >
                    <MoreVertical size={16} />
                  </button>
                  {menuOpenChatId === chat.id && (
                    <div className={styles.chatMenu} role="menu">
                      <button 
                        type="button"
                        className={styles.menuItem}
                        onClick={(e) => handleDeleteChat(e, chat.id)}
                        role="menuitem"
                      >
                        <Trash2 size={14} />
                        <span>删除对话</span>
                      </button>
                    </div>
                  )}
                </div>
              </div>
            ))}
                </>
              )}
            </>
          );
        })()}
      </div>

      {/* Bottom Section with Avatar */}
      <div className={styles.bottomSection}>
        <div className={styles.userRow}>
          <button
            ref={profileButtonRef}
            className={styles.avatarButton}
            onClick={() => setIsProfileOpen(v => !v)}
            aria-label="用户菜单"
          >
            <img 
              src={profile.avatar || defaultAvatar} 
              alt={profile.name} 
              className={styles.avatarImage}
            />
          </button>
          <div className={styles.userMeta}>
            <div className={styles.userName}>
              {profile.name}
              {(profile.is_admin || (profile.user_level && profile.user_level !== 'basic')) && (
                <UserBadge 
                  level={profile.is_admin ? 'admin' : normalizeUserLevel(profile.user_level)} 
                  size="small" 
                />
              )}
            </div>
          </div>
          {/* settings button removed as requested */}
        </div>
        {isProfileOpen && (
          <div ref={profilePopoverRef} className={styles.avatarPopover} role="dialog" aria-label="用户菜单">
            <div className={styles.menuList}>
              <button 
                className={styles.menuItem} 
                onClick={() => {
                  if (isGuestMode) {
                    promptLogin({
                      title: '登录后可查看个人中心',
                      message: '游客模式下暂不提供个人资料和会员能力，请先登录。',
                      confirmText: '去登录',
                    });
                    return;
                  }
                  setProfileInitialTab('profile');
                  setIsProfileModalOpen(true);
                  setIsProfileOpen(false);
                }}
              >
                <span className={styles.menuIcon}><User size={16} /></span>
                <span>个人中心</span>
              </button>
              <button 
                className={styles.menuItem} 
                onClick={() => {
                  if (isGuestMode) {
                    promptLogin({
                      title: '登录后可管理组织',
                      message: '组织相关功能需要先登录。',
                      confirmText: '去登录',
                    });
                    return;
                  }
                  handleOrgManagerClick();
                }}
              >
                <span className={styles.menuIcon}><Building2 size={16} /></span>
                <span>组织管理</span>
              </button>
              <button
                className={styles.menuItem}
                onClick={() => {
                  if (isGuestMode) {
                    promptLogin({
                      title: '登录后可配置模型',
                      message: '模型配置属于账号能力，登录后即可使用。',
                      confirmText: '去登录',
                    });
                    return;
                  }
                  setIsModelConfigOpen(true);
                  setIsProfileOpen(false);
                }}
              >
                <span className={styles.menuIcon}><Bot size={16} /></span>
                <span>模型配置</span>
              </button>
              {!profile.is_admin && profile.user_level === 'basic' && (
                <button 
                  className={styles.menuItem} 
                  onClick={() => {
                    if (isGuestMode) {
                      promptLogin({
                        title: '登录后可升级会员',
                        message: '游客模式下暂不支持会员操作，请先登录。',
                        confirmText: '去登录',
                      });
                      return;
                    }
                    setIsProfileModalOpen(true);
                    setIsProfileOpen(false);
                  }}
                >
                  <span className={styles.menuIcon}><CreditCard size={16} /></span>
                  <span>升级会员</span>
                </button>
              )}
              {profile.is_admin && (
                <button 
                  className={styles.menuItem} 
                  onClick={() => {
                    if (isGuestMode) {
                      promptLogin({
                        title: '登录后可进入管理后台',
                        message: '后台管理能力需要先登录。',
                        confirmText: '去登录',
                      });
                      return;
                    }
                    navigate('/admin');
                    setIsProfileOpen(false);
                  }}
                >
                  <span className={styles.menuIcon}><SettingsIcon size={16} /></span>
                  <span>管理后台</span>
                </button>
              )}
              <button className={styles.menuItem} onClick={toggleTheme}>
                <span className={styles.menuIcon}>{isDark ? <Sun size={16} /> : <Moon size={16} />}</span>
                <span>{isDark ? '日间模式' : '夜间模式'}</span>
              </button>
              <button
                className={styles.menuItem}
                onClick={() => {
                  if (isGuestMode) {
                    promptLogin({
                      title: '登录后可清空对话',
                      message: '游客模式下暂不支持批量管理对话，登录后可继续操作。',
                      confirmText: '去登录',
                    });
                    return;
                  }
                  setIsClearChatsModalOpen(true);
                  setIsProfileOpen(false);
                }}
              >
                <span className={styles.menuIcon}><MessageSquareX size={16} /></span>
                <span>对话清除</span>
              </button>
              <button
                className={styles.menuItem}
                onClick={() => {
                  setIsContactModalOpen(true);
                }}
              >
                <span className={styles.menuIcon}><Headphones size={16} /></span>
                <span>联系我们</span>
              </button>
              <button
                type="button"
                className={`${styles.menuItem} ${styles.menuDanger}`}
                onClick={() => {
                  if (isGuestMode) {
                    promptLogin({
                      title: '继续体验请先登录',
                      message: '游客模式下点击这里会带你回到首页登录弹窗。',
                      confirmText: '去登录',
                    });
                    return;
                  }
                  try {
                    localStorage.removeItem('userProfile');
                    localStorage.removeItem('auth_token');
                    localStorage.removeItem('auth_user');
                    dispatchAuthSessionReset();
                  } catch (error) {
                    console.warn('Failed to clear auth cache during logout:', error);
                  }
                  setIsProfileOpen(false);
                  navigate('/auth');
                }}
              >
                <span className={styles.menuIcon}><LogOut size={16} /></span>
                <span>退出登录</span>
              </button>
            </div>
          </div>
        )}
      </div>

      {/* 联系方式弹窗 */}
      <ContactModal 
        isOpen={isContactModalOpen}
        onClose={() => setIsContactModalOpen(false)}
      />

      <ProfileModal
        isOpen={isProfileModalOpen}
        onClose={() => setIsProfileModalOpen(false)}
        initialTab={profileInitialTab}
      />

      <OrganizationManagerModal
        isOpen={isOrgManagerOpen}
        onClose={() => setIsOrgManagerOpen(false)}
        userLevel={profile.user_level || 'basic'}
      />

      <ModelConfigModal
        isOpen={isModelConfigOpen}
        onClose={() => setIsModelConfigOpen(false)}
      />

      {/* 清除对话确认弹窗 */}
      <ConfirmModal
        isOpen={isClearChatsModalOpen}
        onCancel={() => setIsClearChatsModalOpen(false)}
        onConfirm={handleClearAllChats}
        title="清除所有对话"
        message="确定要清除所有历史对话吗？此操作不可恢复。"
        confirmText="确定清除"
        cancelText="取消"
        type="danger"
        loading={isClearingChats}
      />
    </div>
  );
}
