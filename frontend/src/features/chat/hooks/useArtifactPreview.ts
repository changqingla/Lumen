import { useCallback, useEffect, useRef, useState } from 'react';

import { api, type ChatArtifact } from '@/shared/api/client';
import {
  hardenHtmlArtifactPreview,
  HTML_ARTIFACT_PREVIEW_MAX_BYTES,
  getArtifactPreviewType,
  resolveArtifactName,
  type ChatArtifactPreviewTarget,
} from '@/features/chat/lib/artifact-preview';

interface UseArtifactPreviewOptions {
  sessionId?: string | null;
  onError: (message: string) => void;
}

const revokePreviewUrl = (preview: ChatArtifactPreviewTarget | null) => {
  if (preview?.url.startsWith('blob:')) {
    URL.revokeObjectURL(preview.url);
  }
};

export function useArtifactPreview({
  sessionId,
  onError,
}: UseArtifactPreviewOptions) {
  const [preview, setPreview] = useState<ChatArtifactPreviewTarget | null>(null);
  const previewRef = useRef<ChatArtifactPreviewTarget | null>(null);
  const requestSequenceRef = useRef(0);
  const requestAbortRef = useRef<AbortController | null>(null);

  const replacePreview = useCallback((next: ChatArtifactPreviewTarget | null) => {
    revokePreviewUrl(previewRef.current);
    previewRef.current = next;
    setPreview(next);
  }, []);

  const closePreview = useCallback(() => {
    requestSequenceRef.current += 1;
    requestAbortRef.current?.abort();
    requestAbortRef.current = null;
    replacePreview(null);
  }, [replacePreview]);

  useEffect(() => {
    requestSequenceRef.current += 1;
    requestAbortRef.current?.abort();
    requestAbortRef.current = null;
    const current = previewRef.current;
    if (current && current.sessionId !== sessionId) {
      replacePreview(null);
    }
  }, [replacePreview, sessionId]);

  useEffect(() => () => {
    requestSequenceRef.current += 1;
    requestAbortRef.current?.abort();
    requestAbortRef.current = null;
    revokePreviewUrl(previewRef.current);
    previewRef.current = null;
  }, []);

  const openPreview = useCallback(async (artifact: ChatArtifact) => {
    const objectPath = (artifact.object_path || '').trim();
    if (!objectPath) {
      onError('文件路径无效，无法预览');
      return;
    }

    const targetSessionId = (artifact.session_id || sessionId || '').trim();
    if (!targetSessionId) {
      onError('当前会话不存在，无法预览文件');
      return;
    }

    requestSequenceRef.current += 1;
    const requestSequence = requestSequenceRef.current;
    requestAbortRef.current?.abort();
    const controller = new AbortController();
    requestAbortRef.current = controller;

    try {
      const fileName = resolveArtifactName(artifact);
      const { blob, fileName: responseFileName } = await api.downloadSessionArtifact(
        targetSessionId,
        objectPath,
        { signal: controller.signal },
      );
      if (controller.signal.aborted || requestSequenceRef.current !== requestSequence) {
        return;
      }
      const resolvedFileName = responseFileName || fileName;
      const previewType = getArtifactPreviewType(resolvedFileName);
      let previewBlob = blob;
      if (previewType === 'html') {
        if (blob.size > HTML_ARTIFACT_PREVIEW_MAX_BYTES) {
          throw new Error('HTML 文件过大，无法安全预览，请下载后查看');
        }
        const html = await blob.text();
        if (controller.signal.aborted || requestSequenceRef.current !== requestSequence) {
          return;
        }
        previewBlob = new Blob(
          [hardenHtmlArtifactPreview(html)],
          { type: 'text/html;charset=utf-8' },
        );
      }
      const previewUrl = URL.createObjectURL(previewBlob);
      if (controller.signal.aborted || requestSequenceRef.current !== requestSequence) {
        URL.revokeObjectURL(previewUrl);
        return;
      }
      replacePreview({
        sessionId: targetSessionId,
        objectPath,
        fileName: resolvedFileName,
        url: previewUrl,
        previewType,
      });
    } catch (error) {
      if (controller.signal.aborted || requestSequenceRef.current !== requestSequence) {
        return;
      }
      console.error('Failed to preview artifact:', error);
      onError(error instanceof Error ? error.message : '打开预览失败，请稍后重试');
    } finally {
      if (requestAbortRef.current === controller) {
        requestAbortRef.current = null;
      }
    }
  }, [onError, replacePreview, sessionId]);

  return {
    preview,
    openPreview,
    closePreview,
  };
}
