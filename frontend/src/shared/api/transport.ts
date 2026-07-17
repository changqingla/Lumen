import { dispatchAuthSessionReset } from '../lib/auth-runtime.ts';
import { getGuestModeGuestToken, isGuestModeEnabled } from '../lib/guest-mode.ts';
import { safeLocalStorageRemove } from '../utils/localStorage.ts';

const API_BASE_URL = import.meta.env?.VITE_API_URL || '/api';
let authRedirectTimer: ReturnType<typeof setTimeout> | null = null;

interface ApiErrorDetails {
  code: string;
  message: string;
}

const asRecord = (value: unknown): Record<string, unknown> | null => (
  value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null
);

const readString = (value: unknown): string => (
  typeof value === 'string' ? value.trim() : ''
);

const parseResponseBody = (responseText: string, contentType = ''): unknown => {
  if (!responseText.trim()) {
    return {};
  }

  const looksLikeJson = (
    contentType.toLowerCase().includes('application/json')
    || /^[[{]/u.test(responseText.trim())
    || ['null', 'true', 'false'].includes(responseText.trim())
    || /^-?\d/u.test(responseText.trim())
  );
  if (!looksLikeJson) {
    return responseText;
  }

  try {
    return JSON.parse(responseText) as unknown;
  } catch {
    return responseText;
  }
};

const extractApiError = (data: unknown, status: number): ApiErrorDetails => {
  if (typeof data === 'string') {
    return {
      code: '',
      message: data.trim() || `请求失败 (${status})`,
    };
  }

  const root = asRecord(data);
  const detail = root?.detail;
  const detailRecord = asRecord(detail);
  const nestedError = asRecord(detailRecord?.error);
  const rootError = asRecord(root?.error);
  const candidate = nestedError || rootError || detailRecord || root;

  const message = (
    readString(candidate?.message)
    || readString(detailRecord?.error)
    || readString(detail)
    || readString(root?.error)
    || `请求失败 (${status})`
  );

  return {
    code: readString(candidate?.code),
    message,
  };
};

const parseValidationError = (data: unknown): string | null => {
  const detail = asRecord(data)?.detail;
  if (!Array.isArray(detail) || detail.length === 0) {
    return null;
  }

  const firstError = asRecord(detail[0]);
  const message = readString(firstError?.msg);
  if (!message) {
    return null;
  }

  const location = Array.isArray(firstError?.loc) ? firstError.loc : [];
  const field = location.length > 0 ? String(location[location.length - 1]) : '';
  return field ? `参数错误 (${field}): ${message}` : `参数错误: ${message}`;
};

const decodeAttachmentFileName = (value: string): string => {
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
};

const invalidateExpiredAuthSession = (): void => {
  safeLocalStorageRemove('auth_token');
  safeLocalStorageRemove('auth_user');
  safeLocalStorageRemove('userProfile');
  dispatchAuthSessionReset();

  if (authRedirectTimer !== null || typeof window === 'undefined') {
    return;
  }
  authRedirectTimer = setTimeout(() => {
    authRedirectTimer = null;
    if (!localStorage.getItem('auth_token')) {
      window.location.href = '/auth';
    }
  }, 1500);
};

const attachGuestHeaders = (headers: Headers, token: string | null): void => {
  if (token || !isGuestModeEnabled() || headers.has('X-Guest-Token')) {
    return;
  }

  const guestToken = getGuestModeGuestToken();
  if (guestToken) {
    headers.set('X-Guest-Token', guestToken);
  }
};

/** Send an authenticated API request while leaving response parsing to the caller. */
export async function fetchApi(
  endpoint: string,
  options: RequestInit = {},
): Promise<Response> {
  const token = localStorage.getItem('auth_token');
  const headers = new Headers(options.headers || undefined);

  if (!(options.body instanceof FormData) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  if (token && !headers.has('Authorization')) {
    headers.set('Authorization', `Bearer ${token}`);
  }
  attachGuestHeaders(headers, token);

  const response = await fetch(`${API_BASE_URL}${endpoint}`, {
    ...options,
    headers,
  });
  if (response.status === 401 && token && endpoint !== '/auth/login') {
    invalidateExpiredAuthSession();
  }
  return response;
}

export async function request<T>(
  endpoint: string,
  options: RequestInit = {},
): Promise<T> {
  const token = localStorage.getItem('auth_token');
  const response = await fetchApi(endpoint, options);
  const responseText = await response.text();
  const data = parseResponseBody(
    responseText,
    response.headers.get('content-type') || '',
  );

  if (!response.ok) {
    if (response.status === 422) {
      const validationError = parseValidationError(data);
      if (validationError) {
        throw new Error(validationError);
      }
    }

    const error = extractApiError(data, response.status);

    if (error.code === 'UNAUTHORIZED') {
      const isTokenExpired = token && endpoint !== '/auth/login';
      if (isTokenExpired) {
        invalidateExpiredAuthSession();
        throw new Error('当前登录已过期，请重新登录');
      }
      throw new Error(error.message || '账号或密码不正确');
    }
    if (error.code === 'NOT_FOUND') {
      throw new Error(error.message || '账号未注册');
    }
    if (error.code === 'CONFLICT') {
      throw new Error(error.message || '该邮箱已被注册');
    }
    throw new Error(error.message);
  }

  return data as T;
}

export async function requestText(
  endpoint: string,
  options: RequestInit = {},
): Promise<string> {
  const response = await fetchApi(endpoint, options);
  const responseText = await response.text();

  if (!response.ok) {
    const data = parseResponseBody(
      responseText,
      response.headers.get('content-type') || '',
    );
    throw new Error(extractApiError(data, response.status).message);
  }

  return responseText;
}

export async function requestBlob(
  endpoint: string,
  options: RequestInit = {},
): Promise<{ blob: Blob; fileName?: string | null }> {
  const response = await fetchApi(endpoint, options);

  if (!response.ok) {
    const responseText = await response.text();
    const data = parseResponseBody(
      responseText,
      response.headers.get('content-type') || '',
    );
    throw new Error(extractApiError(data, response.status).message);
  }

  const contentDisposition = response.headers.get('content-disposition') || '';
  const utf8Match = contentDisposition.match(/filename\*=UTF-8''([^;]+)/i);
  const plainMatch = contentDisposition.match(/filename="?([^";]+)"?/i);
  const fileName = utf8Match?.[1]
    ? decodeAttachmentFileName(utf8Match[1])
    : (plainMatch?.[1] || null);

  return {
    blob: await response.blob(),
    fileName,
  };
}
