import lumenIconUrl from '@/assets/lumen.svg';

const DEFAULT_KNOWLEDGE_BASE_AVATAR = lumenIconUrl;

type KnowledgeBaseAvatarSource = {
  avatar?: string;
};

/**
 * 是否使用了用户自定义知识库头像
 * @param kb 知识库对象
 * @returns true 表示使用自定义头像
 */
export function hasCustomKnowledgeBaseAvatar(kb: KnowledgeBaseAvatarSource): boolean {
  return Boolean(kb.avatar && kb.avatar !== '/kb.png' && !kb.avatar.includes('/kb.png'));
}

/**
 * 获取知识库的显示头像
 * 优先使用自定义头像，否则回退到统一默认头像
 * @param kb 知识库对象
 * @returns 头像URL路径
 */
export function getKnowledgeBaseAvatar(kb: KnowledgeBaseAvatarSource): string {
  if (hasCustomKnowledgeBaseAvatar(kb)) {
    return kb.avatar;
  }

  return DEFAULT_KNOWLEDGE_BASE_AVATAR;
}
