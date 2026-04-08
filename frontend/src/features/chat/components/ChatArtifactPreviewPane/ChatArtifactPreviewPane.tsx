import { FileText, X } from 'lucide-react';
import DocumentViewer from '@/shared/components/DocumentViewer/DocumentViewer';
import type { ChatArtifactPreviewTarget } from '@/features/chat/lib/artifact-preview';
import styles from './ChatArtifactPreviewPane.module.css';

interface ChatArtifactPreviewPaneProps {
  preview: ChatArtifactPreviewTarget | null;
  onClose: () => void;
}

export default function ChatArtifactPreviewPane({
  preview,
  onClose,
}: ChatArtifactPreviewPaneProps) {
  if (!preview) {
    return null;
  }

  if (
    preview.previewType === 'md'
    || preview.previewType === 'txt'
    || preview.previewType === 'docx'
    || preview.previewType === 'pptx'
  ) {
    return (
      <aside className={styles.previewPane}>
        <div className={styles.previewPaneInner}>
          <DocumentViewer
            url={preview.url}
            fileName={preview.fileName}
            onClose={onClose}
          />
        </div>
      </aside>
    );
  }

  if (preview.previewType === 'html') {
    return (
      <aside className={styles.previewPane}>
        <div className={styles.htmlShell}>
          <div className={styles.htmlToolbar}>
            <div className={styles.toolbarActions}>
              <button
                type="button"
                className={`${styles.toolbarButton} ${styles.toolbarButtonSecondary}`}
                onClick={onClose}
                title="关闭预览"
                aria-label="关闭预览"
              >
                <X size={14} />
              </button>
            </div>
          </div>
          <div className={styles.iframeWrap}>
            <iframe
              title={preview.fileName}
              src={preview.url}
              className={styles.htmlFrame}
              sandbox="allow-same-origin"
            />
          </div>
        </div>
      </aside>
    );
  }

  return (
    <aside className={styles.previewPane}>
      <div className={styles.statusShell}>
        <div className={styles.statusCard}>
          <div className={styles.statusHeader}>
            <FileText size={18} />
            <div className={styles.statusTitle}>暂不支持在线预览</div>
          </div>
          <p className={styles.statusText}>
            当前文件 <strong>{preview.fileName}</strong> 还没有接入右侧在线预览。
          </p>
          <p className={styles.statusText}>
            第一轮已优先支持 `md`、`txt`、`docx`、`html`，其余格式可以先下载查看，后续再继续扩展。
          </p>
          <div className={styles.toolbarActions}>
            <button
              type="button"
              className={`${styles.toolbarButton} ${styles.toolbarButtonSecondary}`}
              onClick={onClose}
              title="关闭预览"
              aria-label="关闭预览"
            >
              <X size={14} />
            </button>
          </div>
        </div>
      </div>
    </aside>
  );
}
