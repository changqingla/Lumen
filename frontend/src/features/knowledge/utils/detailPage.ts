export const knowledgeQuickPrompts = [
  '请总结一下这个知识库中的内容',
  '基于当前文档，给我一个学习路径',
  '列出本知识库中最值得优先阅读的文档',
  '请提炼关键术语并做简要解释',
];

export const getKnowledgeDetailErrorMessage = (error: unknown, fallback: string): string => {
  if (error instanceof Error && error.message.trim()) {
    return error.message;
  }
  return fallback;
};

export const formatKnowledgeRelativeTime = (dateString: string): string => {
  const date = new Date(dateString);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMins = Math.floor(diffMs / 60000);
  const diffHours = Math.floor(diffMs / 3600000);
  const diffDays = Math.floor(diffMs / 86400000);

  if (diffMins < 1) return '刚刚';
  if (diffMins < 60) return `${diffMins}分钟前`;
  if (diffHours < 24) return `${diffHours}小时前`;
  if (diffDays < 7) return `${diffDays}天前`;

  return date.toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' });
};
