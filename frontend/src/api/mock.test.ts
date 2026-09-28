import { afterEach, describe, expect, it } from 'vitest';

import { installMock, makeMockBackend } from './mock';

describe('mockFetch (fallback)', () => {
  it('answers an unmatched route with a 404 problem', async () => {
    const backend = makeMockBackend();

    const response = await backend.fetch('/-/api/v1/does-not-exist', { method: 'GET' });

    expect(response.status).toBe(404);
    const body = (await response.json()) as { title: string };
    expect(body.title).toBe('Onbekend eindpunt');
  });
});

describe('installMock', () => {
  const originalFetch = globalThis.fetch;

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it('replaces globalThis.fetch with the mock, and restores it again', () => {
    const backend = makeMockBackend();

    const restore = installMock(backend);
    expect(globalThis.fetch).toBe(backend.fetch);

    restore();
    expect(globalThis.fetch).toBe(originalFetch);
  });

  it('builds its own backend when none is given', () => {
    const restore = installMock();
    expect(globalThis.fetch).not.toBe(originalFetch);

    restore();
    expect(globalThis.fetch).toBe(originalFetch);
  });
});
