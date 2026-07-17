import type { ChatArtifact } from '@/shared/api/client';

export type ArtifactPreviewType = 'docx' | 'txt' | 'md' | 'html' | 'pptx' | 'unsupported';

export interface ChatArtifactPreviewTarget {
  sessionId: string;
  objectPath: string;
  fileName: string;
  url: string;
  previewType: ArtifactPreviewType;
}

export const HTML_ARTIFACT_PREVIEW_MAX_BYTES = 5 * 1024 * 1024;

const HTML_ARTIFACT_CSP = [
  "default-src 'none'",
  "base-uri 'none'",
  "connect-src 'none'",
  "font-src data:",
  "form-action 'none'",
  "frame-src 'none'",
  "img-src data: blob:",
  "media-src data: blob:",
  "navigate-to 'none'",
  "object-src 'none'",
  "script-src 'none'",
  "style-src 'unsafe-inline'",
].join('; ');

const META_REFRESH_PATTERN = /<meta\b(?=[^>]*\bhttp-equiv\s*=\s*(?:["']?refresh["']?))[^>]*>/gi;

export const hardenHtmlArtifactPreview = (html: string): string => {
  const withoutRefresh = html.replace(META_REFRESH_PATTERN, '');
  const policy = `<meta http-equiv="Content-Security-Policy" content="${HTML_ARTIFACT_CSP}">`;
  return `${policy}${withoutRefresh}`;
};

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
  if (extension === 'pptx') {
    return 'pptx';
  }
  return 'unsupported';
};

export const isArtifactPreviewable = (fileName: string): boolean => (
  getArtifactPreviewType(fileName) !== 'unsupported'
);
