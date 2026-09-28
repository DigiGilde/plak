import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError, request } from './client';
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

describe('CSRF header', () => {
  afterEach(() => {
    // __Host- cookies require an explicit Secure (and Path=/, no Domain).
    document.cookie = '__Host-plak-csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/; secure';
  });

  it('sends no CSRF header when there is no cookie to double-submit', async () => {
    await request('/-/api/groups', { method: 'POST', body: '{}' });

    expect(headersOf().get('X-CSRF-Token')).toBeNull();
  });
});

describe('error responses', () => {
  it('fills in defaults for a problem+json body missing optional fields', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({}), {
          status: 500,
          statusText: 'Server Error',
          headers: { 'content-type': 'application/problem+json' },
        }),
      ),
    );

    const error = (await request('/-/api/sites/x').catch((e: unknown) => e)) as ApiError;

    expect(error.problem).toEqual({
      type: 'about:blank',
      title: 'Server Error',
      status: 500,
      detail: undefined,
      code: undefined,
    });
  });

  it('builds a generic problem from a non-problem+json error response', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response('Internal Server Error', {
          status: 500,
          statusText: 'Internal Server Error',
          headers: { 'content-type': 'text/plain' },
        }),
      ),
    );

    const error = (await request('/-/api/sites/x').catch((e: unknown) => e)) as ApiError;

    expect(error.problem).toEqual({
      type: 'about:blank',
      title: 'Internal Server Error',
      status: 500,
    });
  });

  it('treats a response without a content-type header as non-problem+json', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(new Response(null, { status: 500, statusText: 'Server Error' })),
    );

    const error = (await request('/-/api/sites/x').catch((e: unknown) => e)) as ApiError;

    expect(error.problem).toEqual({ type: 'about:blank', title: 'Server Error', status: 500 });
  });

  it('falls back to a generic title when the response has none', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response('', {
          status: 500,
          statusText: '',
        }),
      ),
    );

    const error = (await request('/-/api/sites/x').catch((e: unknown) => e)) as ApiError;

    expect(error.problem.title).not.toBe('');
  });
});

describe('response bodies', () => {
  it('returns undefined for a 204 No Content, without reading a body', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(null, { status: 204 })));

    const result = await request('/-/api/sites/x', { method: 'DELETE' });

    expect(result).toBeUndefined();
  });

  it('parses the JSON body for an ordinary 2xx response', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ id: '1' }), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        }),
      ),
    );

    const result = await request<{ id: string }>('/-/api/sites/x');

    expect(result).toEqual({ id: '1' });
  });
});
