/**
 * ✅ 安全的 localStorage 操作工具函数
 * 统一处理隐私模式、存储空间不足等异常情况
 */

/**
 * 安全地移除 localStorage 项
 */
export const safeLocalStorageRemove = (key: string): boolean => {
  try {
    localStorage.removeItem(key);
    return true;
  } catch (error) {
    console.error(`Failed to remove localStorage item "${key}":`, error);
    return false;
  }
};
