export const ALLOWED_AVATAR_TYPES = ['image/jpeg', 'image/jpg', 'image/png', 'image/webp'] as const;
export const AVATAR_ACCEPT = '.jpg,.jpeg,.png,.webp,image/jpeg,image/png,image/webp';
export const MAX_AVATAR_SIZE_BYTES = 10 * 1024 * 1024;
export const MAX_AVATAR_SIZE_LABEL = '10MB';
export const AVATAR_FILE_TYPE_ERROR = '请选择 JPG、PNG 或 WEBP 图片';
export const AVATAR_FILE_SIZE_ERROR = `图片大小不能超过 ${MAX_AVATAR_SIZE_LABEL}`;

export const isAllowedAvatarType = (contentType: string): boolean => (
  ALLOWED_AVATAR_TYPES.includes(contentType as (typeof ALLOWED_AVATAR_TYPES)[number])
);

export const isAllowedAvatarSize = (size: number): boolean => (
  size <= MAX_AVATAR_SIZE_BYTES
);
