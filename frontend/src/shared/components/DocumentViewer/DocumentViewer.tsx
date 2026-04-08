/**
 * 文档查看器组件
 * 支持预览：
 * - DOCX: 使用 mammoth 转换为 HTML（支持图片）
 * - DOC: 显示不支持提示，降级显示 markdown 内容
 * - PPTX: 转换为 HTML 幻灯片
 * - TXT: 直接显示文本内容
 * - MD: 使用 Markdown 渲染
 */
import { useState, useCallback, useRef, useEffect } from 'react';
import { FileText, Star, X, Loader2 } from 'lucide-react';
import { pptxToHtml } from '@jvmr/pptx-to-html';
import OptimizedMarkdown from '@/shared/components/OptimizedMarkdown';
import styles from './DocumentViewer.module.css';

type PreviewType = 'docx' | 'doc' | 'txt' | 'md' | 'pptx';

interface DocumentViewerProps {
  url: string;  // 原文件的 URL
  fileName: string;
  markdownContent?: string;  // 降级用的 markdown 内容（用于 .doc 文件）
  onTextSelect?: (text: string) => void;
  onClose?: () => void;
  onToggleFavorite?: () => void;
  isFavorited?: boolean;
}

export default function DocumentViewer({ 
  url,
  fileName,
  markdownContent,
  onTextSelect, 
  onClose, 
  onToggleFavorite, 
  isFavorited 
}: DocumentViewerProps) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string>('');
  const [htmlContent, setHtmlContent] = useState<string>('');
  const [textContent, setTextContent] = useState<string>('');
  const [pptxSlides, setPptxSlides] = useState<string[]>([]);
  const [selectedText, setSelectedText] = useState<string>('');
  const [showAddButton, setShowAddButton] = useState<boolean>(false);
  const [buttonPosition, setButtonPosition] = useState<{ x: number; y: number }>({ x: 0, y: 0 });
  const containerRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);

  // 获取文件类型
  const getPreviewType = (filename: string): PreviewType => {
    const ext = filename.toLowerCase().split('.').pop();
    switch (ext) {
      case 'docx':
        return 'docx';
      case 'doc':
        return 'doc';
      case 'md':
      case 'markdown':
        return 'md';
      case 'pptx':
        return 'pptx';
      default:
        return 'txt';
    }
  };

  const previewType = getPreviewType(fileName);

  // 检查是否为 ZIP 格式（docx 是 ZIP 容器）
  const isZipLikeBlob = async (blob: Blob): Promise<boolean> => {
    try {
      const headerSlice = blob.slice(0, 4);
      const buf = await headerSlice.arrayBuffer();
      const bytes = new Uint8Array(buf);
      // ZIP files start with "PK" (0x50, 0x4B)
      return bytes.length >= 2 && bytes[0] === 0x50 && bytes[1] === 0x4b;
    } catch (e) {
      console.error('Failed to inspect blob header', e);
      return false;
    }
  };

  // 加载文档内容
  useEffect(() => {
    const controller = new AbortController();
    let isActive = true;

    const loadDocument = async () => {
      if (!url) {
        if (isActive) {
          setLoading(false);
        }
        return;
      }
      
      setLoading(true);
      setError('');
      setHtmlContent('');
      setTextContent('');
      setPptxSlides([]);

      try {
        const response = await fetch(url, { signal: controller.signal });
        if (!response.ok) {
          throw new Error('文档加载失败');
        }

        const blob = await response.blob();

        if (previewType === 'pptx') {
          const arrayBuffer = await blob.arrayBuffer();
          if (!isActive) {
            return;
          }

          const slidesHtml = await pptxToHtml(arrayBuffer, {
            width: 960,
            height: 540,
            scaleToFit: true,
            letterbox: true,
          });

          if (!isActive) {
            return;
          }

          setPptxSlides(slidesHtml);
        } else if (previewType === 'docx' || previewType === 'doc') {
          // 检查是否为 ZIP 格式（真正的 docx）
          const isZip = await isZipLikeBlob(blob);
          if (!isActive) {
            return;
          }
          
          if (isZip) {
            // 使用 mammoth 转换 docx 为 HTML
            const { default: mammoth } = await import('mammoth');
            if (!isActive) {
              return;
            }
            const arrayBuffer = await blob.arrayBuffer();
            if (!isActive) {
              return;
            }
            const result = await mammoth.convertToHtml(
              { arrayBuffer },
              { includeDefaultStyleMap: true }
            );
            if (!isActive) {
              return;
            }
            
            // 添加一些基本样式
            const styledContent = result.value
              .replace(/<p>/g, '<p class="doc-paragraph">')
              .replace(/<h(\d)>/g, '<h$1 class="doc-heading">');
            
            setHtmlContent(styledContent);
          } else {
            // 旧版 .doc 文件，mammoth 不支持
            if (markdownContent) {
              setTextContent(markdownContent);
            } else {
              setError('此文档格式（.doc）暂不支持在线预览，请下载后查看');
            }
          }
        } else if (previewType === 'txt' || previewType === 'md') {
          // 文本文件直接读取
          const text = await blob.text();
          if (!isActive) {
            return;
          }
          setTextContent(text);
        }
      } catch (error: unknown) {
        if (error instanceof DOMException && error.name === 'AbortError') {
          return;
        }
        if (!isActive) {
          return;
        }
        console.error('Error loading document:', error);
        const errorMessage = error instanceof Error ? error.message : '文档加载失败';
        setError(errorMessage);
        // 如果有 markdown 内容作为降级
        if (markdownContent) {
          setTextContent(markdownContent);
          setError('');
        }
      } finally {
        if (isActive) {
          setLoading(false);
        }
      }
    };

    loadDocument();

    return () => {
      isActive = false;
      controller.abort();
    };
  }, [url, previewType, markdownContent]);

  // 处理文本选择
  const handleMouseUp = useCallback(() => {
    // 延迟执行，确保选择已完成
    setTimeout(() => {
      const selection = window.getSelection();
      const text = selection?.toString().trim();
      
      console.log('[DocumentViewer] Mouse up, selected text:', text?.substring(0, 50));
      
      if (text && text.length > 0 && onTextSelect) {
        setSelectedText(text);
        
        const range = selection?.getRangeAt(0);
        if (range) {
          const rect = range.getBoundingClientRect();
          
          // 计算按钮位置
          setButtonPosition({
            x: rect.right + 10,
            y: rect.top - 5
          });
          setShowAddButton(true);
          console.log('[DocumentViewer] Show add button at:', rect.right + 10, rect.top - 5);
        }
      }
    }, 10);
  }, [onTextSelect]);

  // 添加选中文本到对话
  const handleAddToChat = useCallback(() => {
    if (selectedText && onTextSelect) {
      onTextSelect(selectedText);
      setShowAddButton(false);
      setSelectedText('');
      window.getSelection()?.removeAllRanges();
    }
  }, [selectedText, onTextSelect]);

  // 点击其他地方时隐藏按钮
  const handleContainerClick = useCallback((e: React.MouseEvent) => {
    // 如果点击的是添加按钮，不隐藏
    const target = e.target as HTMLElement;
    if (target.closest(`.${styles.addButton}`)) {
      return;
    }
    // 延迟隐藏，避免与 mouseup 事件冲突
    setTimeout(() => {
      const selection = window.getSelection();
      if (!selection || selection.isCollapsed || !selection.toString().trim()) {
        setShowAddButton(false);
      }
    }, 50);
  }, []);

  // 监听选择变化
  useEffect(() => {
    const handleSelectionChange = () => {
      const selection = window.getSelection();
      if (!selection || selection.isCollapsed) {
        setTimeout(() => {
          if (!window.getSelection()?.toString().trim()) {
            setShowAddButton(false);
          }
        }, 100);
      }
    };

    document.addEventListener('selectionchange', handleSelectionChange);
    return () => {
      document.removeEventListener('selectionchange', handleSelectionChange);
    };
  }, []);

  // 渲染内容
  const renderContent = () => {
    if (loading) {
      return (
        <div className={styles.loading}>
          <Loader2 size={32} className={styles.spinner} />
          <p>加载文档中...</p>
        </div>
      );
    }

    if (error) {
      return (
        <div className={styles.error}>
          <FileText size={48} />
          <p>{error}</p>
        </div>
      );
    }

    if (htmlContent) {
      // DOCX 转换后的 HTML
      return (
        <div 
          ref={contentRef}
          className={styles.docContent}
          dangerouslySetInnerHTML={{ __html: htmlContent }}
        />
      );
    }

    if (pptxSlides.length > 0) {
      return (
        <div className={styles.pptxDeck}>
          {pptxSlides.map((slideHtml, index) => (
            <section key={`pptx-slide-${index}`} className={styles.pptxSlideFrame}>
              <div className={styles.pptxSlideMeta}>第 {index + 1} 页</div>
              <div
                className={styles.pptxSlide}
                dangerouslySetInnerHTML={{ __html: slideHtml }}
              />
            </section>
          ))}
        </div>
      );
    }

    if (textContent) {
      if (previewType === 'md') {
        // Markdown 渲染
        return (
          <div className={styles.markdownWrapper}>
            <OptimizedMarkdown>{textContent}</OptimizedMarkdown>
          </div>
        );
      } else {
        // 纯文本
        return (
          <pre className={styles.textContent}>{textContent}</pre>
        );
      }
    }

    return (
      <div className={styles.empty}>
        <FileText size={48} />
        <p>文档内容为空</p>
      </div>
    );
  };

  return (
    <div className={styles.container} ref={containerRef}>
      {/* 工具栏 */}
      <div className={styles.toolbar}>
        <div className={styles.toolbarRight}>
          {onToggleFavorite && (
            <button
              onClick={onToggleFavorite}
              className={`${styles.favoriteBtn} ${isFavorited ? styles.favorited : ''}`}
              title={isFavorited ? "取消收藏" : "收藏文档"}
            >
              <Star size={18} fill={isFavorited ? 'currentColor' : 'none'} />
            </button>
          )}

          {onClose && (
            <button
              onClick={onClose}
              className={styles.closeBtn}
              title="关闭预览"
              aria-label="关闭预览"
            >
              <X size={16} />
            </button>
          )}
        </div>
      </div>

      {/* 文档内容 */}
      <div 
        className={styles.content} 
        onClick={handleContainerClick}
        onMouseUp={handleMouseUp}
      >
        {renderContent()}
      </div>

      {/* 添加到对话按钮 */}
      {showAddButton && onTextSelect && (
        <button
          className={styles.addButton}
          style={{
            position: 'fixed',
            left: `${buttonPosition.x}px`,
            top: `${buttonPosition.y}px`,
          }}
          onClick={(e) => {
            e.stopPropagation();
            handleAddToChat();
          }}
        >
          添加到对话
        </button>
      )}
    </div>
  );
}
