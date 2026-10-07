import { mount } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { defineComponent, h } from 'vue';
import { createMemoryHistory, createRouter, RouterView, type Router } from 'vue-router';

import { makeMockBackend, MOCK_CONTENT_BASE, type MockBackend } from '@/api/mock';
import TabDeploy from '@/components/site/TabDeploy.vue';
import TabOverview from '@/components/site/TabOverview.vue';
import TabPreviews from '@/components/site/TabPreviews.vue';
import TabAccess from '@/components/site/TabAccess.vue';
import TabSettings from '@/components/site/TabSettings.vue';
import TabVersions from '@/components/site/TabVersions.vue';
import {
  fakeTabBarLayout,
  fireDetailEvent,
  serverErrorFetch,
  stubFrames,
  untilIdle,
} from '@/components/site/testHelpers';
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

const Empty = defineComponent({ render: () => h('div', { 'data-testid': 'elsewhere' }) });
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
          { path: 'settings', name: 'site-settings', component: TabSettings },
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
  it('shows title, visibility and the seven tabs with clean hrefs', async () => {
    const { wrapper } = await makeWrapper('/team-aurora/website');

    expect(wrapper.find('h1').text()).toBe('Team Aurora website');
    expect(wrapper.find('[data-testid="visibility-tag"]').attributes('text')).toBe('Publiek');

    const tabs = wrapper.findAll('nldd-tab-bar-item');
    expect(tabs.map((t) => t.attributes('text'))).toEqual([
      'Overzicht',
      'Previews',
      'Versies',
      'Toegang',
      'Leden',
      'Deploy',
      'Instellingen',
    ]);
    expect(wrapper.find('[data-testid="tab-overview"]').attributes('current')).toBeDefined();
    expect(wrapper.find('[data-testid="tab-previews"]').attributes('href')).toBe(
      '/team-aurora/website/previews',
    );
    expect(wrapper.find('[data-testid="tab-settings"]').attributes('href')).toBe(
      '/team-aurora/website/settings',
    );
    expect(wrapper.find('[data-testid="tab-overview"]').attributes('href')).toBe(
      '/team-aurora/website',
    );
  });

  it('falls back to the slug for the heading when the site has no title', async () => {
    backend.data.sites.find((p) => p.slug === 'website')!.title = '';

    const { wrapper } = await makeWrapper('/team-aurora/website');

    expect(wrapper.find('h1').text()).toBe('website');
  });

  it('passes an empty content base to the tabs for an anonymous visitor', async () => {
    backend.data.loggedInMemberId = null;

    const { wrapper } = await makeWrapper('/team-aurora/website');

    // MOCK_CONTENT_BASE only rides along on a real session; without one the
    // live link falls back to a bare path instead of an empty-origin URL.
    const link = wrapper.find('[data-testid="public-url"]');
    expect(link.exists()).toBe(true);
    expect(link.attributes('href')).not.toContain(MOCK_CONTENT_BASE);
    expect(link.attributes('href')).toBe('/team-aurora/website/');
  });

  it('opens the Overzicht tab by default', async () => {
    const { wrapper, router } = await makeWrapper('/team-aurora/website');

    expect(router.currentRoute.value.name).toBe('site-overview');
    expect(wrapper.text()).toContain('Status');
    // Deleting the site moved to the Instellingen tab.
    expect(wrapper.text()).not.toContain('Gevarenzone');
  });

  it('shows a 404 message for an unknown site, without tabs', async () => {
    const { wrapper } = await makeWrapper('/team-aurora/bestaat-niet');

    expect(wrapper.html()).toContain('Onbekende site');
    expect(wrapper.find('[data-testid="site-tabs"]').exists()).toBe(false);
  });

  it('loads nothing without group and site in the route', async () => {
    const { wrapper } = await makeWrapper('/-/los');

    expect(wrapper.find('nldd-activity-indicator').attributes('text')).toBe('Site laden');
    expect(wrapper.find('[data-testid="site-tabs"]').exists()).toBe(false);
  });

  it('shows an error message on a server error', async () => {
    vi.stubGlobal('fetch', serverErrorFetch());
    const { wrapper } = await makeWrapper('/team-aurora/website');

    expect(wrapper.html()).toContain('Serverfout');
  });
});

describe('Site: tab navigation', () => {
  it('navigates per tab to the matching subpath', async () => {
    const { wrapper, router } = await makeWrapper('/team-aurora/website');

    await wrapper.find('[data-testid="tab-previews"]').trigger('click');
    await untilIdle();
    expect(router.currentRoute.value.name).toBe('site-previews');
    expect(router.currentRoute.value.path).toBe('/team-aurora/website/previews');
    expect(wrapper.find('[data-testid="preview-pr-42"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="tab-previews"]').attributes('current')).toBeDefined();
    expect(wrapper.find('[data-testid="tab-overview"]').attributes('current')).toBeUndefined();

    await wrapper.find('[data-testid="tab-access"]').trigger('click');
    await untilIdle();
    expect(router.currentRoute.value.path).toBe('/team-aurora/website/access');
    expect(wrapper.text()).toContain('Wie kan deze site bekijken?');

    await wrapper.find('[data-testid="tab-deploy"]').trigger('click');
    await untilIdle();
    expect(router.currentRoute.value.path).toBe('/team-aurora/website/deploy');
    expect(wrapper.text()).toContain('Publiceren vanuit GitHub of Forgejo');

    await wrapper.find('[data-testid="tab-versions"]').trigger('click');
    await untilIdle();
    expect(router.currentRoute.value.path).toBe('/team-aurora/website/versions');
    expect(wrapper.find('[data-testid="live-marker-versie-1"]').exists()).toBe(true);

    await wrapper.find('[data-testid="tab-settings"]').trigger('click');
    await untilIdle();
    expect(router.currentRoute.value.name).toBe('site-settings');
    expect(router.currentRoute.value.path).toBe('/team-aurora/website/settings');
    expect(wrapper.find('[data-testid="site-title"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="tab-settings"]').attributes('current')).toBeDefined();
  });

  it('gives every tab the reading width except deploy, which keeps it itself', async () => {
    const { wrapper, router } = await makeWrapper('/nldd/website');
    const section = () => wrapper.find('nldd-simple-section');
    expect(section().classes()).toContain('reading-width');

    await router.push('/nldd/website/deploy');
    await untilIdle();
    expect(section().classes()).not.toContain('reading-width');
  });

  it('also loads each tab directly via its URL', async () => {
    const { wrapper } = await makeWrapper('/team-aurora/website/access');

    expect(wrapper.text()).toContain('Wie kan deze site bekijken?');
    expect(wrapper.find('[data-testid="tab-access"]').attributes('current')).toBeDefined();
  });
});

describe('Site: tab bar', () => {
  // Seven tabs of 100 in a bar that shows 250.
  const LAYOUT = { barWidth: 250, tabWidth: 100 };

  function scroller(wrapper: ReturnType<typeof mount>): HTMLElement {
    return wrapper.find('.tabs-scroll').element as HTMLElement;
  }

  it('lets the bar scroll in a wrapper of its own instead of squeezing its labels', async () => {
    const { wrapper } = await makeWrapper('/team-aurora/website');

    const bar = wrapper.find('[data-testid="site-tabs"]');
    expect(bar.element.parentElement?.classList.contains('tabs-scroll')).toBe(true);
    expect(wrapper.findAll('.tabs-scroll')).toHaveLength(1);
  });

  it('brings the current tab into view when the page opens on it', async () => {
    const frames = stubFrames();
    const { wrapper } = await makeWrapper('/team-aurora/website/settings');
    fakeTabBarLayout(scroller(wrapper), LAYOUT);

    frames.run();

    // The seventh tab ends at 700 and the bar shows 250.
    expect(scroller(wrapper).scrollLeft).toBe(450);
  });

  it('follows the route to the next tab', async () => {
    const frames = stubFrames();
    const { wrapper, router } = await makeWrapper('/team-aurora/website/settings');
    fakeTabBarLayout(scroller(wrapper), LAYOUT);
    frames.run();

    await router.push('/team-aurora/website/versions');
    await untilIdle();
    frames.run();

    // The third tab spans 200 to 300, behind the 450 to 700 the bar showed.
    expect(scroller(wrapper).scrollLeft).toBe(200);
  });
});

describe('Site: content origin', () => {
  it('passes the content origin from /me to the tabs for shared links', async () => {
    const { wrapper, router } = await makeWrapper('/team-aurora/website');

    // Overview: public URL on the content host, not on the admin origin.
    const publicHref = wrapper.find('[data-testid="public-url"]').attributes('href');
    expect(publicHref).toBe(`${MOCK_CONTENT_BASE}/team-aurora/website/`);
    expect(publicHref).not.toContain(window.location.origin);

    // Versies: viewing sits in the row menu, so the URL only shows up
    // from what the action opens.
    const open = vi.fn();
    vi.stubGlobal('open', open);
    await router.push('/team-aurora/website/versions');
    await untilIdle();
    wrapper
      .find('[data-testid="view-versie-0"]')
      .element.dispatchEvent(new CustomEvent('select'));
    expect(open).toHaveBeenCalledWith(
      `${MOCK_CONTENT_BASE}/team-aurora/website/_version/versie-0/`,
      '_blank',
      'noopener',
    );

    await router.push('/team-aurora/website/previews');
    await untilIdle();
    expect(
      wrapper.find('[data-testid="preview-pr-42"]').find('nldd-link').attributes('href'),
    ).toBe(`${MOCK_CONTENT_BASE}/team-aurora/website/_preview/pr-42/`);

    await router.push('/team-aurora/website/deploy');
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
    const { wrapper } = await makeWrapper('/team-aurora/website');

    expect(wrapper.find('nldd-breadcrumbs').exists()).toBe(false);
    expect(breadcrumbsFor('/team-aurora/website')).toEqual([
      { text: 'Overzicht', href: '/' },
      { text: 'Team Aurora', href: '/team-aurora' },
      { text: 'website' },
    ]);
  });

  it('puts the site name and the tab in the browser tab title', async () => {
    // Every route was called "Plak", so a row of tabs said nothing and a
    // screen reader reported no location after navigating (WCAG 2.4.2).
    const { router } = await makeWrapper('/team-aurora/website');
    expect(document.title).toBe('Team Aurora website - Plak');

    await router.push('/team-aurora/website/versions');
    await untilIdle();

    expect(document.title).toBe('Team Aurora website - Versies - Plak');
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
    const { router } = await makeWrapper('/team-aurora/website');

    await router.push('/team-aurora/website/versions');
    await untilIdle();

    expect(breadcrumbsFor('/team-aurora/website')).toEqual([]);
    expect(breadcrumbsFor('/team-aurora/website/versions')).toEqual([
      { text: 'Overzicht', href: '/' },
      { text: 'Team Aurora', href: '/team-aurora' },
      { text: 'website' },
    ]);
  });
});

describe('Site: renaming', () => {
  async function rename(wrapper: ReturnType<typeof mount>, title: string): Promise<void> {
    fireDetailEvent(wrapper.find('[data-testid="site-title"]').element, 'input', { value: title });
    await wrapper.find('[data-testid="site-title-form"]').trigger('submit');
    await untilIdle();
  }

  it('shows the new title in the header and the browser tab, without rebuilding the page', async () => {
    const { wrapper } = await makeWrapper('/team-aurora/website/settings');
    const bar = wrapper.find('[data-testid="site-tabs"]').element;
    const title = wrapper.find('h1').element;

    await rename(wrapper, 'Documentatie');

    expect(backend.data.sites[0]!.title).toBe('Documentatie');
    expect(wrapper.find('h1').text()).toBe('Documentatie');
    expect(document.title).toBe('Documentatie - Instellingen - Plak');
    // The confirmation lives in the tab, which the refresh must leave alone.
    expect(wrapper.find('[data-testid="site-title-notice"]').text()).toBe(
      'Titel opgeslagen. De site heet nu Documentatie.',
    );
    expect(wrapper.find('[data-testid="site-tabs"]').element).toBe(bar);
    expect(wrapper.find('h1').element).toBe(title);
    expect(wrapper.find('[data-testid="site-title"]').attributes('value')).toBe('Documentatie');
  });

  it('leaves the breadcrumb on the address, which a title does not change', async () => {
    const { wrapper } = await makeWrapper('/team-aurora/website/settings');

    await rename(wrapper, 'Documentatie');

    expect(breadcrumbsFor('/team-aurora/website/settings')).toEqual([
      { text: 'Overzicht', href: '/' },
      { text: 'Team Aurora', href: '/team-aurora' },
      { text: 'website' },
    ]);
  });

  it('keeps the old header when the member is not allowed to rename', async () => {
    // lid-3 (Ada Vermeer) is editor in the group: the form is not offered.
    backend.data.loggedInMemberId = 'lid-3';
    const { wrapper } = await makeWrapper('/team-aurora/website/settings');

    expect(wrapper.find('[data-testid="site-title-form"]').exists()).toBe(false);
    expect(wrapper.find('h1').text()).toBe('Team Aurora website');
  });
});

describe('Site: deleting', () => {
  it('returns to the overview after deleting the site', async () => {
    const { wrapper, router } = await makeWrapper('/team-aurora/website/settings');

    await wrapper.find('[data-testid="delete-site"]').trigger('click');
    fireDetailEvent(wrapper.find('[data-testid="confirm-phrase"]').element, 'input', {
      value: 'team-aurora/website',
    });
    await wrapper.find('[data-testid="confirm-continue"]').trigger('click');
    await untilIdle();

    expect(backend.data.sites).toHaveLength(0);
    expect(router.currentRoute.value.path).toBe('/');
  });
});

describe('Site: refreshing after a change', () => {
  it("leaves the tab's confirmation in place and doesn't rebuild the tab bar", async () => {
    const { wrapper } = await makeWrapper('/team-aurora/website');
    const bar = wrapper.find('[data-testid="site-tabs"]').element;

    fireDetailEvent(wrapper.find('[data-testid="upload-input"]').element, 'change', {
      files: [new File(['<html></html>'], 'dist.zip', { type: 'application/zip' })],
    });
    await untilIdle();
    await wrapper.find('[data-testid="upload-form"]').trigger('submit');
    await untilIdle();

    expect(wrapper.find('nldd-notification[text="Versie gepubliceerd"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="site-tabs"]').element).toBe(bar);
  });

  it("refreshes the page without swallowing the Toegang tab's notification", async () => {
    const { wrapper } = await makeWrapper('/team-aurora/website/access');
    const bar = wrapper.find('[data-testid="site-tabs"]').element;

    fireDetailEvent(wrapper.find('[data-testid="base-site_team"]').element, 'change', {
      checked: true,
    });
    await untilIdle();

    // Access has recently moved to only the Overzicht tab, next to the
    // status; the heading no longer carries it.
    expect(wrapper.find('[data-testid="visibility-tag"]').exists()).toBe(false);
    expect(wrapper.find('nldd-notification[text="Toegang opgeslagen"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="site-tabs"]').element).toBe(bar);
  });

  it('keeps the page in place when the refresh itself fails', async () => {
    const { wrapper } = await makeWrapper('/team-aurora/website');
    const bar = wrapper.find('[data-testid="site-tabs"]').element;

    vi.stubGlobal('fetch', serverErrorFetch());
    wrapper.findComponent(TabOverview).vm.$emit('changed');
    await untilIdle();

    expect(wrapper.find('[data-testid="site-tabs"]').element).toBe(bar);
    expect(wrapper.find('h1').text()).toBe('Team Aurora website');
  });
});

describe('Site: tab switch', () => {
  it("doesn't rebuild the site header when switching tabs", async () => {
    const { wrapper, router } = await makeWrapper('/team-aurora/website');
    const bar = wrapper.find('[data-testid="site-tabs"]').element;
    const title = wrapper.find('h1').element;

    await router.push('/team-aurora/website/versions');
    await untilIdle();

    expect(wrapper.find('[data-testid="site-tabs"]').element).toBe(bar);
    expect(wrapper.find('h1').element).toBe(title);
  });
});
