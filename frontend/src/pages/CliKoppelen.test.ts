import { mount } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createMemoryHistory, createRouter, type Router } from 'vue-router';

import { makeMockBackend, type MockBackend } from '@/api/mock';
import { _resetCurrentMemberCache } from '@/composables/currentMember';
import { serverErrorFetch, untilIdle } from '@/components/site/testHelpers';
import CliKoppelen from './CliKoppelen.vue';

let backend: MockBackend;
let originalLocation: Location;

beforeEach(() => {
  backend = makeMockBackend();
  vi.stubGlobal('fetch', backend.fetch);
  _resetCurrentMemberCache();
  originalLocation = window.location;
  // @ts-expect-error -- deliberately replaced with a settable stand-in for the test.
  delete window.location;
  (window as unknown as { location: Location }).location = { href: '' } as unknown as Location;
});

afterEach(() => {
  vi.unstubAllGlobals();
  (window as unknown as { location: Location }).location = originalLocation;
});

async function makeWrapper(path: string): Promise<{ wrapper: ReturnType<typeof mount>; router: Router }> {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/cli-koppelen', name: 'cli-koppelen', component: CliKoppelen }],
  });
  await router.push(path);
  await router.isReady();
  const wrapper = mount(CliKoppelen, { global: { plugins: [router] } });
  await untilIdle();
  return { wrapper, router };
}

describe('CliKoppelen: session', () => {
  it('redirects an anonymous visitor to login, with the code in returnTo', async () => {
    backend.data.loggedInMemberId = null;

    await makeWrapper('/cli-koppelen?code=abcd-efgh');

    expect(window.location.href).toBe(
      '/-/login?returnTo=' + encodeURIComponent('/cli-koppelen?code=abcd-efgh'),
    );
  });

  it('redirects to login even without a code', async () => {
    backend.data.loggedInMemberId = null;

    await makeWrapper('/cli-koppelen');

    expect(window.location.href).toBe('/-/login?returnTo=' + encodeURIComponent('/cli-koppelen'));
  });

  it('shows the revoked-access treatment for a deactivated member', async () => {
    // The mock's /me does not model the 403 "deactivated" case (it only
    // checks whether the id exists), so this stubs the response directly,
    // the same way Start.test.ts and Landing.test.ts do.
    vi.stubGlobal('fetch', () =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            type: 'about:blank',
            title: 'Geen toegang',
            status: 403,
            detail: 'Je toegang is ingetrokken door een platformbeheerder.',
            code: 'MEMBER_DEACTIVATED',
          }),
          { status: 403, headers: { 'content-type': 'application/problem+json' } },
        ),
      ),
    );

    const { wrapper } = await makeWrapper('/cli-koppelen?code=abcd-efgh');

    expect(wrapper.find('nldd-title h1').text()).toBe('Je toegang is ingetrokken');
  });

  it('proceeds to the code lookup after reactivation, to the input form without a code', async () => {
    vi.stubGlobal('fetch', () =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            type: 'about:blank',
            title: 'Geen toegang',
            status: 403,
            detail: 'Wacht nog even.',
            code: 'MEMBER_DEACTIVATED',
          }),
          { status: 403, headers: { 'content-type': 'application/problem+json' } },
        ),
      ),
    );

    const { wrapper } = await makeWrapper('/cli-koppelen');
    expect(wrapper.find('nldd-title h1').text()).toBe('Je toegang is ingetrokken');

    // The account gets released while the tab stays open: the retry inside
    // Landing checks again and this page picks up from there.
    vi.stubGlobal('fetch', backend.fetch);
    await wrapper.find('[data-testid="controleer-opnieuw"]').trigger('click');
    await untilIdle();

    expect(wrapper.find('[data-testid="code-formulier"]').exists()).toBe(true);
  });

  it('shows a generic error message when /me itself fails unexpectedly', async () => {
    vi.stubGlobal('fetch', serverErrorFetch());

    const { wrapper } = await makeWrapper('/cli-koppelen?code=abcd-efgh');

    expect(wrapper.html()).toContain('Serverfout');
  });
});

describe('CliKoppelen: entering a code', () => {
  it('asks for a code when none is in the url', async () => {
    const { wrapper } = await makeWrapper('/cli-koppelen');

    expect(wrapper.find('[data-testid="code-formulier"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="code-weergave"]').exists()).toBe(false);
  });

  it('normalizes and looks up the manually entered code', async () => {
    const { wrapper } = await makeWrapper('/cli-koppelen');

    const input = wrapper.find('[data-testid="code-invoer"]').element;
    input.dispatchEvent(new CustomEvent('input', { detail: { value: 'abcdefgh' } }));
    await wrapper.find('[data-testid="code-formulier"]').trigger('submit');
    await untilIdle();

    expect(wrapper.find('[data-testid="code-weergave"]').text()).toBe('ABCD-EFGH');
  });

  it('shows USER_CODE_UNKNOWN and stays on the input form', async () => {
    const { wrapper } = await makeWrapper('/cli-koppelen');

    const input = wrapper.find('[data-testid="code-invoer"]').element;
    input.dispatchEvent(new CustomEvent('input', { detail: { value: 'ZZZZ-ZZZZ' } }));
    await wrapper.find('[data-testid="code-formulier"]').trigger('submit');
    await untilIdle();

    expect(wrapper.find('[data-testid="code-formulier"]').exists()).toBe(true);
    expect(wrapper.html()).toContain('onbekend, verlopen of al gebruikt');
  });
});

describe('CliKoppelen: looking up the code via the url', () => {
  it('looks up the code from the query and shows the data for confirmation', async () => {
    const { wrapper } = await makeWrapper('/cli-koppelen?code=ABCD-EFGH');

    expect(wrapper.find('[data-testid="code-weergave"]').text()).toBe('ABCD-EFGH');
    expect(wrapper.find('[data-testid="code-programma"]').text()).toContain('plak-cli');
    expect(wrapper.find('[data-testid="code-netwerk"]').text()).toContain('203.0.113.0/24');
    expect(wrapper.find('[data-testid="code-waarschuwing"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="code-ander-netwerk"]').exists()).toBe(false);
  });

  it('names the account being linked', async () => {
    const { wrapper } = await makeWrapper('/cli-koppelen?code=ABCD-EFGH');

    const member = backend.data.members.find((m) => m.id === backend.data.loggedInMemberId)!;
    const account = wrapper.find('[data-testid="code-account"]');
    expect(account.exists()).toBe(true);
    expect(account.attributes('text')).toContain(member.email);
    if (member.name) expect(account.attributes('text')).toContain(member.name);
  });

  it('names only the e-mail address when the member has no name', async () => {
    const member = backend.data.members.find((m) => m.id === backend.data.loggedInMemberId)!;
    member.name = '';

    const { wrapper } = await makeWrapper('/cli-koppelen?code=ABCD-EFGH');

    expect(wrapper.find('[data-testid="code-account"]').attributes('text')).toBe(
      `Je koppelt dit programma aan ${member.email}`,
    );
  });

  it('warns when the request comes from a different network', async () => {
    backend.data.deviceAuthorizations[0]!.sameNetwork = false;

    const { wrapper } = await makeWrapper('/cli-koppelen?code=ABCD-EFGH');

    expect(wrapper.find('[data-testid="code-ander-netwerk"]').exists()).toBe(true);
  });

  it("doesn't warn about the network when it's unknown", async () => {
    backend.data.deviceAuthorizations[0]!.sameNetwork = null;

    const { wrapper } = await makeWrapper('/cli-koppelen?code=ABCD-EFGH');

    expect(wrapper.find('[data-testid="code-ander-netwerk"]').exists()).toBe(false);
  });

  it("doesn't repeat the warning in heading and supporting text", async () => {
    const { wrapper } = await makeWrapper('/cli-koppelen?code=ABCD-EFGH');

    const warning = wrapper.find('[data-testid="code-waarschuwing"]');
    const heading = warning.attributes('text')!;
    const supporting = warning.attributes('supporting-text')!;
    expect(supporting).not.toContain(heading);
    expect(supporting).not.toContain('plak login');
  });

  it('shows "Onbekend programma" without clientName', async () => {
    backend.data.deviceAuthorizations[0]!.clientName = null;

    const { wrapper } = await makeWrapper('/cli-koppelen?code=ABCD-EFGH');

    expect(wrapper.find('[data-testid="code-programma"]').text()).toContain('Onbekend programma');
  });

  it('links after Koppelen and shows the success message', async () => {
    const { wrapper } = await makeWrapper('/cli-koppelen?code=ABCD-EFGH');

    await wrapper.find('[data-testid="code-koppelen"]').trigger('click');
    await untilIdle();

    expect(wrapper.find('[data-testid="code-gekoppeld"]').exists()).toBe(true);
    expect(backend.data.deviceAuthorizations[0]!.status).toBe('approved');
    expect(backend.data.cliSessions.some((s) => s.clientName === 'plak-cli')).toBe(true);
  });

  it('refuses after Weigeren and grants no access', async () => {
    const { wrapper } = await makeWrapper('/cli-koppelen?code=ABCD-EFGH');

    await wrapper.find('[data-testid="code-weigeren"]').trigger('click');
    await untilIdle();

    expect(wrapper.find('[data-testid="code-geweigerd"]').exists()).toBe(true);
    expect(backend.data.deviceAuthorizations[0]!.status).toBe('denied');
  });

  it('redirects to login on SESSION_NOT_FRESH during Koppelen', async () => {
    const { wrapper } = await makeWrapper('/cli-koppelen?code=ABCD-EFGH');

    vi.stubGlobal('fetch', (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url.includes('/me') && (init?.method ?? 'GET') === 'GET') {
        return backend.fetch(input, init);
      }
      return Promise.resolve(
        new Response(
          JSON.stringify({
            type: 'about:blank',
            title: 'Sessie niet vers genoeg',
            status: 401,
            detail: 'Log opnieuw in.',
            code: 'SESSION_NOT_FRESH',
          }),
          { status: 401, headers: { 'content-type': 'application/problem+json' } },
        ),
      );
    });

    await wrapper.find('[data-testid="code-koppelen"]').trigger('click');
    await untilIdle();

    expect(window.location.href).toBe(
      '/-/login?returnTo=' + encodeURIComponent('/cli-koppelen?code=ABCD-EFGH'),
    );
  });

  it('goes back to the input form when the code has meanwhile been used', async () => {
    const { wrapper } = await makeWrapper('/cli-koppelen?code=ABCD-EFGH');

    vi.stubGlobal('fetch', (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url.includes('/me') && (init?.method ?? 'GET') === 'GET') {
        return backend.fetch(input, init);
      }
      return Promise.resolve(
        new Response(
          JSON.stringify({
            type: 'about:blank',
            title: 'Onbekende code',
            status: 404,
            detail: 'Deze code is al gebruikt.',
            code: 'USER_CODE_UNKNOWN',
          }),
          { status: 404, headers: { 'content-type': 'application/problem+json' } },
        ),
      );
    });

    await wrapper.find('[data-testid="code-koppelen"]').trigger('click');
    await untilIdle();

    expect(wrapper.find('[data-testid="code-formulier"]').exists()).toBe(true);
    expect(wrapper.html()).toContain('onbekend, verlopen of al gebruikt');
  });

  it('shows a generic error when approving fails unexpectedly', async () => {
    const { wrapper } = await makeWrapper('/cli-koppelen?code=ABCD-EFGH');

    vi.stubGlobal('fetch', (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url.includes('/me') && (init?.method ?? 'GET') === 'GET') {
        return backend.fetch(input, init);
      }
      return serverErrorFetch()(input, init);
    });

    await wrapper.find('[data-testid="code-koppelen"]').trigger('click');
    await untilIdle();

    expect(wrapper.html()).toContain('Serverfout');
  });

  it('redirects to login on SESSION_NOT_FRESH, carrying the code along', async () => {
    vi.stubGlobal('fetch', (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url.includes('/me') && (init?.method ?? 'GET') === 'GET') {
        return backend.fetch(input, init);
      }
      return Promise.resolve(
        new Response(
          JSON.stringify({
            type: 'about:blank',
            title: 'Sessie niet vers genoeg',
            status: 401,
            detail: 'Log opnieuw in.',
            code: 'SESSION_NOT_FRESH',
          }),
          { status: 401, headers: { 'content-type': 'application/problem+json' } },
        ),
      );
    });

    await makeWrapper('/cli-koppelen?code=ABCD-EFGH');

    expect(window.location.href).toBe(
      '/-/login?returnTo=' + encodeURIComponent('/cli-koppelen?code=ABCD-EFGH'),
    );
  });

  it("shows the server's error message on 429", async () => {
    vi.stubGlobal('fetch', (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url.includes('/me') && (init?.method ?? 'GET') === 'GET') {
        return backend.fetch(input, init);
      }
      return Promise.resolve(
        new Response(
          JSON.stringify({
            type: 'about:blank',
            title: 'Te veel pogingen',
            status: 429,
            detail: 'Wacht even en probeer het opnieuw.',
            code: 'TOO_MANY_ATTEMPTS',
          }),
          { status: 429, headers: { 'content-type': 'application/problem+json' } },
        ),
      );
    });

    const { wrapper } = await makeWrapper('/cli-koppelen?code=ABCD-EFGH');

    expect(wrapper.html()).toContain('Wacht even en probeer het opnieuw.');
  });

  it('shows a generic error message on an unexpected error', async () => {
    vi.stubGlobal('fetch', (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url.includes('/me') && (init?.method ?? 'GET') === 'GET') {
        return backend.fetch(input, init);
      }
      return serverErrorFetch()(input, init);
    });

    const { wrapper } = await makeWrapper('/cli-koppelen?code=ABCD-EFGH');

    expect(wrapper.html()).toContain('Serverfout');
  });
});
