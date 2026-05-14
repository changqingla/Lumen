import React, { useRef, useState } from 'react';
import { Camera } from 'lucide-react';
import styles from './AvatarUpload.module.css';
import defaultAvatar from '@/assets/default-avatar.svg';

const ALLOWED_AVATAR_TYPES = ['image/jpeg', 'image/jpg', 'image/png', 'image/webp'];
const AVATAR_ACCEPT = '.jpg,.jpeg,.png,.webp,image/jpeg,image/png,image/webp';

interface AvatarUploadProps {
  currentAvatar: string | null;
  onUpload: (file: File) => Promise<void>;
  size?: number;
  showTips?: boolean;
}

export default function AvatarUpload({ 
  currentAvatar, 
  onUpload, 
  size = 100,
  showTips = true 
}: AvatarUploadProps) {
  const [isUploading, setIsUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleClick = () => {
    fileInputRef.current?.click();
  };

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    // Validate file type
    if (!ALLOWED_AVATAR_TYPES.includes(file.type)) {
      alert('请上传 JPG、PNG 或 WEBP 图片');
      return;
    }

    // Validate file size (e.g., 5MB)
    if (file.size > 5 * 1024 * 1024) {
      alert('图片大小不能超过 5MB');
      return;
    }

    try {
      setIsUploading(true);
      await onUpload(file);
    } catch (error) {
      console.error('Upload failed:', error);
    } finally {
      setIsUploading(false);
      // Reset input
      if (fileInputRef.current) {
        fileInputRef.current.value = '';
      }
    }
  };

  return (
    <div className={styles.uploadContainer}>
      <div 
        className={`${styles.uploadArea} ${size > 100 ? styles.large : styles.small}`}
        onClick={handleClick}
        style={{ width: size, height: size }}
      >
        <img src={currentAvatar || defaultAvatar} alt="Avatar" className={styles.avatar} />
        
        {showTips && (
          <div className={styles.overlay}>
            <div className={styles.iconWrapper}>
              <Camera size={20} strokeWidth={2.5} />
            </div>
          </div>
        )}

        {isUploading && (
          <div className={styles.loading}>
            <div className={styles.spinner} />
          </div>
        )}
      </div>
      
      <input
        ref={fileInputRef}
        type="file"
        accept={AVATAR_ACCEPT}
        onChange={handleFileChange}
        className={styles.hiddenInput}
      />
    </div>
  );
}
