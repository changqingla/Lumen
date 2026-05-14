/**
 * 笔记页面
 * 完整实现文件夹管理、笔记CRUD、自动保存
 */
import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import Sidebar from '@/app/components/Sidebar/Sidebar';
import OptimizedMarkdown from '@/shared/components/OptimizedMarkdown';
import { api, noteAPI } from '@/shared/api/client';
import { useGuestMode } from '@/shared/hooks/useGuestMode';
import { useToast } from '@/shared/hooks/useToast';
import { useChatSessions } from '@/features/chat/hooks/useChatSessions';
import ConfirmModal from '@/shared/components/ConfirmModal/ConfirmModal';
import { getErrorMessage } from '@/shared/utils/errorMessage';
import {
  Plus,
  Folder,
  Trash2,
  MoreVertical,
  Edit3,
  Check,
  Eye,
  PenTool,
  Menu
} from 'lucide-react';

import styles from './NotesPage.module.css';

interface Note {
  id: string;
  title: string;
  content: string;
  folderId?: string;
  tags: string[];
  updatedAt: string;
  createdAt: string;
}

interface FolderData {
  id: string;
  name: string;
  count: number;
}

const SYSTEM_FOLDERS = ['学习', '工作', '生活', '对话笔记'];
const PROTECTED_FOLDER = '生活';

export default function NotesPage() {
  const toast = useToast();
  const navigate = useNavigate();
  const { isGuestMode, promptLogin } = useGuestMode();
  const { chatSessions, refreshSessions } = useChatSessions();
  
  // UI State
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);
  const [isMobile, setIsMobile] = useState(false);
  
  // Data State
  const [folders, setFolders] = useState<FolderData[]>([]);
  const [notes, setNotes] = useState<Note[]>([]);
  const [selectedFolder, setSelectedFolder] = useState<string>('all');
  const [selectedNote, setSelectedNote] = useState<Note | null>(null);
  
  // Editor State
  const [noteTitle, setNoteTitle] = useState('');
  const [noteContent, setNoteContent] = useState('');
  const [saving, setSaving] = useState(false);
  const [isPreviewMode, setIsPreviewMode] = useState(false);
  
  // Folder Edit State
  const [editingFolderId, setEditingFolderId] = useState<string | null>(null);
  const [editingFolderName, setEditingFolderName] = useState('');
  const [creatingFolder, setCreatingFolder] = useState(false);
  const [newFolderName, setNewFolderName] = useState('');
  const [openMenuId, setOpenMenuId] = useState<string | null>(null);
  
  // Modal State
  const [deleteModalOpen, setDeleteModalOpen] = useState(false);
  const [folderToDelete, setFolderToDelete] = useState<FolderData | null>(null);
  
  // Drag State
  const [draggedNote, setDraggedNote] = useState<Note | null>(null);
  const [dragOverFolderId, setDragOverFolderId] = useState<string | null>(null);

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

  const loadFolders = useCallback(async () => {
    try {
      const response = await noteAPI.listFolders();
      setFolders(response);
    } catch (error: unknown) {
      console.error('Failed to load folders:', error);
      toast.error(getErrorMessage(error, '加载文件夹失败'));
    }
  }, [toast]);

  const loadNotes = useCallback(async () => {
    try {
      const folderId = selectedFolder === 'all' ? undefined : selectedFolder;
      const response = await noteAPI.listNotes(folderId, undefined, 1, 100);
      setNotes(response.items);
    } catch (error: unknown) {
      console.error('Failed to load notes:', error);
      toast.error(getErrorMessage(error, '加载笔记失败'));
    }
  }, [selectedFolder, toast]);

  useEffect(() => {
    if (isGuestMode) {
      setFolders([]);
      return;
    }
    void loadFolders();
  }, [isGuestMode, loadFolders]);

  useEffect(() => {
    if (isGuestMode) {
      setNotes([]);
      return;
    }
    void loadNotes();
  }, [isGuestMode, loadNotes]);

  // 自动保存
  useEffect(() => {
    if (isGuestMode) return;
    if (!selectedNote) return;
    
    const hasChanges = noteTitle !== selectedNote.title || noteContent !== selectedNote.content;
    if (!hasChanges) return;

    setSaving(true);
    const timer = setTimeout(async () => {
      try {
        const response = await noteAPI.updateNote(selectedNote.id, {
          title: noteTitle,
          content: noteContent
        });
        
        const updatedAt = response?.updatedAt || new Date().toISOString();
        
        // 更新本地状态
        setNotes(prevNotes => prevNotes.map(n => 
          n.id === selectedNote.id 
            ? { ...n, title: noteTitle, content: noteContent, updatedAt }
            : n
        ));
        
        setSelectedNote(prev => prev ? { ...prev, title: noteTitle, content: noteContent, updatedAt } : null);
      } catch (error: unknown) {
        toast.error(getErrorMessage(error, '保存失败'));
      } finally {
        setSaving(false);
      }
    }, 1000);

    return () => clearTimeout(timer);
  }, [isGuestMode, noteTitle, noteContent, selectedNote, toast]);

  // 同步当前笔记状态到列表
  const syncCurrentNoteToList = useCallback(() => {
    if (selectedNote && (noteTitle !== selectedNote.title || noteContent !== selectedNote.content)) {
      setNotes(prevNotes => prevNotes.map(n => 
        n.id === selectedNote.id 
          ? { ...n, title: noteTitle, content: noteContent, updatedAt: new Date().toISOString() }
          : n
      ));
    }
  }, [selectedNote, noteTitle, noteContent]);

  const handleNoteClick = (note: Note) => {
    // 切换前同步当前笔记状态
    syncCurrentNoteToList();

    setSelectedNote(note);
    setNoteTitle(note.title);
    setNoteContent(note.content);
    // 默认以预览模式打开笔记
    setIsPreviewMode(true);
  };

  const handleNewNote = async () => {
    if (isGuestMode) {
      promptLogin({
        title: '登录后可创建笔记',
        message: '游客模式下暂不支持创建和编辑笔记，请先登录。',
        confirmText: '去登录',
      });
      return;
    }

    try {
      // 切换前同步当前笔记状态
      syncCurrentNoteToList();
      
      const folderId = selectedFolder === 'all' ? undefined : selectedFolder;
      const response = await noteAPI.createNote({
        title: '新笔记',
        content: '',
        folder: folderId,
        tags: []
      });
      
      await loadNotes();
      await loadFolders();
      
      // 创建完整的 Note 对象
      const newNote: Note = {
        id: response.id,
        title: response.title,
        content: response.content || '',
        folderId: response.folderId,
        tags: response.tags || [],
        updatedAt: response.updatedAt || new Date().toISOString(),
        createdAt: response.createdAt || new Date().toISOString()
      };
      
      setSelectedNote(newNote);
      setNoteTitle(newNote.title);
      setNoteContent(newNote.content);
      // 新笔记默认以编辑模式打开
      setIsPreviewMode(false);
    } catch (error: unknown) {
      toast.error(getErrorMessage(error, '创建笔记失败'));
    }
  };

  const handleDeleteNote = async () => {
    if (isGuestMode) {
      promptLogin({
        title: '登录后可删除笔记',
        message: '游客模式下暂不支持编辑笔记，请先登录。',
        confirmText: '去登录',
      });
      return;
    }

    if (!selectedNote) return;
    
    try {
      await noteAPI.deleteNote(selectedNote.id);
      await loadNotes();
      await loadFolders();
      
      setSelectedNote(null);
      setNoteTitle('');
      setNoteContent('');
      
      toast.success('笔记已删除');
    } catch (error: unknown) {
      toast.error(getErrorMessage(error, '删除笔记失败'));
    }
  };

  // 文件夹管理
  const handleCreateFolder = async () => {
    if (isGuestMode) {
      promptLogin({
        title: '登录后可管理文件夹',
        message: '游客模式下暂不支持创建和编辑文件夹，请先登录。',
        confirmText: '去登录',
      });
      return;
    }

    if (!newFolderName.trim()) {
      toast.warning('请输入文件夹名称');
      return;
    }

    if (SYSTEM_FOLDERS.includes(newFolderName.trim())) {
      toast.warning('不能使用系统默认文件夹名称');
      return;
    }

    try {
      await noteAPI.createFolder(newFolderName.trim());
      await loadFolders();
      setCreatingFolder(false);
      setNewFolderName('');
      toast.success('文件夹创建成功');
    } catch (error: unknown) {
      toast.error(getErrorMessage(error, '创建文件夹失败'));
    }
  };

  const handleRenameFolder = async (folder: FolderData) => {
    if (isGuestMode) {
      promptLogin({
        title: '登录后可管理文件夹',
        message: '游客模式下暂不支持创建和编辑文件夹，请先登录。',
        confirmText: '去登录',
      });
      return;
    }

    if (folder.name === PROTECTED_FOLDER) {
      toast.warning('生活才是生命的真谛，不允许重命名');
      return;
    }
    
    setEditingFolderId(folder.id);
    setEditingFolderName(folder.name);
    setOpenMenuId(null);
  };

  const handleSaveRename = async () => {
    if (isGuestMode) {
      promptLogin({
        title: '登录后可管理文件夹',
        message: '游客模式下暂不支持创建和编辑文件夹，请先登录。',
        confirmText: '去登录',
      });
      return;
    }

    if (!editingFolderId) return;
    
    if (!editingFolderName.trim()) {
      toast.warning('文件夹名称不能为空');
      return;
    }

    if (SYSTEM_FOLDERS.includes(editingFolderName.trim())) {
      toast.warning('不能使用系统默认文件夹名称');
      return;
    }

    try {
      await noteAPI.renameFolder(editingFolderId, editingFolderName.trim());
      await loadFolders();
      setEditingFolderId(null);
      setEditingFolderName('');
      toast.success('文件夹重命名成功');
    } catch (error: unknown) {
      toast.error(getErrorMessage(error, '重命名失败'));
    }
  };

  const handleDeleteFolder = (folder: FolderData) => {
    if (isGuestMode) {
      promptLogin({
        title: '登录后可管理文件夹',
        message: '游客模式下暂不支持创建和编辑文件夹，请先登录。',
        confirmText: '去登录',
      });
      return;
    }

    if (folder.name === PROTECTED_FOLDER) {
      toast.warning('生活才是生命的真谛，不允许删除');
      return;
    }
    
    setFolderToDelete(folder);
    setDeleteModalOpen(true);
    setOpenMenuId(null);
  };

  const confirmDeleteFolder = async () => {
    if (isGuestMode) {
      promptLogin({
        title: '登录后可管理文件夹',
        message: '游客模式下暂不支持创建和编辑文件夹，请先登录。',
        confirmText: '去登录',
      });
      return;
    }

    if (!folderToDelete) return;
    
    try {
      await noteAPI.deleteFolder(folderToDelete.id);
      await loadFolders();
      await loadNotes();
      
      if (selectedFolder === folderToDelete.id) {
        setSelectedFolder('all');
      }
      
      toast.success('文件夹已删除');
    } catch (error: unknown) {
      toast.error(getErrorMessage(error, '删除文件夹失败'));
    } finally {
      setDeleteModalOpen(false);
      setFolderToDelete(null);
    }
  };

  // 聊天处理函数
  const handleNewChat = () => {
    if (isGuestMode) {
      promptLogin({
        title: '登录后可新建对话',
        message: '游客模式下仅支持浏览页面和发送 3 条消息，新建对话需要先登录。',
        confirmText: '去登录',
      });
      return;
    }
    navigate('/');
  };

  const handleSelectChat = (chatId: string) => {
    navigate(`/?chatId=${chatId}`);
  };

  const handleDeleteChat = async (chatId: string) => {
    if (isGuestMode) {
      promptLogin({
        title: '登录后可管理对话',
        message: '删除历史对话需要先登录。',
        confirmText: '去登录',
      });
      return;
    }

    try {
      await api.deleteChatSession(chatId);
      await refreshSessions();
      toast.success('对话已删除');
    } catch (error) {
      console.error('Failed to delete chat:', error);
      toast.error('删除对话失败');
    }
  };

  // 拖拽处理函数
  const handleDragStart = (e: React.DragEvent, note: Note) => {
    setDraggedNote(note);
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', note.id);
  };

  const handleDragEnd = () => {
    setDraggedNote(null);
    setDragOverFolderId(null);
  };

  const handleDragOver = (e: React.DragEvent, folderId: string) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    setDragOverFolderId(folderId);
  };

  const handleDragLeave = () => {
    setDragOverFolderId(null);
  };

  const handleDrop = async (e: React.DragEvent, targetFolderId: string | null) => {
    if (isGuestMode) {
      promptLogin({
        title: '登录后可整理笔记',
        message: '游客模式下暂不支持移动笔记，请先登录。',
        confirmText: '去登录',
      });
      return;
    }

    e.preventDefault();
    setDragOverFolderId(null);
    
    if (!draggedNote) return;
    
    // 如果拖到同一个文件夹，不做任何操作
    const currentFolderId = draggedNote.folderId || null;
    if (currentFolderId === targetFolderId) {
      setDraggedNote(null);
      return;
    }
    
    try {
      await noteAPI.updateNote(draggedNote.id, { folderId: targetFolderId });
      
      // 更新本地状态
      setNotes(prevNotes => prevNotes.map(n => 
        n.id === draggedNote.id ? { ...n, folderId: targetFolderId || undefined } : n
      ));
      
      // 如果当前选中的笔记被移动了，更新选中状态
      if (selectedNote?.id === draggedNote.id) {
        setSelectedNote(prev => prev ? { ...prev, folderId: targetFolderId || undefined } : null);
      }
      
      // 刷新文件夹计数
      await loadFolders();
      
      // 如果当前不是"全部"视图，且笔记被移出当前文件夹，从列表中移除
      if (selectedFolder !== 'all' && selectedFolder !== targetFolderId) {
        setNotes(prevNotes => prevNotes.filter(n => n.id !== draggedNote.id));
      }
      
      const targetFolder = folders.find(f => f.id === targetFolderId);
      toast.success(`已移动到「${targetFolder?.name || '全部'}」`);
    } catch (error: unknown) {
      toast.error(getErrorMessage(error, '移动笔记失败'));
    } finally {
      setDraggedNote(null);
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

      <ConfirmModal
        isOpen={deleteModalOpen}
        title="删除文件夹"
        message={`确定要删除文件夹「${folderToDelete?.name}」吗？文件夹中的笔记将移至「全部」。`}
        type="danger"
        confirmText="删除"
        cancelText="取消"
        onConfirm={confirmDeleteFolder}
        onCancel={() => {
          setDeleteModalOpen(false);
          setFolderToDelete(null);
        }}
      />

      <div className={styles.main}>
        <div className={styles.contentArea}>
          {/* 左侧：文件夹列表 */}
          <aside className={styles.folderSidebar}>
            <div className={styles.sidebarHeader}>
              <h2 className={styles.sidebarTitle}>我的笔记</h2>
              <button 
                className={styles.addFolderBtn}
                onClick={() => setCreatingFolder(true)}
                title="新建文件夹"
              >
                <Plus size={16} />
              </button>
            </div>

            <div className={styles.folderList}>
              <button
                className={`${styles.folderItem} ${selectedFolder === 'all' ? styles.folderActive : ''} ${dragOverFolderId === 'all' ? styles.folderDragOver : ''}`}
                onClick={() => setSelectedFolder('all')}
                onDragOver={(e) => handleDragOver(e, 'all')}
                onDragLeave={handleDragLeave}
                onDrop={(e) => handleDrop(e, null)}
              >
                <Folder size={16} />
                <span>全部</span>
                <span className={styles.folderCount}>
                  {folders.reduce((sum, f) => sum + f.count, 0)}
                </span>
              </button>

              {folders.map(folder => (
                <div key={folder.id} className={styles.folderItemWrapper}>
                  {editingFolderId === folder.id ? (
                    <div className={styles.folderEdit}>
                      <input
                        type="text"
                        value={editingFolderName}
                        onChange={(e) => setEditingFolderName(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') handleSaveRename();
                          if (e.key === 'Escape') setEditingFolderId(null);
                        }}
                        onBlur={handleSaveRename}
                        className={styles.folderInput}
                        autoFocus
                      />
                      <button onClick={handleSaveRename} className={styles.saveBtn}>
                        <Check size={14} />
                      </button>
                    </div>
                  ) : (
                    <>
                      <button
                        className={`${styles.folderItem} ${selectedFolder === folder.id ? styles.folderActive : ''} ${dragOverFolderId === folder.id ? styles.folderDragOver : ''}`}
                        onClick={() => setSelectedFolder(folder.id)}
                        onDragOver={(e) => handleDragOver(e, folder.id)}
                        onDragLeave={handleDragLeave}
                        onDrop={(e) => handleDrop(e, folder.id)}
                      >
                        <Folder size={16} />
                        <span>{folder.name}</span>
                        <span className={styles.folderCount}>{folder.count}</span>
                      </button>
                      <div className={styles.folderMenu}>
                        <button
                          className={styles.menuBtn}
                          onClick={() => setOpenMenuId(openMenuId === folder.id ? null : folder.id)}
                        >
                          <MoreVertical size={14} />
                        </button>
                        {openMenuId === folder.id && (
                          <div className={styles.menuDropdown}>
                            <button
                              className={styles.menuItem}
                              onClick={() => handleRenameFolder(folder)}
                            >
                              <Edit3 size={14} />
                              重命名
                            </button>
                            <button
                              className={`${styles.menuItem} ${styles.menuItemDanger}`}
                              onClick={() => handleDeleteFolder(folder)}
                            >
                              <Trash2 size={14} />
                              删除
                            </button>
                          </div>
                        )}
                      </div>
                    </>
                  )}
                </div>
              ))}

              {creatingFolder && (
                <div className={styles.folderEdit}>
                  <input
                    type="text"
                    value={newFolderName}
                    onChange={(e) => setNewFolderName(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') handleCreateFolder();
                      if (e.key === 'Escape') {
                        setCreatingFolder(false);
                        setNewFolderName('');
                      }
                    }}
                    onBlur={() => {
                      if (newFolderName.trim()) {
                        handleCreateFolder();
                      } else {
                        setCreatingFolder(false);
                      }
                    }}
                    className={styles.folderInput}
                    placeholder="未命名的文件夹"
                    autoFocus
                  />
                  <button onClick={handleCreateFolder} className={styles.saveBtn}>
                    <Check size={14} />
                  </button>
                </div>
              )}
            </div>
          </aside>

          {/* 中间：笔记列表 */}
          <section className={styles.notesList}>
            <div className={styles.notesHeader}>
              <button className={styles.newNoteBtn} onClick={handleNewNote}>
                <Plus size={16} />
                新笔记
              </button>
            </div>

            <div className={styles.notesContent}>
              {notes.length === 0 ? (
                <div className={styles.emptyNotes}>
                  <Edit3 size={48} className={styles.emptyIcon} />
                  <p>还没有笔记，点击"新笔记"开始</p>
                </div>
              ) : (
                notes.map(note => (
                  <div
                    key={note.id}
                    className={`${styles.noteItem} ${selectedNote?.id === note.id ? styles.noteActive : ''} ${draggedNote?.id === note.id ? styles.noteDragging : ''}`}
                    onClick={() => handleNoteClick(note)}
                    draggable
                    onDragStart={(e) => handleDragStart(e, note)}
                    onDragEnd={handleDragEnd}
                  >
                    <div className={styles.noteTitle}>{note.title || '无标题'}</div>
                    <div className={styles.notePreview}>
                      {note.content.substring(0, 100) || '空笔记'}
                    </div>
                    <div className={styles.noteMeta}>
                      {new Date(note.updatedAt).toLocaleString('zh-CN', {
                        month: 'numeric',
                        day: 'numeric',
                        hour: '2-digit',
                        minute: '2-digit'
                      })}
                    </div>
                  </div>
                ))
              )}
            </div>
          </section>

          {/* 右侧：编辑器 */}
          <section className={styles.editor}>
            {selectedNote ? (
              <>
                <div className={styles.editorHeader}>
                  <input
                    type="text"
                    value={noteTitle}
                    onChange={(e) => setNoteTitle(e.target.value)}
                    className={styles.editorTitle}
                    placeholder="笔记标题"
                  />
                  <div className={styles.editorActions}>
                    {saving && <span className={styles.savingText}>保存中...</span>}
                    {!saving && selectedNote && (
                      <span className={styles.savedText}>
                        {new Date(selectedNote.updatedAt).toLocaleString('zh-CN', {
                          month: 'numeric',
                          day: 'numeric',
                          hour: '2-digit',
                          minute: '2-digit'
                        })}
                      </span>
                    )}
                    <button
                      className={styles.previewBtn}
                      onClick={() => setIsPreviewMode(!isPreviewMode)}
                      title={isPreviewMode ? "编辑" : "预览"}
                    >
                      {isPreviewMode ? <PenTool size={16} /> : <Eye size={16} />}
                    </button>
                    <button
                      className={styles.deleteBtn}
                      onClick={handleDeleteNote}
                      title="删除笔记"
                    >
                      <Trash2 size={16} />
                    </button>
                  </div>
                </div>
                {isPreviewMode ? (
                  <div className={styles.previewContent}>
                    <OptimizedMarkdown>{noteContent}</OptimizedMarkdown>
                  </div>
                ) : (
                  <textarea
                    value={noteContent}
                    onChange={(e) => setNoteContent(e.target.value)}
                    className={styles.editorContent}
                    placeholder="开始写笔记..."
                  />
                )}
              </>
            ) : (
              <div className={styles.editorEmpty}>
                <Edit3 size={64} className={styles.emptyIcon} />
                <p>选择一个笔记开始编辑</p>
              </div>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}
