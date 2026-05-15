/**
 * UUID 生成工具
 */

/**
 * 生成 UUID v4
 */
export function generateUUID(): string {
  if (typeof crypto === 'undefined' || typeof crypto.randomUUID !== 'function') {
    throw new Error('crypto.randomUUID is required to generate UUIDs');
  }

  return crypto.randomUUID();
}
