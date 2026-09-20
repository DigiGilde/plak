import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError, request } from '../src/api/client';

/**
 * vue-tsc cannot type `rejects.toSatisfy`: vitest wraps the chai matcher in
 * `Promisify`, which drops the call signature. So we catch the rejection
 * ourselves and return the error to assert on.
 */
async function refusedWith(promise: Promise<unknown>): Promise<ApiError> {
  await expect(promise).rejects.toBeInstanceOf(ApiError);
  return (await promise.catch((error: unknown) => error)) as ApiError;
}

function setCookie(name: string, value: string): void {
  // __Host- cookies require an explicit Secure (and Path=/, no Domain).
  document.cookie = `${name}=${value}; path=/; secure`;
}

function clearCookies(): void {
  document.cookie.split(';').forEach((row) => {
    const name = row.split('=')[0]?.trim();
    if (name) {
      document.cookie = `${name}=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/`;
    }
  });
}

describe('verzoek', () => {
  beforeEach(() => {
    clearCookies();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('zet de X-CSRF-Token-header op een POST vanuit de csrf-cookie', async () => {
    setCookie('__Host-plak-csrf', 'geheime-token-waarde');

    const fetchMock = vi.fn(
      async (_path: string, _init?: RequestInit) =>
        new Response(JSON.stringify({ versionId: 'abc' }), {
          status: 201,
          headers: { 'content-type': 'application/json' },
        }),
    );
    vi.stubGlobal('fetch', fetchMock);

    await request('/-/api/v1/groups', { method: 'POST', body: '{}' });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [, init] = fetchMock.mock.calls[0]!;
    const headers = new Headers(init?.headers);
    expect(headers.get('X-CSRF-Token')).toBe('geheime-token-waarde');
  });

  it('zet geen CSRF-header op een GET', async () => {
    setCookie('__Host-plak-csrf', 'geheime-token-waarde');

    const fetchMock = vi.fn(
      async (_path: string, _init?: RequestInit) =>
        new Response(JSON.stringify({}), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        }),
    );
    vi.stubGlobal('fetch', fetchMock);

    await request('/-/api/v1/overview');

    const [, init] = fetchMock.mock.calls[0]!;
    const headers = new Headers(init?.headers);
    expect(headers.get('X-CSRF-Token')).toBeNull();
  });

  it('parseert een application/problem+json-fout naar een Fout-object', async () => {
    const fetchMock = vi.fn(
      async () =>
        new Response(
          JSON.stringify({
            type: 'https://plak.example/fouten/onbekend-site',
            title: 'Onbekend site',
            status: 404,
            detail: 'Er bestaat geen site met deze slug.',
          }),
          {
            status: 404,
            headers: { 'content-type': 'application/problem+json' },
          },
        ),
    );
    vi.stubGlobal('fetch', fetchMock);

    const error = await refusedWith(request('/-/api/v1/sites/groep/onbekend'));
    expect(error.problem).toEqual({
      type: 'https://plak.example/fouten/onbekend-site',
      title: 'Onbekend site',
      status: 404,
      detail: 'Er bestaat geen site met deze slug.',
    });
  });

  it('valt terug op een generiek Fout-object als de body geen problem+json is', async () => {
    const fetchMock = vi.fn(
      async () =>
        new Response('Internal Server Error', {
          status: 500,
          statusText: 'Internal Server Error',
          headers: { 'content-type': 'text/plain' },
        }),
    );
    vi.stubGlobal('fetch', fetchMock);

    await expect(request('/-/api/v1/overview')).rejects.toBeInstanceOf(ApiError);
  });
});
