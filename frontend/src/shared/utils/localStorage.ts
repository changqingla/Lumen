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

/**
 * 安全地读取 localStorage 项
 */
export const safeLocalStorageGet = (key: string): string | null => {
  try {
    return localStorage.getItem(key);
  } catch (error) {
    console.error(`Failed to read localStorage item "${key}":`, error);
    return null;
  }
};

/**
 * 安全地写入 localStorage 项
 */
export const safeLocalStorageSet = (key: string, value: string): boolean => {
  try {
    localStorage.setItem(key, value);
    return true;
  } catch (error) {
    console.error(`Failed to write localStorage item "${key}":`, error);
    return false;
  }
};
