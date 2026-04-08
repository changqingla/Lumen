import { useCallback, useRef, useState } from 'react';

export interface ChatImage {
  id: string;
  name: string;
  dataUrl: string;
}

const ALLOWED_IMAGE_TYPES = ['image/jpeg', 'image/png', 'image/webp'];
const ALLOWED_IMAGE_EXTENSIONS = ['.jpg', '.jpeg', '.png', '.webp'];
const DEFAULT_MAX_CHAT_IMAGES = 4;
const DEFAULT_MAX_CHAT_IMAGE_SIZE = 5 * 1024 * 1024;

const getFileExtension = (file: File) => `.${file.name.split('.').pop()?.toLowerCase() || ''}`;

interface UseChatImageUploadOptions {
  isStreaming: boolean;
  onError: (message: string) => void;
  maxImages?: number;
  maxImageSize?: number;
}

export function useChatImageUpload(options: UseChatImageUploadOptions) {
  const {
    isStreaming,
    onError,
    maxImages = DEFAULT_MAX_CHAT_IMAGES,
    maxImageSize = DEFAULT_MAX_CHAT_IMAGE_SIZE,
  } = options;

  const [chatImages, setChatImages] = useState<ChatImage[]>([]);
  const [isImageDragOver, setIsImageDragOver] = useState(false);
  const imageInputRef = useRef<HTMLInputElement>(null);

  const readImageAsDataUrl = (file: File): Promise<string> =>
    new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result || ''));
      reader.onerror = () => reject(new Error(`读取图片失败: ${file.name}`));
      reader.readAsDataURL(file);
    });

  const appendChatImages = useCallback(async (files: File[]) => {
    if (!files.length) return;

    if (chatImages.length + files.length > maxImages) {
      onError(`最多上传 ${maxImages} 张图片`);
      return;
    }

    const accepted: File[] = [];
    for (const file of files) {
      const matchesMimeType = ALLOWED_IMAGE_TYPES.includes(file.type);
      const matchesExtension = ALLOWED_IMAGE_EXTENSIONS.includes(getFileExtension(file));
      if (!matchesMimeType && !matchesExtension) {
        onError(`仅支持 JPG/JPEG/PNG/WEBP: ${file.name}`);
        continue;
      }
      if (file.size > maxImageSize) {
        onError(`图片过大（>${Math.floor(maxImageSize / 1024 / 1024)}MB）: ${file.name}`);
        continue;
      }
      accepted.push(file);
    }

    if (!accepted.length) return;

    const nextImages: ChatImage[] = [];
    for (const file of accepted) {
      const dataUrl = await readImageAsDataUrl(file);
      nextImages.push({
        id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
        name: file.name,
        dataUrl,
      });
    }
    setChatImages((prev) => [...prev, ...nextImages]);
  }, [chatImages.length, maxImageSize, maxImages, onError]);

  const handleImageUploadClick = useCallback(() => {
    if (imageInputRef.current) {
      imageInputRef.current.click();
    }
  }, []);

  const handleImageSelect = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || []);
    e.target.value = '';
    await appendChatImages(files);
  };

  const handleRemoveChatImage = useCallback((id: string) => {
    setChatImages((prev) => prev.filter((item) => item.id !== id));
  }, []);

  const handleImageDragOver = useCallback((e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setIsImageDragOver(true);
  }, []);

  const handleImageDragLeave = useCallback((e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setIsImageDragOver(false);
  }, []);

  const handleImageDrop = async (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setIsImageDragOver(false);
    if (isStreaming) return;
    const files = Array.from(e.dataTransfer.files || []);
    await appendChatImages(files);
  };

  const clearChatImages = useCallback(() => {
    setChatImages([]);
  }, []);

  return {
    chatImages,
    isImageDragOver,
    imageInputRef,
    maxChatImages: maxImages,
    handleImageUploadClick,
    handleImageSelect,
    handleRemoveChatImage,
    handleImageDragOver,
    handleImageDragLeave,
    handleImageDrop,
    appendChatImages,
    clearChatImages,
  };
}
