import assert from 'node:assert/strict';
import test, { afterEach, beforeEach } from 'node:test';

const storage = new Map<string, string>();
const localStorageStub = {
  getItem(key: string): string | null {
    return storage.get(key) ?? null;
  },
  setItem(key: string, value: string): void {
    storage.set(key, value);
  },
  removeItem(key: string): void {
    storage.delete(key);
  },
  clear(): void {
    storage.clear();
  },
  key(index: number): string | null {
    return [...storage.keys()][index] ?? null;
  },
  get length(): number {
    return storage.size;
  },
};

const windowStub = new EventTarget() as EventTarget & {
  localStorage: typeof localStorageStub;
  location: { href: string };
};
windowStub.localStorage = localStorageStub;
windowStub.location = { href: '/' };

Object.defineProperty(globalThis, 'window', {
  value: windowStub,
  configurable: true,
});
Object.defineProperty(globalThis, 'localStorage', {
  value: localStorageStub,
  configurable: true,
});

const originalFetch = globalThis.fetch;
const originalSetTimeout = globalThis.setTimeout;
const {
  fetchApi,
  request,
  requestBlob,
  requestText,
} = await import('../src/shared/api/transport.ts');
const {
  disableGuestMode,
  enableGuestMode,
  getGuestModeState,
  markGuestMessageUsed,
} = await import('../src/shared/lib/guest-mode.ts');

beforeEach(() => {
  storage.clear();
  windowStub.location.href = '/';
});

afterEach(() => {
  globalThis.fetch = originalFetch;
  globalThis.setTimeout = originalSetTimeout;
});

test('JSON requests attach the access token and default content type', async () => {
  storage.set('auth_token', 'access-token');
  let capturedUrl = '';
  let capturedHeaders = new Headers();
  globalThis.fetch = async (input, init) => {
    capturedUrl = String(input);
    capturedHeaders = new Headers(init?.headers);
    return new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    });
  };

  const result = await request<{ ok: boolean }>('/status', {
    method: 'POST',
    body: JSON.stringify({ check: true }),
  });

  assert.deepEqual(result, { ok: true });
  assert.equal(capturedUrl, '/api/status');
  assert.equal(capturedHeaders.get('authorization'), 'Bearer access-token');
  assert.equal(capturedHeaders.get('content-type'), 'application/json');
  assert.equal(capturedHeaders.has('x-guest-token'), false);
});

test('guest requests use the signed guest token and leave multipart boundaries to fetch', async () => {
  storage.set('lumen_guest_mode', JSON.stringify({
    enabled: true,
    usedMessageCount: 0,
    guestToken: 'signed-guest-token',
  }));
  let capturedHeaders = new Headers();
  globalThis.fetch = async (_input, init) => {
    capturedHeaders = new Headers(init?.headers);
    return new Response(null, { status: 204 });
  };

  const body = new FormData();
  body.append('file', new Blob(['contents']), 'notes.txt');
  await fetchApi('/uploads', { method: 'POST', body });

  assert.equal(capturedHeaders.get('x-guest-token'), 'signed-guest-token');
  assert.equal(capturedHeaders.has('authorization'), false);
  assert.equal(capturedHeaders.has('content-type'), false);
});

test('a late guest completion cannot re-enable guest mode after login', () => {
  enableGuestMode('signed-guest-token');
  markGuestMessageUsed();
  assert.equal(getGuestModeState().usedMessageCount, 1);

  disableGuestMode();
  markGuestMessageUsed();

  assert.deepEqual(getGuestModeState(), {
    enabled: false,
    usedMessageCount: 1,
    guestToken: 'signed-guest-token',
  });
});

test('authenticated 401 responses invalidate every transport consumer', async () => {
  storage.set('auth_token', 'expired-access-token');
  storage.set('auth_user', 'cached-user');
  storage.set('userProfile', 'cached-profile');
  let redirectCallback: (() => void) | null = null;
  globalThis.setTimeout = ((callback: (...args: unknown[]) => void) => {
    redirectCallback = () => callback();
    return 1;
  }) as typeof setTimeout;
  globalThis.fetch = async () => new Response(JSON.stringify({
    detail: {
      error: {
        code: 'UNAUTHORIZED',
        message: 'User not found',
      },
    },
  }), {
    status: 401,
    headers: { 'content-type': 'application/json' },
  });

  const response = await fetchApi('/protected-download');

  assert.equal(response.status, 401);
  assert.equal(storage.has('auth_token'), false);
  assert.equal(storage.has('auth_user'), false);
  assert.equal(storage.has('userProfile'), false);
  assert.ok(redirectCallback);
  redirectCallback();
  assert.equal(windowStub.location.href, '/auth');
});

test('structured API failures expose the backend message', async () => {
  globalThis.fetch = async () => new Response(JSON.stringify({
    detail: {
      error: {
        code: 'BAD_REQUEST',
        message: 'invalid runtime request',
      },
    },
  }), {
    status: 400,
    headers: { 'content-type': 'application/json' },
  });

  await assert.rejects(
    request('/runtime', { method: 'POST' }),
    /invalid runtime request/,
  );
});

test('malformed or empty structured failures keep a stable fallback error', async () => {
  globalThis.fetch = async () => new Response('null', {
    status: 500,
    headers: { 'content-type': 'application/json' },
  });

  await assert.rejects(
    request('/runtime', { method: 'POST' }),
    /请求失败 \(500\)/,
  );

  globalThis.fetch = async () => new Response(JSON.stringify({ detail: [] }), {
    status: 422,
    headers: { 'content-type': 'application/json' },
  });

  await assert.rejects(
    request('/runtime', { method: 'POST' }),
    /请求失败 \(422\)/,
  );
});

test('text requests unwrap FastAPI detail messages without stringifying objects', async () => {
  globalThis.fetch = async () => new Response(JSON.stringify({
    detail: {
      error: {
        code: 'NOT_READY',
        message: 'artifact is still being generated',
      },
    },
  }), {
    status: 409,
    headers: { 'content-type': 'application/json' },
  });

  await assert.rejects(
    requestText('/artifact'),
    /artifact is still being generated/,
  );
});

test('blob responses decode UTF-8 filenames from content-disposition', async () => {
  globalThis.fetch = async () => new Response('report', {
    status: 200,
    headers: {
      'content-disposition': "attachment; filename*=UTF-8''%E6%8A%A5%E5%91%8A.md",
    },
  });

  const result = await requestBlob('/artifact');

  assert.equal(result.fileName, '报告.md');
  assert.equal(await result.blob.text(), 'report');
});

test('blob responses tolerate malformed percent escapes in filenames', async () => {
  globalThis.fetch = async () => new Response('report', {
    status: 200,
    headers: {
      'content-disposition': "attachment; filename*=UTF-8''report%ZZ.md",
    },
  });

  const result = await requestBlob('/artifact');

  assert.equal(result.fileName, 'report%ZZ.md');
  assert.equal(await result.blob.text(), 'report');
});
