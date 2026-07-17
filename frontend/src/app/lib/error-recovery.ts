export type ApplicationErrorKind = 'chunk-load' | 'application';

export interface ApplicationErrorCopy {
  title: string;
  message: string;
}

const CHUNK_LOAD_ERROR_PATTERN = /(?:chunkloaderror|loading chunk|dynamically imported module|failed to fetch module)/iu;

export const classifyApplicationError = (error: unknown): ApplicationErrorKind => {
  if (error instanceof Error && CHUNK_LOAD_ERROR_PATTERN.test(`${error.name} ${error.message}`)) {
    return 'chunk-load';
  }
  return 'application';
};

export const getApplicationErrorCopy = (error: unknown): ApplicationErrorCopy => (
  classifyApplicationError(error) === 'chunk-load'
    ? {
        title: '页面资源需要重新加载',
        message: '应用可能已更新，请刷新页面后继续。',
      }
    : {
        title: '页面暂时无法显示',
        message: '当前页面遇到异常。你可以重试，或返回首页继续使用。',
      }
);

export const shouldResetApplicationError = (
  previousResetKey: string,
  nextResetKey: string,
): boolean => previousResetKey !== nextResetKey;
