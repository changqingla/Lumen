import type { ChatArtifact } from '@/shared/api/client';

export type ArtifactPreviewType = 'docx' | 'txt' | 'md' | 'html' | 'unsupported';

export interface ChatArtifactPreviewTarget {
  sessionId: string;
  objectPath: string;
  fileName: string;
  url: string;
  previewType: ArtifactPreviewType;
}

export const resolveArtifactName = (artifact: ChatArtifact): string => {
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

export const getArtifactPreviewType = (fileName: string): ArtifactPreviewType => {
  const extension = fileName.toLowerCase().split('.').pop() || '';
  if (extension === 'docx') {
    return 'docx';
  }
  if (extension === 'txt') {
    return 'txt';
  }
  if (extension === 'md' || extension === 'markdown') {
    return 'md';
  }
  if (extension === 'html' || extension === 'htm') {
    return 'html';
  }
  return 'unsupported';
};

export const isArtifactPreviewable = (fileName: string): boolean => (
  getArtifactPreviewType(fileName) !== 'unsupported'
);
