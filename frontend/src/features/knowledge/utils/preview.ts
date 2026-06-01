import { kbAPI } from '@/shared/api/client';

export interface KnowledgeDocumentPreviewPayload {
  url: string;
  markdownContent: string;
}

export const loadKnowledgeDocumentPreview = async (
  kbId: string,
  docId: string,
  fileName: string,
): Promise<KnowledgeDocumentPreviewPayload> => {
  const urlResponse = await kbAPI.getDocumentUrl(kbId, docId);
  const extension = fileName.toLowerCase().split('.').pop();

  if (extension !== 'doc' && extension !== 'md' && extension !== 'markdown') {
    return {
      url: urlResponse.url,
      markdownContent: '',
    };
  }

  try {
    const markdownResponse = await kbAPI.getDocumentMarkdown(kbId, docId);
    return {
      url: extension === 'doc' ? urlResponse.url : '',
      markdownContent: markdownResponse.content,
    };
  } catch (error) {
    console.warn('Failed to get markdown content for document preview:', error);
    return {
      url: urlResponse.url,
      markdownContent: '',
    };
  }
};
