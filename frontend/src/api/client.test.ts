import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { request } from './client';
import { _setLocaleForTest } from '../i18n';

let calls: Array<{ url: string; init: RequestInit }>;

function ok(body: unknown = {}): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'content-type': 'application/json' },
  });
}

beforeEach(() => {
  calls = [];
  vi.stubGlobal('fetch', (url: string, init: RequestInit) => {
    calls.push({ url, init });
    return Promise.resolve(ok());
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function headersOf(index = 0): Headers {
  return new Headers(calls[index]!.init.headers);
}

describe('Accept-Language', () => {
  afterEach(() => {
    _setLocaleForTest('nl');
  });

  it('asks the API for the language the interface is in', async () => {
    _setLocaleForTest('nl');
    await request('/-/api/me');

    expect(headersOf().get('Accept-Language')).toBe('nl');
  });

  it('follows a change of language, so a refusal comes back in the language on screen', async () => {
    _setLocaleForTest('en');
    await request('/-/api/me');

    expect(headersOf().get('Accept-Language')).toBe('en');
  });

  it('asks for it on a mutation too, beside the CSRF header', async () => {
    _setLocaleForTest('nl');
    await request('/-/api/groups', { method: 'POST', body: '{}' });

    expect(headersOf().get('Accept-Language')).toBe('nl');
  });

  it('does not lose the headers the caller passed in', async () => {
    _setLocaleForTest('nl');
    await request('/-/api/me', { headers: { 'Content-Type': 'application/json' } });

    expect(headersOf().get('Content-Type')).toBe('application/json');
    expect(headersOf().get('Accept-Language')).toBe('nl');
  });
});
