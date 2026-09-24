import { mount } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { defineComponent, h } from 'vue';
import { createMemoryHistory, createRouter, RouterView, type Router } from 'vue-router';

import { makeMockBackend, MOCK_CONTENT_BASE, type MockBackend } from '@/api/mock';
import TabDeploy from '@/components/site/TabDeploy.vue';
import TabOverview from '@/components/site/TabOverview.vue';
import TabPreviews from '@/components/site/TabPreviews.vue';
import TabAccess from '@/components/site/TabAccess.vue';
import TabVersions from '@/components/site/TabVersions.vue';
import { serverErrorFetch, untilIdle, fireDetailEvent } from '@/components/site/testHelpers';
import { _resetCurrentMemberCache } from '@/composables/currentMember';
import { _resetBreadcrumbs, breadcrumbsFor } from '@/composables/breadcrumbs';
import Site from './Site.vue';

let backend: MockBackend;

beforeEach(() => {
  backend = makeMockBackend();
  vi.stubGlobal('fetch', backend.fetch);
  // Module-level session cache: every test starts with a fresh /me.
  _resetCurrentMemberCache();
  _resetBreadcrumbs();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

const Empty = defineComponent({ render: () => h('div', { 'data-testid': 'elders' }) });
const Host = defineComponent({ render: () => h(RouterView) });

// A route table of its own (the same shape as router.ts) so this test does not
// have to load the whole app router with every other page.
function makeRouter(): Router {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'overview', component: Empty },
      { path: '/:group', name: 'group', component: Empty },
      // Without groep and site in the route: the page should load nothing.
      { path: '/-/los', name: 'los', component: Site },
      {
        path: '/:group/:site',
        component: Site,
        children: [
          { path: '', name: 'site-overview', component: TabOverview },
          { path: 'previews', name: 'site-previews', component: TabPreviews },
          { path: 'versions', name: 'site-versions', component: TabVersions },
          { path: 'access', name: 'site-access', component: TabAccess },
          { path: 'deploy', name: 'site-deploy', component: TabDeploy },
        ],
      },
    ],
  });
}

async function makeWrapper(path: string) {
  const router = makeRouter();
  await router.push(path);
  await router.isReady();
  const wrapper = mount(Host, { global: { plugins: [router], stubs: { teleport: true } } });
  await untilIdle();
  return { wrapper, router };
}

describe('Site: structure', () => {
  it('shows title, visibility and the six tabs with clean hrefs', async () => {
    const { wrapper } = await makeWrapper('/nldd/website');

    expect(wrapper.find('h1').text()).toBe('NLDD website');
    expect(wrapper.find('[data-testid="zichtbaarheid-tag"]').attributes('text')).toBe('Publiek');

    const tabs = wrapper.findAll('nldd-tab-bar-item');
    expect(tabs.map((t) => t.attributes('text'))).toEqual([
      'Overzicht',
      'Previews',
      'Versies',
      'Toegang',
      'Leden',
      'Deploy',
    ]);
    expect(wrapper.find('[data-testid="tab-overzicht"]').attributes('current')).toBeDefined();
    expect(wrapper.find('[data-testid="tab-previews"]').attributes('href')).toBe(
      '/nldd/website/previews',
    );
    expect(wrapper.find('[data-testid="tab-overzicht"]').attributes('href')).toBe(
      '/nldd/website',
    );
  });

  it('opens the Overzicht tab by default', async () => {
    const { wrapper, router } = await makeWrapper('/nldd/website');

    expect(router.currentRoute.value.name).toBe('site-overview');
    expect(wrapper.text()).toContain('Status');
    expect(wrapper.text()).toContain('Gevarenzone');
  });

  it('shows a 404 message for an unknown site, without tabs', async () => {
    const { wrapper } = await makeWrapper('/nldd/bestaat-niet');

    expect(wrapper.html()).toContain('Onbekend site');
    expect(wrapper.find('[data-testid="site-tabs"]').exists()).toBe(false);
  });

  it('loads nothing without group and site in the route', async () => {
    const { wrapper } = await makeWrapper('/-/los');

    expect(wrapper.find('nldd-activity-indicator').attributes('text')).toBe('Site laden');
    expect(wrapper.find('[data-testid="site-tabs"]').exists()).toBe(false);
  });

  it('shows an error message on a server error', async () => {
    vi.stubGlobal('fetch', serverErrorFetch());
    const { wrapper } = await makeWrapper('/nldd/website');

    expect(wrapper.html()).toContain('Serverfout');
  });
});

describe('Site: tab navigation', () => {
  it('navigates per tab to the matching subpath', async () => {
    const { wrapper, router } = await makeWrapper('/nldd/website');

    await wrapper.find('[data-testid="tab-previews"]').trigger('click');
    await untilIdle();
    expect(router.currentRoute.value.name).toBe('site-previews');
    expect(router.currentRoute.value.path).toBe('/nldd/website/previews');
    expect(wrapper.find('[data-testid="preview-pr-42"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="tab-previews"]').attributes('current')).toBeDefined();
    expect(wrapper.find('[data-testid="tab-overzicht"]').attributes('current')).toBeUndefined();

    await wrapper.find('[data-testid="tab-toegang"]').trigger('click');
    await untilIdle();
    expect(router.currentRoute.value.path).toBe('/nldd/website/access');
    expect(wrapper.text()).toContain('Wie kan deze site bekijken?');

    await wrapper.find('[data-testid="tab-deploy"]').trigger('click');
    await untilIdle();
    expect(router.currentRoute.value.path).toBe('/nldd/website/deploy');
    expect(wrapper.text()).toContain('Publiceren vanuit GitHub of Forgejo');

    await wrapper.find('[data-testid="tab-versies"]').trigger('click');
    await untilIdle();
    expect(router.currentRoute.value.path).toBe('/nldd/website/versions');
    expect(wrapper.find('[data-testid="live-marker-versie-1"]').exists()).toBe(true);
  });

  it('also loads each tab directly via its URL', async () => {
    const { wrapper } = await makeWrapper('/nldd/website/access');

    expect(wrapper.text()).toContain('Wie kan deze site bekijken?');
    expect(wrapper.find('[data-testid="tab-toegang"]').attributes('current')).toBeDefined();
  });
});

describe('Site: content origin', () => {
  it('passes the content origin from /me to the tabs for shared links', async () => {
    const { wrapper, router } = await makeWrapper('/nldd/website');

    // Overview: public URL on the content host, not on the admin origin.
    const publicHref = wrapper.find('[data-testid="publieke-url"]').attributes('href');
    expect(publicHref).toBe(`${MOCK_CONTENT_BASE}/nldd/website/`);
    expect(publicHref).not.toContain(window.location.origin);

    // Versies: viewing sits in the row menu, so the URL only shows up
    // from what the action opens.
    const open = vi.fn();
    vi.stubGlobal('open', open);
    await router.push('/nldd/website/versions');
    await untilIdle();
    wrapper
      .find('[data-testid="bekijk-versie-0"]')
      .element.dispatchEvent(new CustomEvent('select'));
    expect(open).toHaveBeenCalledWith(
      `${MOCK_CONTENT_BASE}/nldd/website/_version/versie-0/`,
      '_blank',
      'noopener',
    );

    await router.push('/nldd/website/previews');
    await untilIdle();
    expect(
      wrapper.find('[data-testid="preview-pr-42"]').find('nldd-link').attributes('href'),
    ).toBe(`${MOCK_CONTENT_BASE}/nldd/website/_preview/pr-42/`);

    await router.push('/nldd/website/deploy');
    await untilIdle();
    // Deploy shows the CI audience (the admin origin), not the content host:
    // the workflow snippet uploads there, it does not link to the site itself.
    expect(wrapper.find('[data-testid="workflow-snippet"]').text()).not.toContain(
      MOCK_CONTENT_BASE,
    );
  });
});

describe('Site: breadcrumb path', () => {
  it('supplies the breadcrumb path to the app shell instead of putting it at the top itself', async () => {
    const { wrapper } = await makeWrapper('/nldd/website');

    expect(wrapper.find('nldd-breadcrumbs').exists()).toBe(false);
    expect(breadcrumbsFor('/nldd/website')).toEqual([
      { text: 'Overzicht', href: '/' },
      { text: 'NLDD', href: '/nldd' },
      { text: 'website' },
    ]);
  });

  it('puts the site name and the tab in the browser tab title', async () => {
    // Every route was called "Plak", so a row of tabs said nothing and a
    // screen reader reported no location after navigating (WCAG 2.4.2).
    const { router } = await makeWrapper('/nldd/website');
    expect(document.title).toBe('NLDD website - Plak');

    await router.push('/nldd/website/versions');
    await untilIdle();

    expect(document.title).toBe('NLDD website - Versies - Plak');
  });

  // The group slug is whatever the visitor's URL decoded to, and the trail is
  // set without waiting for the API, so a 404 group gets a crumb too.
  it.each(['//example.com', '/\\example.com', '/\\/example.com'])(
    'leaves the group crumb of %s without an href',
    async (slug) => {
      const { router } = await makeWrapper(`/${encodeURIComponent(slug)}/website`);

      const crumbs = breadcrumbsFor(router.currentRoute.value.path);
      expect(crumbs[1]).toEqual({ text: slug, href: undefined });
    },
  );

  it('moves the breadcrumb path along to the path of the open tab', async () => {
    const { router } = await makeWrapper('/nldd/website');

    await router.push('/nldd/website/versions');
    await untilIdle();

    expect(breadcrumbsFor('/nldd/website')).toEqual([]);
    expect(breadcrumbsFor('/nldd/website/versions')).toEqual([
      { text: 'Overzicht', href: '/' },
      { text: 'NLDD', href: '/nldd' },
      { text: 'website' },
    ]);
  });
});

describe('Site: deleting', () => {
  it('returns to the overview after deleting the site', async () => {
    const { wrapper, router } = await makeWrapper('/nldd/website');

    await wrapper.find('[data-testid="verwijder-site"]').trigger('click');
    await wrapper.find('[data-testid="bevestig-doorgaan"]').trigger('click');
    await untilIdle();

    expect(backend.data.sites).toHaveLength(0);
    expect(router.currentRoute.value.path).toBe('/');
  });
});

describe('Site: refreshing after a change', () => {
  it("leaves the tab's confirmation in place and doesn't rebuild the tab bar", async () => {
    const { wrapper } = await makeWrapper('/nldd/website');
    const bar = wrapper.find('[data-testid="site-tabs"]').element;

    fireDetailEvent(wrapper.find('[data-testid="upload-invoer"]').element, 'change', {
      files: [new File(['<html></html>'], 'dist.zip', { type: 'application/zip' })],
    });
    await untilIdle();
    await wrapper.find('[data-testid="upload-formulier"]').trigger('submit');
    await untilIdle();

    expect(wrapper.find('nldd-notification[text="Versie gepubliceerd"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="site-tabs"]').element).toBe(bar);
  });

  it("refreshes the page without swallowing the Toegang tab's notification", async () => {
    const { wrapper } = await makeWrapper('/nldd/website/access');
    const bar = wrapper.find('[data-testid="site-tabs"]').element;

    fireDetailEvent(wrapper.find('[data-testid="basis-site_team"]').element, 'change', {
      checked: true,
    });
    await untilIdle();

    // Access has recently moved to only the Overzicht tab, next to the
    // status; the heading no longer carries it.
    expect(wrapper.find('[data-testid="zichtbaarheid-tag"]').exists()).toBe(false);
    expect(wrapper.find('nldd-notification[text="Toegang opgeslagen"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="site-tabs"]').element).toBe(bar);
  });

  it('keeps the page in place when the refresh itself fails', async () => {
    const { wrapper } = await makeWrapper('/nldd/website');
    const bar = wrapper.find('[data-testid="site-tabs"]').element;

    vi.stubGlobal('fetch', serverErrorFetch());
    wrapper.findComponent(TabOverview).vm.$emit('changed');
    await untilIdle();

    expect(wrapper.find('[data-testid="site-tabs"]').element).toBe(bar);
    expect(wrapper.find('h1').text()).toBe('NLDD website');
  });
});

describe('Site: tab switch', () => {
  it("doesn't rebuild the site header when switching tabs", async () => {
    const { wrapper, router } = await makeWrapper('/nldd/website');
    const bar = wrapper.find('[data-testid="site-tabs"]').element;
    const title = wrapper.find('h1').element;

    await router.push('/nldd/website/versions');
    await untilIdle();

    expect(wrapper.find('[data-testid="site-tabs"]').element).toBe(bar);
    expect(wrapper.find('h1').element).toBe(title);
  });
});
