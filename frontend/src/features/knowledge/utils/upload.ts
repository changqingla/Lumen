import { kbAPI } from '@/shared/api/client';

const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

const completeKnowledgeDirectUploadWithRetry = async (
  kbId: string,
  docId: string,
  retries = 2,
) => {
  let lastError: unknown;
  for (let attempt = 0; attempt <= retries; attempt += 1) {
    try {
      return await kbAPI.completeDirectUpload(kbId, docId);
    } catch (error) {
      lastError = error;
      if (attempt === retries) {
        break;
      }
      await wait(600 * (attempt + 1));
    }
  }
  throw lastError instanceof Error ? lastError : new Error('触发解析失败');
};

const uploadKnowledgeDocumentWithDirectFlow = async (
  kbId: string,
  file: File,
  onProgress?: (progress: number) => void,
) => {
  const init = await kbAPI.initDirectUpload(kbId, file);
  await kbAPI.uploadToPresignedUrl(init.uploadUrl, file, {
    retries: 2,
    retryDelayMs: 800,
    onProgress,
  });
  onProgress?.(100);
  return completeKnowledgeDirectUploadWithRetry(kbId, init.id, 2);
};

export const uploadKnowledgeDocuments = async (
  kbId: string,
  files: File[],
  options?: {
    onFileStart?: (file: File) => void;
    onProgress?: (progress: number) => void;
  },
) => {
  for (const file of files) {
    options?.onFileStart?.(file);
    options?.onProgress?.(0);
    await uploadKnowledgeDocumentWithDirectFlow(kbId, file, options?.onProgress);
  }
};
