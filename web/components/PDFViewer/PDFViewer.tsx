/**
 * PDF查看器组件
 * 使用 react-pdf-highlighter 库渲染PDF，支持精确的文本选择
 */
import { useState, useCallback, useRef } from 'react';
import { PdfLoader, PdfHighlighter } from 'react-pdf-highlighter';
import pdfjsPkg from 'react-pdf-highlighter/node_modules/pdfjs-dist/package.json';
import { FileText, Star, X } from 'lucide-react';
import { getFileIcon } from '@/utils/fileIcons';
import styles from './PDFViewer.module.css';
import 'react-pdf-highlighter/dist/style/PdfHighlighter.css';
import 'react-pdf-highlighter/dist/style/Highlight.css';
import 'react-pdf-highlighter/dist/style/AreaHighlight.css';
import 'react-pdf-highlighter/dist/style/MouseSelection.css';
import 'react-pdf-highlighter/dist/style/Tip.css';
import 'react-pdf-highlighter/dist/style/pdf_viewer.css';

interface PDFViewerProps {
  url: string;
  fileName?: string;
  onTextSelect?: (text: string) => void;
  onClose?: () => void;
  onToggleFavorite?: () => void;
  isFavorited?: boolean;
}

export default function PDFViewer({ 
  url, 
  fileName, 
  onTextSelect, 
  onClose, 
  onToggleFavorite, 
  isFavorited 
}: PDFViewerProps) {
  const [error, setError] = useState<string>('');
  const [selectedText, setSelectedText] = useState<string>('');
  const [showAddButton, setShowAddButton] = useState<boolean>(false);
  const [buttonPosition, setButtonPosition] = useState<{ x: number; y: number }>({ x: 0, y: 0 });
  
  const containerRef = useRef<HTMLDivElement>(null);
  const justSelectedRef = useRef<boolean>(false); // 用于防止选择后立即触发点击隐藏

  // 将相对路径转换为绝对路径
  const absoluteUrl = url.startsWith('http') ? url : `${window.location.origin}${url}`;

  // 处理文本选择完成
  const handleSelectionFinished = useCallback((
    position: any,
    content: { text?: string; image?: string }
  ) => {
    const text = content.text?.trim();
    
    if (text && text.length > 0) {
      setSelectedText(text);
      
      // 计算按钮位置
      const { boundingRect } = position;
      if (boundingRect && containerRef.current) {
        const containerRect = containerRef.current.getBoundingClientRect();
        setButtonPosition({
          x: containerRect.left + boundingRect.x2 + 10,
          y: containerRect.top + boundingRect.y1
        });
        setShowAddButton(true);
        
        // 设置标记，防止选择完成后的点击事件立即隐藏按钮
        justSelectedRef.current = true;
        setTimeout(() => {
          justSelectedRef.current = false;
        }, 300); // 300ms 内的点击不会隐藏按钮
      }
    }
    
    return null;
  }, []);

  // 添加选中文本到对话
  const handleAddToChat = useCallback(() => {
    if (selectedText && onTextSelect) {
      onTextSelect(selectedText);
      setShowAddButton(false);
      setSelectedText('');
    }
  }, [selectedText, onTextSelect]);

  // 点击其他地方时隐藏按钮
  const handleContainerClick = useCallback(() => {
    // 如果刚刚完成选择，忽略这次点击
    if (justSelectedRef.current) {
      return;
    }
    if (showAddButton) {
      setShowAddButton(false);
    }
  }, [showAddButton]);

  return (
    <div className={styles.container}>
      {/* 工具栏 */}
      <div className={styles.toolbar}>
        <div className={styles.toolbarLeft}>
          <img src={getFileIcon(fileName || 'document.pdf')} alt="File" className={styles.fileIcon} />
          <span className={styles.fileName}>{fileName || '文档预览'}</span>
        </div>

        <div className={styles.toolbarCenter} />

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
            <button onClick={onClose} className={styles.closeBtn} title="关闭预览">
              <X size={16} />
              <span className={styles.closeBtnText}>关闭预览</span>
            </button>
          )}
        </div>
      </div>

      {/* PDF内容 */}
      <div 
        className={styles.content} 
        ref={containerRef}
        onClick={handleContainerClick}
      >
        {error ? (
          <div className={styles.error}>
            <FileText size={48} />
            <p>{error}</p>
          </div>
        ) : (
          <PdfLoader
            url={absoluteUrl}
            workerSrc={`https://unpkg.com/pdfjs-dist@${pdfjsPkg.version}/build/pdf.worker.min.mjs`}
            cMapUrl="/cmaps/"
            cMapPacked={true}
            beforeLoad={
              <div className={styles.loading}>
                <div className={styles.loadingSpinner} />
                <span className={styles.loadingText}>正在加载文档...</span>
              </div>
            }
            errorMessage={
              <div className={styles.error}>
                <FileText size={48} />
                <p>PDF文档加载失败</p>
              </div>
            }
            onError={(err) => setError(err.message)}
          >
            {(pdfDocument) => (
              <PdfHighlighter
                pdfDocument={pdfDocument}
                enableAreaSelection={(event) => event.altKey}
                onScrollChange={() => {}}
                scrollRef={() => {}}
                onSelectionFinished={handleSelectionFinished}
                highlightTransform={() => null}
                highlights={[]}
                pdfScaleValue="page-width"
              />
            )}
          </PdfLoader>
        )}
      </div>

      {/* 添加到对话按钮 - 仅在 onTextSelect 存在时显示 */}
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
