import { flushPromises, mount } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createMemoryHistory, createRouter, type Router } from 'vue-router';

import { makeMockBackend, type MockBackend } from '@/api/mock';
import { _resetCurrentMemberCache } from '@/composables/currentMember';
import { _resetBreadcrumbs, breadcrumbsFor } from '@/composables/breadcrumbs';
import { goToDone } from '@/composables/publishedMark';
import { routes } from '@/router';

import Done from './Done.vue';

let backend: MockBackend;
let router: Router;

async function mountComponent(path: string) {
  router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/:group/:site/done', component: Done },
      { path: '/:group/:site', component: { template: '<div />' } },
    ],
  });
  await router.push(path);
  await router.isReady();
  return mount(Done, { global: { plugins: [router] } });
}

/** The result screen as the publish flow reaches it, mark and all. */
async function mountFromPublish(group: string, site: string) {
  router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/:group/:site/done', component: Done },
      { path: '/:group/:site', component: { template: '<div />' } },
    ],
  });
  await goToDone(router, group, site);
  await router.isReady();
  return mount(Done, { global: { plugins: [router] } });
}

function writeClipboard(): ReturnType<typeof vi.fn> {
  const write = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, 'clipboard', {
    value: { writeText: write },
    configurable: true,
  });
  return write;
}

beforeEach(() => {
  backend = makeMockBackend();
  vi.stubGlobal('fetch', backend.fetch);
  _resetBreadcrumbs();
  _resetCurrentMemberCache();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('Done (the address is the answer)', () => {
  it("shows the site's address on the content host, not on the admin origin", async () => {
    const wrapper = await mountComponent('/team-aurora/website/done');
    await flushPromises();

    expect(wrapper.find('h1').text()).toBe('Je site staat online');
    const link = wrapper.find('[data-testid="done-address-link-site"]');
    expect(link.attributes('href')).toBe('https://sites.plak.test/team-aurora/website/');
    expect(link.text()).toBe('https://sites.plak.test/team-aurora/website/');
    expect(link.attributes('target')).toBe('_blank');

    wrapper.unmount();
  });

  it('puts copy and open next to the address, both with a text label', async () => {
    const wrapper = await mountComponent('/team-aurora/website/done');
    await flushPromises();

    const copy = wrapper.find('[data-testid="done-copy-site"]');
    expect(copy.attributes('text')).toBe('Kopieer adres');
    const openButtons = wrapper.find('[data-testid="done-open-site"]');
    expect(openButtons.attributes('text')).toBe('Open site');
    expect(openButtons.attributes('href')).toBe('https://sites.plak.test/team-aurora/website/');
    expect(openButtons.attributes('target')).toBe('_blank');

    wrapper.unmount();
  });

  it('leaves room for a second address: the rows come from a list, not fixed markup', async () => {
    const wrapper = await mountComponent('/team-aurora/website/done');
    await flushPromises();

    // Today there is one address; the structure is the one for more.
    expect(wrapper.findAll('[data-testid="done-address"]')).toHaveLength(1);
    expect(wrapper.find('[data-testid="done-address"]').text()).toContain('Adres van je site');

    wrapper.unmount();
  });

  it('copies the address and confirms it in the page, not in a toast', async () => {
    const write = writeClipboard();
    const wrapper = await mountComponent('/team-aurora/website/done');
    await flushPromises();

    expect(wrapper.find('[data-testid="done-copy-notice-site"]').text()).toBe('');

    await wrapper.find('[data-testid="done-copy-site"]').trigger('click');
    await flushPromises();

    expect(write).toHaveBeenCalledWith('https://sites.plak.test/team-aurora/website/');
    const notice = wrapper.find('[data-testid="done-copy-notice-site"]');
    expect(notice.text()).toBe('Adres gekopieerd.');
    // A status line, so a screen reader gets the confirmation too.
    expect(notice.attributes('role')).toBe('status');
    expect(wrapper.find('nldd-notification').exists()).toBe(false);

    wrapper.unmount();
  });

  it('points to the address itself when clipboard access is refused', async () => {
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText: vi.fn().mockRejectedValue(new Error('geen toestemming')) },
      configurable: true,
    });
    const wrapper = await mountComponent('/team-aurora/website/done');
    await flushPromises();

    await wrapper.find('[data-testid="done-copy-site"]').trigger('click');
    await flushPromises();

    expect(wrapper.find('[data-testid="done-copy-notice-site"]').text()).toContain(
      'Selecteer het adres',
    );

    wrapper.unmount();
  });
});

describe('Done (who can see this)', () => {
  it('names the level and explains it, instead of claiming "Publieke URL"', async () => {
    const wrapper = await mountComponent('/team-aurora/website/done');
    await flushPromises();

    expect(wrapper.text()).not.toContain('Publieke URL');
    expect(wrapper.find('[data-testid="done-visibility"]').text()).toContain(
      'Iedereen kan de site bekijken',
    );
    expect(wrapper.find('[data-testid="done-visibility-change"]').attributes('href')).toBe(
      '/team-aurora/website/access',
    );

    wrapper.unmount();
  });

  it("tells what actually applies, even when it isn't public", async () => {
    backend.data.sites[0]!.access = { base: 'site_team', keys: false, invitees: false };
    const wrapper = await mountComponent('/team-aurora/website/done');
    await flushPromises();

    const row = wrapper.find('[data-testid="done-visibility"]').text();
    expect(row).toContain('Alleen wie een rol heeft op deze site');

    wrapper.unmount();
  });

  it('links through to admin and to the overview', async () => {
    const wrapper = await mountComponent('/team-aurora/website/done');
    await flushPromises();

    expect(wrapper.find('[data-testid="done-to-site"]').attributes('href')).toBe(
      '/team-aurora/website',
    );
    expect(wrapper.find('[data-testid="done-to-overview"]').attributes('href')).toBe('/');

    wrapper.unmount();
  });
});

describe('Done (reload and share)', () => {
  it('exists as its own route in the app, so the address is reloadable', () => {
    const record = routes.find((r) => 'name' in r && r.name === 'site-done');
    expect(record?.path).toBe('/:group/:site/done');
    // Beside the site page, not as a tab inside it: otherwise the route
    // would be a child of '/:group/:site'.
    const siteRecord = routes.find((r) => r.path === '/:group/:site');
    expect(
      (siteRecord?.children ?? []).some((child) => child.path === 'done'),
    ).toBe(false);
  });

  it('loads its own data, so a reload shows the same thing', async () => {
    const wrapper = await mountComponent('/team-aurora/website/done');

    expect(wrapper.find('nldd-inline-dialog[variant="loading"]').exists()).toBe(true);

    await flushPromises();

    expect(wrapper.find('nldd-inline-dialog[variant="loading"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="done-address-link-site"]').exists()).toBe(true);

    wrapper.unmount();
  });

  it('supplies the breadcrumb path to the app shell', async () => {
    const wrapper = await mountComponent('/team-aurora/website/done');
    await flushPromises();

    expect(breadcrumbsFor('/team-aurora/website/done')).toEqual([
      { text: 'Overzicht', href: '/' },
      { text: 'Team Aurora', href: '/team-aurora' },
      { text: 'Team Aurora website' },
    ]);

    wrapper.unmount();
  });

  // Same trail, same rule: an impossible group slug may not become a link off
  // our own origin.
  it.each(['//example.com', '/\\example.com', '/\\/example.com'])(
    'leaves the group crumb of %s without an href',
    async (slug) => {
      const wrapper = await mountComponent(`/${encodeURIComponent(slug)}/website/done`);
      await flushPromises();

      const crumbs = breadcrumbsFor(router.currentRoute.value.path);
      expect(crumbs[1]).toEqual({ text: slug, href: undefined });

      wrapper.unmount();
    },
  );

  it('fetches again as soon as the route points to a different site', async () => {
    backend.data.sites.push({
      id: '6d0e4b9a-1f27-4c83-8a5e-2b7c9d1e3f46',
      groupSlug: 'team-aurora',
      slug: 'tweede',
      title: 'Tweede site',
      access: { base: 'public', keys: false, invitees: false },
      externalSources: false,
      sandbox: true,
      liveVersionsKept: null,
      liveVersionId: 'versie-1',
      createdBy: 'dev-beheerder',
      hasLiveVersion: true,
      lastPublishedAt: '2026-07-17T14:32:00.000Z',
      previewCount: 0,
      previousSlugs: [],
    });
    const wrapper = await mountComponent('/team-aurora/website/done');
    await flushPromises();

    await router.push('/team-aurora/tweede/done');
    await flushPromises();

    expect(wrapper.find('[data-testid="done-address-link-site"]').attributes('href')).toBe(
      'https://sites.plak.test/team-aurora/tweede/',
    );

    wrapper.unmount();
  });
});

describe('Done (what goes wrong)', () => {
  it("reports a site that doesn't exist, instead of a blank screen", async () => {
    const wrapper = await mountComponent('/team-aurora/bestaat-niet/done');
    await flushPromises();

    const banner = wrapper.find('nldd-banner[variant="critical"]');
    expect(banner.attributes('text')).toBe('Onbekende site');
    expect(wrapper.find('[data-testid="done-address-link-site"]').exists()).toBe(false);

    wrapper.unmount();
  });

  it('reports an address without group or site as an error, not a blank screen', async () => {
    router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/done', component: Done }],
    });
    await router.push('/done');
    await router.isReady();
    const wrapper = mount(Done, { global: { plugins: [router] } });
    await flushPromises();

    expect(wrapper.find('nldd-banner[variant="critical"]').exists()).toBe(true);

    wrapper.unmount();
  });

  it('reports a failed fetch as an error, not an empty page', async () => {
    vi.stubGlobal('fetch', () => Promise.reject(new Error('netwerk weg')));
    const wrapper = await mountComponent('/team-aurora/website/done');
    await flushPromises();

    expect(wrapper.find('nldd-banner[variant="critical"]').exists()).toBe(true);

    wrapper.unmount();
  });

  it('says so honestly when no version is online yet', async () => {
    backend.data.sites[0]!.liveVersionId = null;
    backend.data.sites[0]!.hasLiveVersion = false;
    const wrapper = await mountComponent('/team-aurora/website/done');
    await flushPromises();

    expect(wrapper.find('h1').text()).toBe('Je site staat nog niet online');
    expect(wrapper.find('[data-testid="done-address-link-site"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="done-no-version"]').exists()).toBe(true);
    // The visibility stays on screen: it applies before anything is there.
    expect(wrapper.find('[data-testid="done-visibility"]').exists()).toBe(true);

    wrapper.unmount();
  });

  it("shows the path instead of a half address when there's no content origin", async () => {
    backend.data.loggedInMemberId = null;
    const wrapper = await mountComponent('/team-aurora/website/done');
    await flushPromises();

    expect(wrapper.find('[data-testid="done-address-link-site"]').attributes('href')).toBe(
      '/team-aurora/website/',
    );

    wrapper.unmount();
  });
});

describe('Done (secret link)', () => {
  function keyPostCalls(spy: ReturnType<typeof vi.fn>): number {
    return spy.mock.calls.filter(([input, init]) => {
      const url = typeof input === 'string' ? input : (input as URL | Request).toString();
      return url.includes('/keys') && (init as RequestInit | undefined)?.method === 'POST';
    }).length;
  }

  it('creates no key on a visit that does not come from publishing', async () => {
    // A link from anywhere, another site included, can open this route; with
    // secret links on and no active key (an admin just revoked it), such a
    // visit must not quietly mint a new one.
    backend.data.sites[0]!.access = { base: 'nobody', keys: true, invitees: false };
    backend.data.keys = backend.data.keys.filter((k) => k.siteSlug !== 'website');
    const spy = vi.fn(backend.fetch);
    vi.stubGlobal('fetch', spy);

    const wrapper = await mountComponent('/team-aurora/website/done');
    await flushPromises();
    await flushPromises();

    expect(keyPostCalls(spy)).toBe(0);
    expect(wrapper.find('[data-testid="done-key"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="done-key-error"]').exists()).toBe(false);
    // The rest of the screen is there as usual.
    expect(wrapper.find('[data-testid="done-address-link-site"]').exists()).toBe(true);

    wrapper.unmount();
  });

  it('creates exactly one key for secret-link visibility and shows the link once', async () => {
    backend.data.sites[0]!.access = { base: 'nobody', keys: true, invitees: false };
    backend.data.keys = backend.data.keys.filter((k) => k.siteSlug !== 'website');
    const spy = vi.fn(backend.fetch);
    vi.stubGlobal('fetch', spy);

    const wrapper = await mountFromPublish('team-aurora', 'website');
    await flushPromises();
    await flushPromises();

    expect(keyPostCalls(spy)).toBe(1);
    const banner = wrapper.find('[data-testid="done-key"]');
    expect(banner.exists()).toBe(true);
    expect(banner.attributes('supporting-text')).toContain('maar één keer');
    expect(banner.attributes('supporting-text')).toContain('Toegang');
    const link = wrapper.find('[data-testid="done-key-link"]');
    expect(link.text()).toContain('?key=');
    expect(link.text()).toContain('https://sites.plak.test/team-aurora/website/');

    wrapper.unmount();
  });

  it('copies the secret link via the same button as on the Toegang tab', async () => {
    backend.data.sites[0]!.access = { base: 'nobody', keys: true, invitees: false };
    backend.data.keys = backend.data.keys.filter((k) => k.siteSlug !== 'website');
    const write = writeClipboard();
    const wrapper = await mountFromPublish('team-aurora', 'website');
    await flushPromises();
    await flushPromises();

    await wrapper.find('[data-testid="done-key-copy"]').trigger('click');
    await flushPromises();

    expect(write).toHaveBeenCalled();
    expect(wrapper.find('[data-testid="done-key-notice"]').text()).toBe('Link gekopieerd.');

    wrapper.unmount();
  });

  it('shows the link without the code and the code itself next to the full link', async () => {
    backend.data.sites[0]!.access = { base: 'nobody', keys: true, invitees: false };
    backend.data.keys = backend.data.keys.filter((k) => k.siteSlug !== 'website');
    const wrapper = await mountFromPublish('team-aurora', 'website');
    await flushPromises();
    await flushPromises();

    const bare = wrapper.find('[data-testid="done-key-link-without-code"]');
    expect(bare.text()).toContain('https://sites.plak.test/team-aurora/website/?key=');
    const code = wrapper.find('[data-testid="done-key-code"]').text();
    expect(code).not.toBe('');
    expect(bare.text()).not.toContain(code);
    expect(wrapper.find('[data-testid="done-key-code-copy"]').exists()).toBe(true);

    wrapper.unmount();
  });

  it('creates no key when an active key already exists, not even after navigating back', async () => {
    backend.data.sites[0]!.access = { base: 'nobody', keys: true, invitees: false };
    // The seeded site already has an active key.
    const spy = vi.fn(backend.fetch);
    vi.stubGlobal('fetch', spy);

    const wrapper = await mountFromPublish('team-aurora', 'website');
    await flushPromises();
    await flushPromises();

    expect(keyPostCalls(spy)).toBe(0);
    expect(wrapper.find('[data-testid="done-key"]').exists()).toBe(false);

    // Simulate navigating away and back to the same site's Done screen.
    await router.push('/team-aurora/website');
    await router.push('/team-aurora/website/done');
    await flushPromises();
    await flushPromises();

    expect(keyPostCalls(spy)).toBe(0);

    wrapper.unmount();
  });

  it.each(['public', 'sso', 'site_team', 'nobody'] as const)(
    'creates no key when secret links are off (base %s)',
    async (base) => {
      backend.data.sites[0]!.access = { base, keys: false, invitees: false };
      const spy = vi.fn(backend.fetch);
      vi.stubGlobal('fetch', spy);

      const wrapper = await mountFromPublish('team-aurora', 'website');
      await flushPromises();
      await flushPromises();

      expect(keyPostCalls(spy)).toBe(0);
      expect(wrapper.find('[data-testid="done-key"]').exists()).toBe(false);

      wrapper.unmount();
    },
  );

  it("shows the API's own detail when key creation fails with a problem+json error", async () => {
    backend.data.sites[0]!.access = { base: 'nobody', keys: true, invitees: false };
    backend.data.keys = backend.data.keys.filter((k) => k.siteSlug !== 'website');
    const realFetch = backend.fetch;
    vi.stubGlobal('fetch', ((input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : (input as URL | Request).toString();
      if (url.includes('/keys') && init?.method === 'POST') {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              type: 'about:blank',
              title: 'Interne fout',
              status: 500,
              detail: 'De sleutel kon niet worden aangemaakt.',
            }),
            { status: 500, headers: { 'content-type': 'application/problem+json' } },
          ),
        );
      }
      return realFetch(input, init);
    }) as typeof fetch);

    const wrapper = await mountFromPublish('team-aurora', 'website');
    await flushPromises();
    await flushPromises();

    expect(wrapper.find('[data-testid="done-key-error"]').attributes('supporting-text')).toBe(
      'De sleutel kon niet worden aangemaakt.',
    );

    wrapper.unmount();
  });

  it("falls back to the problem's title when it carries no detail of its own", async () => {
    backend.data.sites[0]!.access = { base: 'nobody', keys: true, invitees: false };
    backend.data.keys = backend.data.keys.filter((k) => k.siteSlug !== 'website');
    const realFetch = backend.fetch;
    vi.stubGlobal('fetch', ((input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : (input as URL | Request).toString();
      if (url.includes('/keys') && init?.method === 'POST') {
        return Promise.resolve(
          new Response(
            JSON.stringify({ type: 'about:blank', title: 'Interne fout', status: 500 }),
            { status: 500, headers: { 'content-type': 'application/problem+json' } },
          ),
        );
      }
      return realFetch(input, init);
    }) as typeof fetch);

    const wrapper = await mountFromPublish('team-aurora', 'website');
    await flushPromises();
    await flushPromises();

    expect(wrapper.find('[data-testid="done-key-error"]').attributes('supporting-text')).toBe(
      'Interne fout',
    );

    wrapper.unmount();
  });

  it('shows the fallback option with a link to Toegang when creation fails', async () => {
    backend.data.sites[0]!.access = { base: 'nobody', keys: true, invitees: false };
    backend.data.keys = backend.data.keys.filter((k) => k.siteSlug !== 'website');
    const realFetch = backend.fetch;
    vi.stubGlobal('fetch', ((input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : (input as URL | Request).toString();
      if (url.includes('/keys') && init?.method === 'POST') {
        return Promise.reject(new Error('netwerk weg'));
      }
      return realFetch(input, init);
    }) as typeof fetch);

    const wrapper = await mountFromPublish('team-aurora', 'website');
    await flushPromises();
    await flushPromises();

    expect(wrapper.find('[data-testid="done-key"]').exists()).toBe(false);
    const fallback = wrapper.find('[data-testid="done-key-error"]');
    expect(fallback.exists()).toBe(true);
    expect(
      wrapper.find('[data-testid="done-key-to-access"]').attributes('href'),
    ).toBe('/team-aurora/website/access');

    wrapper.unmount();
  });
});
