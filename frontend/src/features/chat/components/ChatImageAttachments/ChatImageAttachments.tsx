import { X } from 'lucide-react';
import type { ChatImage } from '@/features/chat/hooks/useChatImageUpload';

interface ChatImagePreviewListClassNames {
  list: string;
  item: string;
  image: string;
  removeButton: string;
}

interface ChatImagePreviewListProps {
  images: ChatImage[];
  onRemoveImage: (id: string) => void;
  classNames: ChatImagePreviewListClassNames;
}

export function ChatImagePreviewList({ images, onRemoveImage, classNames }: ChatImagePreviewListProps) {
  if (!images.length) return null;

  return (
    <div className={classNames.list}>
      {images.map((image) => (
        <div key={image.id} className={classNames.item}>
          <img src={image.dataUrl} alt={image.name} className={classNames.image} />
          <button
            type="button"
            className={classNames.removeButton}
            onClick={() => onRemoveImage(image.id)}
            title="移除图片"
          >
            <X size={14} />
          </button>
        </div>
      ))}
    </div>
  );
}
