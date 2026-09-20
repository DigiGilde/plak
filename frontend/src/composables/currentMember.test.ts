import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { MOCK_CONTENT_BASE } from '../api/mock';
import {
  _resetCurrentMemberCache,
  currentMemberState,
  fetchCurrentMember,
  fetchSession,
  isPlatformAdmin,
} from './currentMember';

beforeEach(() => {
  _resetCurrentMemberCache();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

const MEMBER = {
  id: 'lid-1',
  ssoSubject: 'sub-1',
  email: 'lid@voorbeeld.nl',
  name: 'Lid',
  platformRole: 'member',
  status: 'active',
  createdAt: '2026-01-01T00:00:00Z',
  lastLoginAt: null,
  contentBaseUrl: MOCK_CONTENT_BASE,
};

function response(status: number, body: object, type = 'application/problem+json'): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': type } });
}

describe('currentMember: states', () => {
  it('reads the member from a successful /me', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(response(200, MEMBER, 'application/json'))));

    expect(await fetchSession()).toMatchObject({ state: 'active', reason: '' });
    expect(currentMemberState().member.value?.email).toBe('lid@voorbeeld.nl');
    expect(currentMemberState().loaded.value).toBe(true);
  });

  it('keeps a 403 without explanation at an empty reason', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(response(403, { title: 'Geen toegang', status: 403 }))),
    );

    expect(await fetchSession()).toEqual({
      state: 'awaiting-activation',
      member: null,
      reason: '',
    });
  });

  it('rethrows an error that says nothing about the session', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.reject(new TypeError('netwerk weg'))));

    await expect(fetchSession()).rejects.toThrow('netwerk weg');
    // Even then the fetch has happened: the UI need not keep waiting.
    expect(currentMemberState().loaded.value).toBe(true);
  });
});

describe('currentMember: cache', () => {
  it('fetches /me only once, even with multiple calls', async () => {
    const fetchMock = vi.fn(() => Promise.resolve(response(200, MEMBER, 'application/json')));
    vi.stubGlobal('fetch', fetchMock);

    await Promise.all([fetchCurrentMember(), fetchCurrentMember(), fetchSession()]);

    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('fetches again on refresh, so a reactivated account comes through', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(response(403, { status: 403, detail: 'Toegang ingetrokken.' }))
      .mockResolvedValueOnce(response(200, MEMBER, 'application/json'));
    vi.stubGlobal('fetch', fetchMock);

    expect(await fetchCurrentMember()).toBeNull();
    expect((await fetchSession(true)).state).toBe('active');
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});

describe('currentMember: platform admin', () => {
  it('requires both admin and active', () => {
    expect(isPlatformAdmin(null)).toBe(false);
    expect(isPlatformAdmin({ ...MEMBER, platformRole: 'admin', status: 'active' })).toBe(true);
    expect(isPlatformAdmin({ ...MEMBER, platformRole: 'admin', status: 'deactivated' })).toBe(false);
    expect(isPlatformAdmin({ ...MEMBER, platformRole: 'member', status: 'active' })).toBe(false);
  });
});
