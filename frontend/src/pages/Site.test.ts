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
import { titleAfterNavigation } from '@/router';
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

  it('points to the overview when the site is not found, in case it was renamed', async () => {
    const { wrapper } = await makeWrapper('/team-aurora/bestaat-niet');

    expect(wrapper.find('[data-testid="renamed-hint"]').text()).toBe(
      'Is de site of groep hernoemd? Zoek hem dan in je overzicht.',
    );
    const link = wrapper.find('[data-testid="renamed-overview"]');
    expect(link.attributes('text')).toBe('Naar het overzicht');
    expect(link.attributes('href')).toBe('/');
  });

  it('points to the overview when the group of the site is not found either', async () => {
    const { wrapper } = await makeWrapper('/onbekend/website');

    expect(wrapper.find('[data-testid="renamed-overview"]').exists()).toBe(true);
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

  it('suggests no rename for a server error: the site is there, it just does not load', async () => {
    vi.stubGlobal('fetch', serverErrorFetch());
    const { wrapper } = await makeWrapper('/team-aurora/website');

    expect(wrapper.find('[data-testid="renamed-hint"]').exists()).toBe(false);
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

describe('Site: changing the address', () => {
  const ADDRESS = 'section[aria-labelledby="heading-site-address"]';

  beforeEach(() => {
    // Today is the 8th of October 2026 in Amsterdam.
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date('2026-10-08T10:00:00Z'));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  async function changeAddress(wrapper: ReturnType<typeof mount>, slug: string): Promise<void> {
    fireDetailEvent(wrapper.find('[data-testid="site-address"]').element, 'input', { value: slug });
    await wrapper.find('[data-testid="site-address-form"]').trigger('submit');
    await untilIdle();
    await wrapper.find(`${ADDRESS} [data-testid="confirm-continue"]`).trigger('click');
    await untilIdle();
  }

  /** The dialog is gone: this is when the browser says so, and the status line speaks. */
  async function dialogClosed(wrapper: ReturnType<typeof mount>): Promise<void> {
    await wrapper.find(`${ADDRESS} nldd-modal-dialog`).trigger('close');
    await untilIdle();
  }

  it('moves the route to the new address on the same tab', async () => {
    const { wrapper, router } = await makeWrapper('/team-aurora/website/settings');

    await changeAddress(wrapper, 'handboek');

    expect(backend.data.sites[0]!.slug).toBe('handboek');
    expect(router.currentRoute.value.name).toBe('site-settings');
    expect(router.currentRoute.value.path).toBe('/team-aurora/handboek/settings');
    expect(wrapper.find('[data-testid="tab-settings"]').attributes('current')).toBeDefined();
  });

  it('leaves the tab where it is: nothing is rebuilt and nothing loads in front of it', async () => {
    const { wrapper } = await makeWrapper('/team-aurora/website/settings');
    const bar = wrapper.find('[data-testid="site-tabs"]').element;
    const section = wrapper.find(ADDRESS).element;
    const button = wrapper.find('[data-testid="site-address-change"]').element;
    const status = wrapper.find('[data-testid="site-address-notice"]').element;

    await changeAddress(wrapper, 'handboek');

    expect(wrapper.find('[data-testid="site-tabs"]').element).toBe(bar);
    expect(wrapper.find(ADDRESS).element).toBe(section);
    expect(wrapper.find('[data-testid="site-address-change"]').element).toBe(button);
    expect(wrapper.find('[data-testid="site-address-notice"]').element).toBe(status);
    expect(wrapper.find('nldd-activity-indicator[text="Site laden"]').exists()).toBe(false);
  });

  it('asks for nothing but the roles, and never lets the tab go inert while it moves', async () => {
    const { wrapper } = await makeWrapper('/team-aurora/website/settings');
    const indicator = wrapper.find('nldd-activity-indicator[text="Instellingen laden"]').element;
    // A loading indicator makes what it wraps inert: no focus, and a status
    // line that is not heard.
    const completeness: boolean[] = [];
    new MutationObserver(() => completeness.push(indicator.hasAttribute('complete'))).observe(
      indicator,
      { attributes: true, attributeFilter: ['complete'] },
    );
    const sent: string[] = [];
    vi.stubGlobal('fetch', (input: RequestInfo | URL, init?: RequestInit) => {
      sent.push(`${init?.method ?? 'GET'} ${new URL(String(input), 'http://plak.test').pathname}`);
      return backend.fetch(input, init);
    });

    await changeAddress(wrapper, 'handboek');

    expect(sent).toEqual(['PUT /-/api/v1/sites/team-aurora/website/slug', 'GET /-/api/v1/me']);
    expect(completeness).toEqual([]);
  });

  it('says what happened where the member is, and leaves the focus on the button', async () => {
    const { wrapper } = await makeWrapper('/team-aurora/website/settings');
    await changeAddress(wrapper, 'handboek');
    const focus = vi.spyOn(HTMLElement.prototype, 'focus');

    await dialogClosed(wrapper);

    expect(wrapper.find('[data-testid="site-address-notice"]').text()).toBe(
      'Het adres is gewijzigd. team-aurora/website stuurt tot en met 7 november 2026 door naar team-aurora/handboek. Gebruik je automatisch publiceren of plak publish? Pas het adres daar nu aan.',
    );
    expect(focus.mock.contexts).toEqual([
      wrapper.find('[data-testid="site-address-change"]').element,
    ]);
    focus.mockRestore();
  });

  it('shows the new address in the header, the crumbs, the tab bar and the section', async () => {
    const { wrapper } = await makeWrapper('/team-aurora/website/settings');

    await changeAddress(wrapper, 'handboek');

    expect(wrapper.find('nldd-title span[slot="subtitle"]').text()).toBe('team-aurora/handboek');
    expect(breadcrumbsFor('/team-aurora/handboek/settings')).toEqual([
      { text: 'Overzicht', href: '/' },
      { text: 'Team Aurora', href: '/team-aurora' },
      { text: 'handboek' },
    ]);
    expect(wrapper.find('[data-testid="tab-previews"]').attributes('href')).toBe(
      '/team-aurora/handboek/previews',
    );
    expect(wrapper.find('[data-testid="site-address-current"]').text()).toContain(
      `${MOCK_CONTENT_BASE}/team-aurora/handboek/`,
    );
    expect(wrapper.find('[data-testid="site-address-previous"]').text()).toContain(
      `${MOCK_CONTENT_BASE}/team-aurora/website/ stuurt door tot en met 7 november 2026.`,
    );
    expect(document.title).toBe('Team Aurora website - Instellingen - Plak');
  });

  it('keeps the title in the header and the page title: only the address has changed', async () => {
    const { wrapper } = await makeWrapper('/team-aurora/website/settings');

    await changeAddress(wrapper, 'handboek');

    expect(wrapper.find('h1').text()).toBe('Team Aurora website');
  });

  it('keeps the title of the browser tab after the router has put the new address in it', async () => {
    const { wrapper, router } = await makeWrapper('/team-aurora/website/settings');
    // As the app does on every navigation, before the page has had its say.
    router.afterEach(titleAfterNavigation);

    await changeAddress(wrapper, 'handboek');

    expect(document.title).toBe('Team Aurora website - Instellingen - Plak');
  });

  it('takes the roles of the member along: an admin of this site alone stays one', async () => {
    // lid-4 (Zoë de Wit) is a reader in the group and admin of this site, by a role named after its address.
    backend.data.loggedInMemberId = 'lid-4';
    const { wrapper } = await makeWrapper('/team-aurora/website/settings');
    const form = wrapper.find('[data-testid="site-address-form"]').element;
    const title = wrapper.find('[data-testid="site-title-form"]').element;

    await changeAddress(wrapper, 'handboek');

    expect(wrapper.find('[data-testid="site-address-form"]').element).toBe(form);
    expect(wrapper.find('[data-testid="site-title-form"]').element).toBe(title);
    expect(wrapper.find('[data-testid="delete-site"]').exists()).toBe(true);
  });

  it('can change the address once more straight away, from the address it has now', async () => {
    const { wrapper, router } = await makeWrapper('/team-aurora/website/settings');
    await changeAddress(wrapper, 'handboek');
    await dialogClosed(wrapper);

    await changeAddress(wrapper, 'gids');

    expect(backend.data.sites[0]!.slug).toBe('gids');
    expect(router.currentRoute.value.path).toBe('/team-aurora/gids/settings');
    expect(backend.data.sites[0]!.previousSlugs.map((p) => p.slug)).toEqual(['handboek', 'website']);
  });

  it('changes an address back from the list of the old ones', async () => {
    const { wrapper, router } = await makeWrapper('/team-aurora/website/settings');
    await changeAddress(wrapper, 'handboek');
    await dialogClosed(wrapper);

    await wrapper.find('[data-testid="site-address-restore-website"]').trigger('click');
    await untilIdle();
    await wrapper.find(`${ADDRESS} [data-testid="confirm-continue"]`).trigger('click');
    await untilIdle();

    expect(backend.data.sites[0]!.slug).toBe('website');
    expect(router.currentRoute.value.path).toBe('/team-aurora/website/settings');
    expect(wrapper.find('[data-testid="site-address-previous"]').text()).toContain('handboek');
  });

  it('tells the Deploy tab that workflows with the old address no longer publish', async () => {
    const { wrapper, router } = await makeWrapper('/team-aurora/website/settings');
    await changeAddress(wrapper, 'handboek');

    await router.push('/team-aurora/handboek/deploy');
    await untilIdle();

    expect(wrapper.find('[data-testid="deploy-moved"] nldd-rich-text').text()).toBe(
      'Workflows met site: team-aurora/website publiceren niet meer; gebruik site: team-aurora/handboek en het site-ID.',
    );
  });

  it('shows the Deploy tab of a site that has an old address from before the page opened', async () => {
    backend.data.sites[0]!.previousSlugs = [
      { slug: 'oude-naam', redirectsUntil: '2099-11-06T23:00:00Z' },
    ];

    const { wrapper } = await makeWrapper('/team-aurora/website/deploy');

    expect(wrapper.find('[data-testid="deploy-moved"] nldd-rich-text').text()).toContain(
      'site: team-aurora/oude-naam',
    );
  });

  it('has no such notice on the Deploy tab of a site that kept its address', async () => {
    const { wrapper } = await makeWrapper('/team-aurora/website/deploy');

    expect(wrapper.find('[data-testid="deploy-moved"]').exists()).toBe(false);
  });

  it('leaves the other sites of the group as they are', async () => {
    backend.data.sites.push({ ...backend.data.sites[0]!, id: 'tweede-id', slug: 'tweede', title: 'Tweede site' });
    const { wrapper, router } = await makeWrapper('/team-aurora/website/settings');

    await changeAddress(wrapper, 'handboek');
    await router.push('/team-aurora/tweede/settings');
    await untilIdle();

    expect(wrapper.find('h1').text()).toBe('Tweede site');
    expect(wrapper.find('[data-testid="site-address-current"]').text()).toContain('/team-aurora/tweede/');
  });

  it('loads the page again for any other address, so the old one finds nothing', async () => {
    const { wrapper, router } = await makeWrapper('/team-aurora/website/settings');
    await changeAddress(wrapper, 'handboek');

    // Back to the old address, as the back button does: the API follows no old address.
    await router.push('/team-aurora/website/settings');
    await untilIdle();
    expect(wrapper.html()).toContain('Onbekende site');
    expect(wrapper.find('[data-testid="site-tabs"]').exists()).toBe(false);

    // And forward again to the new one: a page that loaded in between does load again.
    await router.push('/team-aurora/handboek/settings');
    await untilIdle();
    expect(wrapper.find('[data-testid="site-tabs"]').exists()).toBe(true);
    expect(wrapper.find('nldd-title span[slot="subtitle"]').text()).toBe('team-aurora/handboek');
  });

  it('moves on even when the roles cannot be fetched again, and the member can go on from there', async () => {
    const { wrapper, router } = await makeWrapper('/team-aurora/website/settings');
    typeAddress(wrapper, 'handboek');
    await wrapper.find('[data-testid="site-address-form"]').trigger('submit');
    await untilIdle();
    const form = wrapper.find('[data-testid="site-address-form"]').element;
    vi.stubGlobal('fetch', (input: RequestInfo | URL, init?: RequestInit) =>
      String(input).endsWith('/me')
        ? Promise.reject(new TypeError('network down'))
        : backend.fetch(input, init),
    );
    const focus = vi.spyOn(HTMLElement.prototype, 'focus');

    await wrapper.find(`${ADDRESS} [data-testid="confirm-continue"]`).trigger('click');
    await untilIdle();
    await dialogClosed(wrapper);

    expect(backend.data.sites[0]!.slug).toBe('handboek');
    expect(router.currentRoute.value.path).toBe('/team-aurora/handboek/settings');
    // The roles the tab was given when it opened still hold: the form is still there.
    expect(wrapper.find('[data-testid="site-address-form"]').element).toBe(form);
    expect(wrapper.find('[data-testid="site-address-notice"]').text()).toContain(
      'Het adres is gewijzigd.',
    );
    expect(focus.mock.contexts).toEqual([
      wrapper.find('[data-testid="site-address-change"]').element,
    ]);
    focus.mockRestore();
  });

  function typeAddress(wrapper: ReturnType<typeof mount>, slug: string): void {
    fireDetailEvent(wrapper.find('[data-testid="site-address"]').element, 'input', { value: slug });
  }
});

describe('Site: deleting', () => {
  it('returns to the overview after deleting the site', async () => {
    const { wrapper, router } = await makeWrapper('/team-aurora/website/settings');

    await wrapper.find('[data-testid="delete-site"]').trigger('click');
    fireDetailEvent(wrapper.find('[data-testid="confirm-phrase"]').element, 'input', {
      value: 'team-aurora/website',
    });
    // The address section has a dialog of its own, earlier on the page.
    await wrapper
      .find('section[aria-labelledby="heading-danger-zone"] [data-testid="confirm-continue"]')
      .trigger('click');
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
