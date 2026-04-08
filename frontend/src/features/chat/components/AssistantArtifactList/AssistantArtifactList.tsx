import { useCallback, useState } from 'react';
import { Download } from 'lucide-react';
import { api, type ChatArtifact } from '@/shared/api/client';
import { useToast } from '@/shared/hooks/useToast';
import { getFileIcon } from '@/shared/utils/fileIcons';
import styles from './AssistantArtifactList.module.css';

interface AssistantArtifactListProps {
  artifacts?: ChatArtifact[];
  sessionId?: string;
  messageId: string;
}

const formatFileSize = (bytes: number) => {
  if (bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(1))} ${sizes[i]}`;
};

const resolveArtifactName = (artifact: ChatArtifact) => {
  if (artifact.name && artifact.name.trim()) {
    return artifact.name.trim();
  }
  if (artifact.path && artifact.path.trim()) {
    const segments = artifact.path.split('/');
    return segments[segments.length - 1] || '生成文件';
  }
  const objectPath = artifact.object_path || '';
  const parts = objectPath.split('/');
  return parts[parts.length - 1] || '生成文件';
};

export default function AssistantArtifactList({
  artifacts,
  sessionId,
  messageId,
}: AssistantArtifactListProps) {
  const toast = useToast();
  const [artifactLoadingKeys, setArtifactLoadingKeys] = useState<Set<string>>(new Set());

  const normalizedArtifacts = (artifacts || []).filter((artifact) => Boolean(artifact.object_path));
  const handleDownloadArtifact = useCallback(async (artifact: ChatArtifact) => {
    const objectPath = (artifact.object_path || '').trim();
    if (!objectPath) {
      toast.error('附件路径无效，无法下载');
      return;
    }

    const targetSessionId = (artifact.session_id || sessionId || '').trim();
    if (!targetSessionId) {
      toast.error('当前会话不存在，无法下载附件');
      return;
    }

    const loadingKey = `${targetSessionId}::${objectPath}`;
    if (artifactLoadingKeys.has(loadingKey)) {
      return;
    }

    setArtifactLoadingKeys((prev) => {
      const next = new Set(prev);
      next.add(loadingKey);
      return next;
    });

    try {
      let fileName = resolveArtifactName(artifact);
      const { blob, fileName: responseFileName } = await api.downloadSessionArtifact(targetSessionId, objectPath);
      fileName = responseFileName || fileName;
      const downloadUrl = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = downloadUrl;
      link.download = fileName;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      window.setTimeout(() => {
        URL.revokeObjectURL(downloadUrl);
      }, 30_000);
    } catch (error) {
      console.error('Failed to download artifact:', error);
      toast.error(error instanceof Error ? error.message : '下载文件失败，请稍后重试');
    } finally {
      setArtifactLoadingKeys((prev) => {
        const next = new Set(prev);
        next.delete(loadingKey);
        return next;
      });
    }
  }, [artifactLoadingKeys, sessionId, toast]);

  if (!normalizedArtifacts.length) {
    return null;
  }

  return (
    <div className={styles.artifactList}>
      {normalizedArtifacts.map((artifact, artifactIndex) => {
        const targetSessionId = (artifact.session_id || sessionId || '').trim();
        const loadingKey = `${targetSessionId}::${artifact.object_path}`;
        const isDownloading = artifactLoadingKeys.has(loadingKey);
        const artifactName = resolveArtifactName(artifact);
        const canDownload = Boolean(targetSessionId && artifact.object_path);

        return (
          <div
            key={`${messageId}-artifact-${artifact.object_path}-${artifactIndex}`}
            className={styles.artifactCard}
          >
            <div className={styles.artifactInfo}>
              <img src={getFileIcon(artifactName)} alt="artifact" className={styles.artifactIcon} />
              <div className={styles.artifactMeta}>
                <div className={styles.artifactName}>{artifactName}</div>
                <div className={styles.artifactSubMeta}>
                  {typeof artifact.size_bytes === 'number' && artifact.size_bytes >= 0
                    ? formatFileSize(artifact.size_bytes)
                    : '文件已生成'}
                </div>
              </div>
            </div>
            <button
              type="button"
              className={styles.artifactDownloadButton}
              onClick={() => handleDownloadArtifact(artifact)}
              disabled={!canDownload || isDownloading}
            >
              <Download size={14} />
              <span>{isDownloading ? '准备中...' : '下载'}</span>
            </button>
          </div>
        );
      })}
    </div>
  );
}
