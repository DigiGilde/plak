import '@nldd/design-system';

import { flushPromises, mount } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createMemoryHistory, createRouter, RouterView } from 'vue-router';

import { MOCK_CONTENT_BASE } from '../../tests/mock-api';
import { _resetCurrentMemberCache } from '../composables/currentMember';
import { _setLocaleForTest } from '@/i18n';
import { routes, titleAfterNavigation } from '@/router';
import Landing from './Landing.vue';
import Start from './Start.vue';

let wrapper: ReturnType<typeof mount> | null = null;

beforeEach(() => {
  _resetCurrentMemberCache();
});

afterEach(() => {
  wrapper?.unmount();
  wrapper = null;
  vi.unstubAllGlobals();
});

const WITHDRAWN = 'Je toegang is ingetrokken door een platformbeheerder.';

function meFetch(status: number): typeof fetch {
  return () => {
    if (status === 200) {
      return Promise.resolve(
        new Response(
          JSON.stringify({
            id: 'lid-1',
            ssoSubject: 'actief-lid',
            email: 'lid@voorbeeld.nl',
            name: 'Lid',
            platformRole: 'member',
            status: 'active',
            createdAt: '2026-01-01T00:00:00Z',
            lastLoginAt: null,
            lastActiveAt: null,
            contentBaseUrl: MOCK_CONTENT_BASE,
          }),
          { status: 200, headers: { 'content-type': 'application/json' } },
        ),
      );
    }
    const detail = status === 403 ? WITHDRAWN : 'Er is geen actieve sessie.';
    return Promise.resolve(
      new Response(JSON.stringify({ type: 'about:blank', title: 'Fout', status, detail }), {
        status,
        headers: { 'content-type': 'application/problem+json' },
      }),
    );
  };
}

/** The overview has its own tests; all that counts here is whether it is its turn. */
async function makeWrapper(status: number) {
  vi.stubGlobal('fetch', meFetch(status));
  const mounted = mount(Start, {
    global: { stubs: { Overview: { template: '<div data-testid="overview"></div>' } } },
  });
  await flushPromises();
  return mounted;
}

describe('Start: the three session states', () => {
  it('shows the overview to an active member', async () => {
    wrapper = await makeWrapper(200);

    expect(wrapper.find('[data-testid="overview"]').exists()).toBe(true);
    expect(wrapper.findComponent(Landing).exists()).toBe(false);
  });

  it('shows the login explanation without a session', async () => {
    wrapper = await makeWrapper(401);

    expect(wrapper.find('[data-testid="overview"]').exists()).toBe(false);
    expect(wrapper.findComponent(Landing).props()).toMatchObject({
      state: 'no-session',
      reason: '',
    });
  });

  it('shows the withdrawn explanation with the backend\'s reason for a blocked session', async () => {
    wrapper = await makeWrapper(403);

    expect(wrapper.find('[data-testid="overview"]').exists()).toBe(false);
    expect(wrapper.findComponent(Landing).props()).toMatchObject({
      state: 'awaiting-activation',
      reason: WITHDRAWN,
    });
  });

  it('falls back to the login explanation on an unexpected error instead of continuing to load', async () => {
    wrapper = await makeWrapper(500);

    expect(wrapper.find('nldd-activity-indicator').exists()).toBe(false);
    expect(wrapper.findComponent(Landing).props('state')).toBe('no-session');
  });

  it('lets a blocked member in once the landing page reports an active session', async () => {
    wrapper = await makeWrapper(403);

    wrapper
      .findComponent(Landing)
      .vm.$emit('refreshed', { state: 'active', member: null, reason: '' });
    await flushPromises();

    expect(wrapper.find('[data-testid="overview"]').exists()).toBe(true);
  });
});

/** `/` the way the app serves it: the real route table, and the hook that titles the tab after a navigation. */
async function openThroughRouter(status: number) {
  vi.stubGlobal('fetch', meFetch(status));
  const router = createRouter({ history: createMemoryHistory(), routes });
  router.afterEach(titleAfterNavigation);
  await router.push('/');
  const mounted = mount(RouterView, {
    global: {
      plugins: [router],
      stubs: { Overview: { template: '<div data-testid="overview"></div>' } },
    },
  });
  await flushPromises();
  return mounted;
}

describe('Start: the title of the browser tab', () => {
  afterEach(() => {
    _setLocaleForTest('nl');
    document.title = '';
  });

  it.each(['nl', 'en'] as const)(
    'is the name of the product without a session, like the heading (%s)',
    async (locale) => {
      _setLocaleForTest(locale);
      wrapper = await openThroughRouter(401);

      expect(wrapper.find('h1').text()).toBe('Toon');
      expect(document.title).toBe('Toon');
    },
  );

  it.each([
    ['nl', 'Overzicht - Toon'],
    ['en', 'Overview - Toon'],
  ] as const)('names the overview for a member (%s)', async (locale, title) => {
    _setLocaleForTest(locale);
    wrapper = await openThroughRouter(200);

    expect(wrapper.find('[data-testid="overview"]').exists()).toBe(true);
    expect(document.title).toBe(title);
  });

  it('does not give the withdrawn page the title of the landing page', async () => {
    wrapper = await openThroughRouter(403);

    expect(wrapper.find('[data-testid="reason"]').exists()).toBe(true);
    expect(document.title).not.toBe('Toon');
  });

  it('takes the title of the landing page when the check finds the session gone', async () => {
    wrapper = await openThroughRouter(403);

    wrapper
      .findComponent(Landing)
      .vm.$emit('refreshed', { state: 'no-session', member: null, reason: '' });
    await flushPromises();

    expect(document.title).toBe('Toon');
  });
});
