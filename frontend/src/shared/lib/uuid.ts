/**
 * UUID 生成工具
 * 兼容不支持 crypto.randomUUID 的浏览器
 */

/**
 * 生成 UUID v4
 * 优先使用 crypto.randomUUID，fallback 到自定义实现
 */
export function generateUUID(): string {
  // 尝试使用原生 API
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    try {
      return crypto.randomUUID();
    } catch {
      // fallback to custom implementation
    }
  }

  // Fallback: 自定义实现
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
    const r = Math.random() * 16 | 0;
    const v = c === 'x' ? r : (r & 0x3 | 0x8);
    return v.toString(16);
  });
}
