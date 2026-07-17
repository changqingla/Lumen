import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

import type {
  authAPI,
  noteAPI,
  organizationAPI,
  OrganizationMember,
} from '../src/shared/api/client.ts';

type Equal<Left, Right> = (
  (<Value>() => Value extends Left ? 1 : 2) extends
  (<Value>() => Value extends Right ? 1 : 2)
    ? true
    : false
);
type Expect<Value extends true> = Value;

type CurrentUser = Awaited<ReturnType<typeof authAPI.getMe>>;
type UpdatedUser = Awaited<ReturnType<typeof authAPI.updateProfile>>;
type MembersResponse = Awaited<ReturnType<typeof organizationAPI.getMembers>>;
type ListedNote = Awaited<ReturnType<typeof noteAPI.listNotes>>['items'][number];
type HasRequiredFolderId = {} extends Pick<ListedNote, 'folderId'> ? false : true;

const contractAssertions: [
  Expect<Equal<CurrentUser['member_expires_at'], string | null>>,
  Expect<Equal<UpdatedUser['member_expires_at'], string | null>>,
  Expect<Equal<'membership_expires_at' extends keyof CurrentUser ? true : false, false>>,
  Expect<Equal<MembersResponse, OrganizationMember[]>>,
  Expect<Equal<HasRequiredFolderId, true>>,
] = [true, true, true, true, true];

test('backend-facing API client types retain the agreed response shapes', () => {
  assert.deepEqual(contractAssertions, [true, true, true, true, true]);
});

test('the production frontend defaults to the same-origin API', async () => {
  const productionEnv = await readFile(
    new URL('../.env.production', import.meta.url),
    'utf8',
  );
  const apiSetting = productionEnv
    .split(/\r?\n/u)
    .find((line) => line.startsWith('VITE_API_URL='));

  assert.equal(apiSetting, 'VITE_API_URL=/api');
  assert.doesNotMatch(productionEnv, /ireader\.online/u);
});
