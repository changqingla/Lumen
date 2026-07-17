import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

import {
  classifyApplicationError,
  getApplicationErrorCopy,
  shouldResetApplicationError,
} from '../src/app/lib/error-recovery.ts';
import { canAccessProtectedRoute } from '../src/app/lib/route-access.ts';
import {
  buildLegacyChatRedirect,
  resolveChatSessionForRoute,
} from '../src/features/chat/lib/chat-route.ts';

test('protected routes are bearer-only unless guest access is explicit', () => {
  assert.equal(canAccessProtectedRoute({
    authToken: '',
    isGuestMode: true,
  }), false);
  assert.equal(canAccessProtectedRoute({
    authToken: '',
    isGuestMode: true,
    allowGuest: true,
  }), true);
  assert.equal(canAccessProtectedRoute({
    authToken: '  bearer-token  ',
    isGuestMode: false,
  }), true);
  assert.equal(canAccessProtectedRoute({
    authToken: '   ',
    isGuestMode: false,
    allowGuest: true,
  }), false);
});

test('only the home route opts into guest access and unknown routes have a catch-all', async () => {
  const appSource = await readFile(
    new URL('../src/app/App.tsx', import.meta.url),
    'utf8',
  );

  assert.equal(appSource.match(/<ProtectedRoute allowGuest>/gu)?.length, 1);
  assert.match(appSource, /path="\/" element=\{<ProtectedRoute allowGuest>/u);
  assert.match(appSource, /<Route path="\*" element=\{<NotFoundPage \/>\} \/>/u);
});

test('legacy chat links preserve the session ID in the canonical query route', () => {
  assert.equal(buildLegacyChatRedirect(undefined), '/');
  assert.equal(buildLegacyChatRedirect('  session-1  '), '/?chatId=session-1');
  assert.equal(
    buildLegacyChatRedirect('session/with space'),
    '/?chatId=session%2Fwith+space',
  );
});

test('chat route restoration fetches only sessions missing from the loaded page', async () => {
  const loadedSession = { id: 'loaded', title: 'Loaded session' };
  const requestedIds: string[] = [];
  const loadSession = async (sessionId: string) => {
    requestedIds.push(sessionId);
    return { id: sessionId, title: 'Fetched session' };
  };

  assert.equal(
    await resolveChatSessionForRoute('loaded', [loadedSession], loadSession),
    loadedSession,
  );
  assert.deepEqual(requestedIds, []);

  assert.deepEqual(
    await resolveChatSessionForRoute('  outside-first-page  ', [loadedSession], loadSession),
    { id: 'outside-first-page', title: 'Fetched session' },
  );
  assert.deepEqual(requestedIds, ['outside-first-page']);

  assert.equal(await resolveChatSessionForRoute('', [loadedSession], loadSession), null);
  assert.deepEqual(requestedIds, ['outside-first-page']);
});

test('application errors use stable recovery copy and reset after navigation', () => {
  const sensitiveError = new Error('database token=secret-value');
  const genericCopy = getApplicationErrorCopy(sensitiveError);

  assert.equal(classifyApplicationError(sensitiveError), 'application');
  assert.doesNotMatch(`${genericCopy.title} ${genericCopy.message}`, /secret-value/u);
  assert.equal(
    classifyApplicationError(new TypeError('Failed to fetch dynamically imported module')),
    'chunk-load',
  );
  assert.equal(shouldResetApplicationError('route-a', 'route-b'), true);
  assert.equal(shouldResetApplicationError('route-a', 'route-a'), false);
  assert.equal(classifyApplicationError(null), 'application');
});
