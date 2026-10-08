import { mount } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { defineComponent, h } from 'vue';
import { createMemoryHistory, createRouter, RouterView, type Router } from 'vue-router';

import { makeMockBackend, MOCK_CONTENT_BASE, type MockBackend } from '@/api/mock';
import TabSettings from '@/components/group/TabSettings.vue';
import TabMembers from '@/components/group/TabMembers.vue';
import TabSites from '@/components/group/TabSites.vue';
import PublishSheet from '@/components/PublishSheet.vue';
import {
  fakeTabBarLayout,
  fireDetailEvent,
  serverErrorFetch,
  stubFrames,
  untilIdle,
} from '@/components/site/testHelpers';
import { _resetCurrentMemberCache } from '@/composables/currentMember';
import { _resetBreadcrumbs, breadcrumbsFor } from '@/composables/breadcrumbs';
import { _resetAddActions, useAddActions } from '@/composables/addActions';
import { takePublishedMark } from '@/composables/publishedMark';

import { titleAfterNavigation } from '@/router';
import Group from './Group.vue';

let backend: MockBackend;

const Empty = defineComponent({ render: () => h('div', { 'data-testid': 'elsewhere' }) });
const Host = defineComponent({ render: () => h(RouterView) });

// A route table of its own (the same shape as router.ts) so this test does not
// have to load the whole app router with every other page.
function makeRouter(): Router {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'overview', component: Empty },
      { path: '/:group/:site/done', name: 'site-done', component: Empty },
      {
        path: '/:group',
        component: Group,
        children: [
          { path: '', name: 'group-sites', component: TabSites },
          { path: '-/members', name: 'group-members', component: TabMembers },
          { path: '-/settings', name: 'group-settings', component: TabSettings },
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

/** The sheets on the page; only "Zet een site online" belongs there. */
function sheetLabels(wrapper: ReturnType<typeof mount>): string[] {
  return wrapper.findAll('nldd-sheet').map((s) => s.attributes('accessible-label') ?? '');
}

/** Picks a file in the publish sheet, the way nldd-file-field reports it. */
function chooseFile(wrapper: ReturnType<typeof mount>, name: string): void {
  const file = new File(['<h1>hoi</h1>'], name, { type: 'text/html' });
  fireDetailEvent(
    wrapper.find('nldd-sheet[accessible-label="Zet een site online"] nldd-file-field').element,
    'change',
    { files: [file] },
  );
}

/**
 * Runs an action from a row menu. A menu item fires `select`, not `click`; the
 * testid carries the identifier, so it points at one row on its own.
 */
async function runRowAction(wrapper: ReturnType<typeof mount>, testid: string): Promise<void> {
  const item = wrapper.find(`[data-testid="${testid}"]`);
  expect(item.exists(), `action "${testid}" is missing`).toBe(true);
  item.element.dispatchEvent(new CustomEvent('select'));
  await untilIdle();
}

/** The role as the row of this member shows it. */
function roleOf(wrapper: ReturnType<typeof mount>, identifier: string): string | undefined {
  const row = wrapper
    .findAll('nldd-table-row:not([slot="header"])')
    .find((candidate) => candidate.html().includes(identifier));
  return row?.findAll('nldd-text-cell')[1]?.attributes('text');
}

beforeEach(() => {
  backend = makeMockBackend();
  vi.stubGlobal('fetch', backend.fetch);
  _resetBreadcrumbs();
  _resetAddActions();
  _resetCurrentMemberCache();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('Group: layout', () => {
  it('shows the group name and the three tabs with clean hrefs', async () => {
    const { wrapper } = await makeWrapper('/team-aurora');

    expect(wrapper.find('h1').text()).toBe('Team Aurora');

    const tabs = wrapper.findAll('nldd-tab-bar-item');
    expect(tabs.map((t) => t.attributes('text'))).toEqual(['Sites', 'Leden', 'Instellingen']);
    expect(wrapper.find('[data-testid="tab-sites"]').attributes('href')).toBe('/team-aurora');
    expect(wrapper.find('[data-testid="tab-members"]').attributes('href')).toBe(
      '/team-aurora/-/members',
    );
    expect(wrapper.find('[data-testid="tab-settings"]').attributes('href')).toBe(
      '/team-aurora/-/settings',
    );
    expect(wrapper.find('[data-testid="tab-sites"]').attributes('current')).toBeDefined();
  });

  it('opens the Sites tab by default', async () => {
    const { wrapper, router } = await makeWrapper('/team-aurora');

    expect(router.currentRoute.value.name).toBe('group-sites');
    // This wrapper mounts detached, so the custom element never upgrades and
    // Vue leaves :href as a plain attribute rather than a property.
    const link = wrapper.find('[data-testid="site-website"]').find('nldd-link');
    expect(link.attributes('href')).toBe('/team-aurora/website');
    expect(link.text()).toBe('Team Aurora website');
  });

  it('builds the heading structure with nested h-elements in nldd-title', async () => {
    const { wrapper, router } = await makeWrapper('/team-aurora');

    expect(wrapper.find('nldd-title h1').text()).toBe('Team Aurora');
    // Only the open tab's h2s, never those of another tab.
    for (const [path, expected] of [
      ['/team-aurora', ['Sites']],
      ['/team-aurora/-/members', ['Leden']],
      // The logged-in member is group admin, so the danger zone is there too.
      [
        '/team-aurora/-/settings',
        [
          'Naam van de groep',
          'Adres van de groep',
          'Standaardtoegang voor nieuwe sites',
          'Gevarenzone',
        ],
      ],
    ] as const) {
      await router.push(path);
      await untilIdle();
      const headings = wrapper
        .findAll('nldd-title h2')
        // The publish sheet stands ready on every tab and carries its own h2.
        .filter((h) => !h.element.closest('nldd-sheet'))
        .map((h) => h.text());
      expect(headings).toEqual(expected);
    }
  });

  it('shows an error banner for an unknown group, without tabs', async () => {
    const { wrapper } = await makeWrapper('/onbekend');

    const banner = wrapper.find('nldd-banner');
    expect(banner.exists()).toBe(true);
    expect(banner.attributes('text')).toBe('Onbekende groep');
    expect(wrapper.find('[data-testid="group-tabs"]').exists()).toBe(false);
  });

  it('points to the overview when the group is not found, in case it was renamed', async () => {
    const { wrapper } = await makeWrapper('/onbekend');

    expect(wrapper.find('[data-testid="renamed-hint"]').text()).toBe(
      'Is de site of groep hernoemd? Zoek hem dan in je overzicht.',
    );
    const link = wrapper.find('[data-testid="renamed-overview"]');
    expect(link.attributes('text')).toBe('Naar het overzicht');
    expect(link.attributes('href')).toBe('/');
  });

  it('suggests no rename for a server error', async () => {
    vi.stubGlobal('fetch', serverErrorFetch());
    const { wrapper } = await makeWrapper('/team-aurora');

    expect(wrapper.find('nldd-banner').exists()).toBe(true);
    expect(wrapper.find('[data-testid="renamed-hint"]').exists()).toBe(false);
  });
});

describe('Group: tab navigation', () => {
  it('navigates per tab to the corresponding subpath', async () => {
    const { wrapper, router } = await makeWrapper('/team-aurora');

    await wrapper.find('[data-testid="tab-members"]').trigger('click');
    await untilIdle();
    expect(router.currentRoute.value.name).toBe('group-members');
    expect(router.currentRoute.value.path).toBe('/team-aurora/-/members');
    expect(wrapper.find('[data-testid="members-list"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="tab-members"]').attributes('current')).toBeDefined();
    expect(wrapper.find('[data-testid="tab-sites"]').attributes('current')).toBeUndefined();

    await wrapper.find('[data-testid="tab-settings"]').trigger('click');
    await untilIdle();
    expect(router.currentRoute.value.path).toBe('/team-aurora/-/settings');
    expect(wrapper.find('[data-testid="default-access-group"]').exists()).toBe(true);

    await wrapper.find('[data-testid="tab-sites"]').trigger('click');
    await untilIdle();
    expect(router.currentRoute.value.path).toBe('/team-aurora');
    expect(wrapper.find('[data-testid="site-website"]').exists()).toBe(true);
  });

  it('also loads each tab directly via its URL', async () => {
    const { wrapper } = await makeWrapper('/team-aurora/-/settings');

    expect(wrapper.find('[data-testid="default-access-public"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="tab-settings"]').attributes('current')).toBeDefined();
  });

  it('keeps "Zet een site online" in the header on every tab', async () => {
    const { wrapper, router } = await makeWrapper('/team-aurora');

    for (const path of ['/team-aurora', '/team-aurora/-/members', '/team-aurora/-/settings']) {
      await router.push(path);
      await untilIdle();
      expect(wrapper.find('[data-testid="group-publish"]').attributes('text')).toBe(
        'Zet een site online',
      );
    }
  });

  it('gives the site table the work width and the other tabs the reading width', async () => {
    const { wrapper, router } = await makeWrapper('/nldd');
    const section = () => wrapper.find('nldd-simple-section');
    expect(section().classes()).not.toContain('reading-width');

    for (const path of ['/nldd/-/members', '/nldd/-/settings']) {
      await router.push(path);
      await untilIdle();
      expect(section().classes()).toContain('reading-width');
    }
  });

  it('does not rebuild the group header when switching tabs', async () => {
    const { wrapper, router } = await makeWrapper('/team-aurora');
    const bar = wrapper.find('[data-testid="group-tabs"]').element;
    const title = wrapper.find('h1').element;

    await router.push('/team-aurora/-/members');
    await untilIdle();

    expect(wrapper.find('[data-testid="group-tabs"]').element).toBe(bar);
    expect(wrapper.find('h1').element).toBe(title);
  });
});

describe('Group: tab bar', () => {
  // Three tabs of 100 in a bar that shows 150.
  const LAYOUT = { barWidth: 150, tabWidth: 100 };

  function scroller(wrapper: ReturnType<typeof mount>): HTMLElement {
    return wrapper.find('.tabs-scroll').element as HTMLElement;
  }

  it('lets the bar scroll in a wrapper of its own instead of squeezing its labels', async () => {
    const { wrapper } = await makeWrapper('/team-aurora');

    const bar = wrapper.find('[data-testid="group-tabs"]');
    expect(bar.element.parentElement?.classList.contains('tabs-scroll')).toBe(true);
    expect(wrapper.findAll('.tabs-scroll')).toHaveLength(1);
  });

  it('brings the current tab into view when the page opens on it', async () => {
    const frames = stubFrames();
    const { wrapper } = await makeWrapper('/team-aurora/-/settings');
    fakeTabBarLayout(scroller(wrapper), LAYOUT);

    frames.run();

    // The third tab ends at 300 and the bar shows 150.
    expect(scroller(wrapper).scrollLeft).toBe(150);
  });

  it('follows the route to the next tab', async () => {
    const frames = stubFrames();
    const { wrapper, router } = await makeWrapper('/team-aurora/-/settings');
    fakeTabBarLayout(scroller(wrapper), LAYOUT);
    frames.run();

    await router.push('/team-aurora/-/members');
    await untilIdle();
    frames.run();

    // The second tab spans 100 to 200, behind the 150 to 300 the bar showed.
    expect(scroller(wrapper).scrollLeft).toBe(100);
  });
});

describe('Group: put a site online', () => {
  it('names the button after the task, not after the data model', async () => {
    const { wrapper } = await makeWrapper('/team-aurora');

    expect(wrapper.find('[data-testid="group-publish"]').attributes('text')).toBe(
      'Zet een site online',
    );
    expect(wrapper.html()).not.toContain('Nieuw site');
  });

  it('shows the button for an editor of the group', async () => {
    // lid-3 (Ada Vermeer) is editor on team-aurora, not a platform admin.
    backend.data.loggedInMemberId = 'lid-3';
    const { wrapper } = await makeWrapper('/team-aurora');

    expect(wrapper.find('[data-testid="group-publish"]').exists()).toBe(true);
  });

  it('hides the button for someone with only the reader role in the group', async () => {
    // lid-4 (Zoë de Wit) is reader on team-aurora: may not create a site.
    backend.data.loggedInMemberId = 'lid-4';
    const { wrapper } = await makeWrapper('/team-aurora');

    expect(wrapper.find('[data-testid="group-publish"]').exists()).toBe(false);
    // Do not keep a dead button under another name, and otherwise a quiet
    // heading.
    expect(wrapper.html()).not.toContain('Nieuw site');
  });

  it('does not open the sheet via the toolbar for someone without an editor or admin role', async () => {
    backend.data.loggedInMemberId = 'lid-4';
    const { wrapper } = await makeWrapper('/team-aurora');

    useAddActions().requestNewSite();
    await untilIdle();

    expect(wrapper.findComponent(PublishSheet).props('open')).toBe(false);
  });

  describe('dragging', () => {
    /** Enough of DataTransfer for `resolveDroppedFile`. */
    function fakeDataTransfer(files: File[]): DataTransfer {
      return {
        types: files.length ? ['Files'] : [],
        files,
        items: files.map(() => ({ kind: 'file', webkitGetAsEntry: () => ({ isDirectory: false }) })),
      } as unknown as DataTransfer;
    }

    function dragEvent(type: string, dt: DataTransfer): Event {
      const event = new Event(type, { bubbles: true, cancelable: true });
      Object.defineProperty(event, 'dataTransfer', { value: dt });
      return event;
    }

    function droppableFile(): File {
      return new File(['<h1>hoi</h1>'], 'site.zip', { type: 'application/zip' });
    }

    it('opens the sheet with the group preselected as soon as a file is dropped', async () => {
      const { wrapper } = await makeWrapper('/team-aurora');

      wrapper.find('nldd-simple-section').element.dispatchEvent(
        dragEvent('drop', fakeDataTransfer([droppableFile()])),
      );
      await untilIdle();

      const sheet = wrapper.findComponent(PublishSheet);
      expect(sheet.props('open')).toBe(true);
      expect(sheet.props('initialFile')).toBeInstanceOf(File);
      // Only this group is ever offered here: no picker to preselect within.
      expect(sheet.props('groups')).toEqual([
        {
          slug: 'team-aurora',
          name: 'Team Aurora',
          defaultAccess: expect.anything(),
          previousSlugs: [],
        },
      ]);
    });

    it('does nothing for someone without an editor or admin role', async () => {
      backend.data.loggedInMemberId = 'lid-4';
      const { wrapper } = await makeWrapper('/team-aurora');

      const section = wrapper.find('nldd-simple-section').element;
      section.dispatchEvent(dragEvent('dragenter', fakeDataTransfer([droppableFile()])));
      await untilIdle();
      expect(wrapper.find('[data-testid="group-drag-active"]').exists()).toBe(false);

      section.dispatchEvent(dragEvent('drop', fakeDataTransfer([droppableFile()])));
      await untilIdle();
      expect(wrapper.findComponent(PublishSheet).props('open')).toBe(false);
    });

    it('shows the drag state on dragenter and hides it again on dragleave', async () => {
      const { wrapper } = await makeWrapper('/team-aurora');

      const section = wrapper.find('nldd-simple-section').element;
      section.dispatchEvent(dragEvent('dragenter', fakeDataTransfer([droppableFile()])));
      await untilIdle();
      expect(wrapper.find('[data-testid="group-drag-active"]').exists()).toBe(true);

      section.dispatchEvent(dragEvent('dragleave', fakeDataTransfer([droppableFile()])));
      await untilIdle();
      expect(wrapper.find('[data-testid="group-drag-active"]').exists()).toBe(false);
    });

    it('prevents the browser default while dragging over the target', async () => {
      const { wrapper } = await makeWrapper('/team-aurora');

      const section = wrapper.find('nldd-simple-section').element;
      const event = dragEvent('dragover', fakeDataTransfer([droppableFile()]));
      section.dispatchEvent(event);

      expect(event.defaultPrevented).toBe(true);
    });

    it('does not intercept dragover for someone without an editor or admin role', async () => {
      backend.data.loggedInMemberId = 'lid-4';
      const { wrapper } = await makeWrapper('/team-aurora');

      const section = wrapper.find('nldd-simple-section').element;
      const event = dragEvent('dragover', fakeDataTransfer([droppableFile()]));
      section.dispatchEvent(event);

      expect(event.defaultPrevented).toBe(false);
    });

    it('does not intercept dragleave for someone without an editor or admin role', async () => {
      backend.data.loggedInMemberId = 'lid-4';
      const { wrapper } = await makeWrapper('/team-aurora');

      const section = wrapper.find('nldd-simple-section').element;
      section.dispatchEvent(dragEvent('dragenter', fakeDataTransfer([droppableFile()])));
      section.dispatchEvent(dragEvent('dragleave', fakeDataTransfer([droppableFile()])));
      await untilIdle();

      expect(wrapper.find('[data-testid="group-drag-active"]').exists()).toBe(false);
    });

    it('shows a dismissible error for a dropped file of an unsupported type', async () => {
      const { wrapper } = await makeWrapper('/team-aurora');
      const badFile = new File(['hoi'], 'site.pdf', { type: 'application/pdf' });

      wrapper
        .find('nldd-simple-section')
        .element.dispatchEvent(dragEvent('drop', fakeDataTransfer([badFile])));
      await untilIdle();

      const banner = wrapper.find('[data-testid="group-drag-error"]');
      expect(banner.exists()).toBe(true);
      expect(banner.attributes('text')).toContain('Sleep één bestand');
      expect(wrapper.findComponent(PublishSheet).props('open')).toBe(false);

      banner.element.dispatchEvent(new CustomEvent('dismiss'));
      await untilIdle();

      expect(wrapper.find('[data-testid="group-drag-error"]').exists()).toBe(false);
    });

    it('ignores a drop that carries nothing file-shaped, such as dragged text', async () => {
      const { wrapper } = await makeWrapper('/team-aurora');

      wrapper
        .find('nldd-simple-section')
        .element.dispatchEvent(dragEvent('drop', fakeDataTransfer([])));
      await untilIdle();

      expect(wrapper.find('[data-testid="group-drag-error"]').exists()).toBe(false);
      expect(wrapper.findComponent(PublishSheet).props('open')).toBe(false);
    });
  });

  it('puts a site online from the sheet and takes the user to the result', async () => {
    const { wrapper, router } = await makeWrapper('/team-aurora');

    await wrapper.find('[data-testid="group-publish"]').trigger('click');
    await untilIdle();

    // The group is known, so the sheet does not ask for it; the address is
    // there, built from the content origin out of /me.
    const sheet = wrapper.find('nldd-sheet[accessible-label="Zet een site online"]');
    expect(sheet.find('nldd-dropdown').exists()).toBe(false);

    chooseFile(wrapper, 'documentatie.zip');
    await untilIdle();
    expect(wrapper.find('[data-testid="publish-address"]').text()).toContain(
      'https://sites.plak.test/team-aurora/documentatie/',
    );

    await sheet.find('nldd-form').trigger('submit');
    await untilIdle();

    const created = backend.data.sites.find((p) => p.slug === 'documentatie');
    expect(created?.title).toBe('Documentatie');
    // Created and published at once: a version, not an empty site.
    expect(
      backend.data.versions.some((v) => v.siteSlug === 'documentatie' && v.target === 'live'),
    ).toBe(true);
    expect(router.currentRoute.value.fullPath).toBe('/team-aurora/documentatie/done');
    // Marked as the publish flow, so the result screen may make a secret link.
    expect(takePublishedMark(router)).toBe(true);
  });

  it('puts a created site on the Sites tab immediately', async () => {
    const { wrapper } = await makeWrapper('/team-aurora');

    // Publishing itself navigates to the result screen; this is the
    // intermediate step that updates the group, apart from that navigation.
    wrapper.findComponent(PublishSheet).vm.$emit('created', {
      groupSlug: 'team-aurora',
      slug: 'documentatie',
      title: 'Documentatie',
      access: { base: 'public', keys: false, invitees: false },
      liveVersionId: null,
      createdBy: 'dev-beheerder',
      hasLiveVersion: false,
      lastPublishedAt: null,
      previewCount: 0,
    });
    await untilIdle();

    // The title sits as a link in the title cell, not as an attribute on it.
    const row = wrapper.find('[data-testid="site-documentatie"]');
    expect(row.exists()).toBe(true);
    expect(row.find('nldd-link').text()).toBe('Documentatie');
  });

  it('shows the path instead of an address with an empty host when there is no session', async () => {
    backend.data.loggedInMemberId = null;
    const { wrapper } = await makeWrapper('/team-aurora');

    await wrapper.find('[data-testid="group-publish"]').trigger('click');
    chooseFile(wrapper, 'documentatie.zip');
    await untilIdle();

    expect(wrapper.find('[data-testid="publish-address"]').text()).toContain('/team-aurora/documentatie/');
    expect(wrapper.find('[data-testid="publish-address"]').text()).not.toContain('https://');

    wrapper.unmount();
  });

  it('loads the group even when the session itself fails unexpectedly', async () => {
    const realFetch = backend.fetch;
    vi.stubGlobal('fetch', ((input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : (input as URL | Request).toString();
      if (url.includes('/me') && (init?.method ?? 'GET') === 'GET') {
        return Promise.reject(new TypeError('netwerkfout'));
      }
      return realFetch(input, init);
    }) as typeof fetch);

    const { wrapper } = await makeWrapper('/team-aurora');

    // The group loaded regardless: only the content host in the address
    // dropped out, not the whole page.
    expect(wrapper.find('h1').text()).toBe('Team Aurora');
    expect(wrapper.find('[data-testid="group-publish"]').exists()).toBe(true);
  });

  it('handles the request from the toolbar here, on every tab', async () => {
    const { wrapper, router } = await makeWrapper('/team-aurora/-/members');

    useAddActions().requestNewSite();
    await untilIdle();

    expect(wrapper.findAll('nldd-sheet[accessible-label="Zet een site online"]')).toHaveLength(1);
    expect(router.currentRoute.value.fullPath).toBe('/team-aurora/-/members');
  });
});

describe('Group: breadcrumbs', () => {
  it('supplies the breadcrumbs to the app shell instead of placing them at the top itself', async () => {
    const { wrapper } = await makeWrapper('/team-aurora');

    expect(wrapper.find('nldd-breadcrumbs').exists()).toBe(false);
    expect(breadcrumbsFor('/team-aurora')).toEqual([
      { text: 'Overzicht', href: '/' },
      { text: 'Team Aurora' },
    ]);
  });

  it('moves the breadcrumbs along to the path of the open tab', async () => {
    const { router } = await makeWrapper('/team-aurora');

    await router.push('/team-aurora/-/members');
    await untilIdle();

    expect(breadcrumbsFor('/team-aurora')).toEqual([]);
    expect(breadcrumbsFor('/team-aurora/-/members')).toEqual([
      { text: 'Overzicht', href: '/' },
      { text: 'Team Aurora' },
    ]);
  });
});

describe('Group: group members', () => {
  it('adds a member in the list itself, without a side panel', async () => {
    const { wrapper } = await makeWrapper('/team-aurora/-/members');

    expect(sheetLabels(wrapper)).toEqual(['Zet een site online']);

    const field = wrapper.find('nldd-combo-box[name="identifier"]').element;
    fireDetailEvent(field, 'input', { value: 'nieuw@voorbeeld.nl' });
    // Only a pick is an identifier: the combo box reports one as a `change`.
    fireDetailEvent(field, 'change', { value: 'nieuw@voorbeeld.nl' });
    await wrapper.find('[data-testid="member-form"]').trigger('submit');
    await untilIdle();

    expect(
      backend.data.groupMembers.find((l) => l.identifier === 'nieuw@voorbeeld.nl')?.role,
    ).toBe('reader');
    expect(wrapper.find('[data-testid="member-delete-nieuw@voorbeeld.nl"]').exists()).toBe(true);
  });

  it('removes a member from the row and keeps the group in sync', async () => {
    const { wrapper, router } = await makeWrapper('/team-aurora/-/members');

    await runRowAction(wrapper, 'member-delete-dev-beheerder');
    await wrapper.find('[data-testid="confirm-continue"]').trigger('click');
    await untilIdle();

    expect(backend.data.groupMembers.map((l) => l.identifier)).not.toContain('dev-beheerder');
    expect(wrapper.find('[data-testid="member-delete-dev-beheerder"]').exists()).toBe(false);

    // The page owns the source of truth: a detour via another tab does not
    // bring the member back.
    await router.push('/team-aurora/-/settings');
    await untilIdle();
    await router.push('/team-aurora/-/members');
    await untilIdle();
    expect(wrapper.find('[data-testid="member-delete-dev-beheerder"]').exists()).toBe(false);
  });

  it('changes a role from the row and keeps the group in sync', async () => {
    const { wrapper, router } = await makeWrapper('/team-aurora/-/members');

    expect(roleOf(wrapper, 'ada@voorbeeld.nl')).toBe('Redacteur');

    await runRowAction(wrapper, 'member-role-ada@voorbeeld.nl-admin');

    expect(
      backend.data.groupMembers.find((l) => l.identifier === 'ada@voorbeeld.nl')?.role,
    ).toBe('admin');
    expect(roleOf(wrapper, 'ada@voorbeeld.nl')).toBe('Beheerder');

    // The changed member replaces the old one in the list of the page, so a
    // detour via another tab does not bring the previous role back.
    await router.push('/team-aurora/-/settings');
    await untilIdle();
    await router.push('/team-aurora/-/members');
    await untilIdle();
    expect(roleOf(wrapper, 'ada@voorbeeld.nl')).toBe('Beheerder');
  });
});

describe('Group: settings', () => {
  it('picks the default base inline on the tab, without a sheet', async () => {
    const { wrapper } = await makeWrapper('/team-aurora/-/settings');

    expect(sheetLabels(wrapper)).toEqual(['Zet een site online']);
    expect(wrapper.find('nldd-button[text="Wijzigen"]').exists()).toBe(false);

    const group = wrapper.find('[data-testid="default-access-group"]');
    expect(group.attributes('type')).toBe('radiogroup');
    expect(group.findAll('nldd-list-item')).toHaveLength(4);
    expect(
      wrapper.find('[data-testid="default-access-public"]').attributes('checked'),
    ).toBeDefined();

    fireDetailEvent(
      wrapper.find('[data-testid="default-access-site_team"]').element,
      'change',
      { checked: true },
    );
    await untilIdle();

    expect(backend.data.groups.find((g) => g.slug === 'team-aurora')?.defaultAccess).toEqual({
      base: 'site_team',
      keys: false,
      invitees: false,
    });
    expect(
      wrapper.find('[data-testid="default-access-site_team"]').attributes('checked'),
    ).toBeDefined();
    expect(
      wrapper.find('nldd-notification[text="Standaardtoegang opgeslagen"]').exists(),
    ).toBe(true);
  });

  it('turns on an exception in the default without touching the base', async () => {
    const { wrapper } = await makeWrapper('/team-aurora/-/settings');

    fireDetailEvent(
      wrapper.find('[data-testid="default-access-keys"]').element,
      'change',
      { checked: true },
    );
    await untilIdle();

    expect(backend.data.groups.find((g) => g.slug === 'team-aurora')?.defaultAccess).toEqual({
      base: 'public',
      keys: true,
      invitees: false,
    });
  });

  it('rolls back the choice and reports it when saving fails', async () => {
    const { wrapper } = await makeWrapper('/team-aurora/-/settings');

    vi.stubGlobal('fetch', serverErrorFetch());
    fireDetailEvent(
      wrapper.find('[data-testid="default-access-site_team"]').element,
      'change',
      { checked: true },
    );
    await untilIdle();

    expect(
      wrapper.find('[data-testid="default-access-public"]').attributes('checked'),
    ).toBeDefined();
    expect(
      wrapper.find('[data-testid="default-access-site_team"]').attributes('checked'),
    ).toBeUndefined();
    expect(
      wrapper.find('nldd-notification[text="Standaardtoegang niet opgeslagen"]').exists(),
    ).toBe(true);
  });

  it('leaves the already chosen base alone, without bothering the API', async () => {
    const { wrapper } = await makeWrapper('/team-aurora/-/settings');

    fireDetailEvent(wrapper.find('[data-testid="default-access-public"]').element, 'change', {
      checked: true,
    });
    await untilIdle();

    expect(backend.data.groups.find((g) => g.slug === 'team-aurora')?.defaultAccess).toEqual({
      base: 'public',
      keys: false,
      invitees: false,
    });
    expect(wrapper.find('nldd-notification').exists()).toBe(false);
  });
});

describe('Group: renaming the group', () => {
  async function rename(wrapper: ReturnType<typeof mount>, name: string): Promise<void> {
    fireDetailEvent(wrapper.find('[data-testid="group-name"]').element, 'input', { value: name });
    await wrapper.find('[data-testid="group-name-form"]').trigger('submit');
    await untilIdle();
  }

  it('offers it to a group admin, and the header, the crumb and the title follow', async () => {
    const { wrapper } = await makeWrapper('/team-aurora/-/settings');
    expect(wrapper.find('[data-testid="group-name"]').attributes('value')).toBe('Team Aurora');

    await rename(wrapper, 'Team Zonsopgang');

    expect(backend.data.groups[0]!.name).toBe('Team Zonsopgang');
    expect(wrapper.find('h1').text()).toBe('Team Zonsopgang');
    expect(breadcrumbsFor('/team-aurora/-/settings')).toEqual([
      { text: 'Overzicht', href: '/' },
      { text: 'Team Zonsopgang' },
    ]);
    expect(document.title).toBe('Team Zonsopgang - Instellingen - Plak');
    expect(wrapper.find('[data-testid="group-name-notice"]').text()).toBe(
      'Naam opgeslagen. De groep heet nu Team Zonsopgang.',
    );
    expect(wrapper.find('[data-testid="group-name"]').attributes('value')).toBe('Team Zonsopgang');
  });

  it('can be undone straight away, because the page holds the new name', async () => {
    const { wrapper } = await makeWrapper('/team-aurora/-/settings');
    await rename(wrapper, 'Team Zonsopgang');

    await rename(wrapper, 'Team Aurora');

    expect(backend.data.groups[0]!.name).toBe('Team Aurora');
    expect(wrapper.find('h1').text()).toBe('Team Aurora');
  });

  it('keeps the new name when the member leaves the tab and comes back', async () => {
    const { wrapper, router } = await makeWrapper('/team-aurora/-/settings');
    await rename(wrapper, 'Team Zonsopgang');

    await router.push('/team-aurora/-/members');
    await untilIdle();
    await router.push('/team-aurora/-/settings');
    await untilIdle();

    expect(wrapper.find('[data-testid="group-name"]').attributes('value')).toBe('Team Zonsopgang');
  });

  it('shows the name as text to an editor of the group', async () => {
    // lid-3 (Ada Vermeer) is editor on team-aurora.
    backend.data.loggedInMemberId = 'lid-3';
    const { wrapper } = await makeWrapper('/team-aurora/-/settings');

    expect(wrapper.find('[data-testid="group-name-form"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="group-name-text"]').text()).toBe('Team Aurora');
    expect(wrapper.text()).toContain('Alleen een beheerder van de groep kan de naam wijzigen.');
  });

  it('shows the name as text while the role is unknown', async () => {
    vi.stubGlobal('fetch', (input: RequestInfo | URL, init?: RequestInit) =>
      String(input).endsWith('/me') ? serverErrorFetch()(input, init) : backend.fetch(input, init),
    );
    const { wrapper } = await makeWrapper('/team-aurora/-/settings');

    expect(wrapper.find('[data-testid="group-name-form"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="group-name-text"]').text()).toBe('Team Aurora');
  });
});

describe('Group: the address of the group', () => {
  it('offers the form to a group admin, with the content host of /me in the example', async () => {
    const { wrapper } = await makeWrapper('/team-aurora/-/settings');

    expect(wrapper.find('[data-testid="group-address-form"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="group-address-current"]').text()).toContain(
      'https://sites.plak.test/team-aurora/website/',
    );
  });

  it('lists the old addresses the page holds, with the day they stop redirecting', async () => {
    backend.data.groups[0]!.previousSlugs = [
      { slug: 'team-oud', redirectsUntil: '2099-11-06T23:00:00Z' },
    ];
    const { wrapper } = await makeWrapper('/team-aurora/-/settings');

    expect(wrapper.find('[data-testid="group-address-previous"]').text()).toBe(
      'team-oud stuurt door tot en met 6 november 2099.',
    );
    expect(wrapper.find('[data-testid="group-address-restore-team-oud"]').exists()).toBe(true);
  });

  it('says who may change it to an editor of the group', async () => {
    // lid-3 (Ada Vermeer) is editor on team-aurora.
    backend.data.loggedInMemberId = 'lid-3';
    const { wrapper } = await makeWrapper('/team-aurora/-/settings');

    expect(wrapper.find('[data-testid="group-address-form"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="group-address-readonly"]').text()).toBe(
      'Alleen een beheerder van de groep kan het adres wijzigen.',
    );
  });

  it('shows it as text while the role is unknown', async () => {
    vi.stubGlobal('fetch', (input: RequestInfo | URL, init?: RequestInit) =>
      String(input).endsWith('/me') ? serverErrorFetch()(input, init) : backend.fetch(input, init),
    );
    const { wrapper } = await makeWrapper('/team-aurora/-/settings');

    expect(wrapper.find('[data-testid="group-address-form"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="group-address-readonly"]').exists()).toBe(true);
  });

  it('counts the days /me says an old address redirects', async () => {
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date('2026-10-08T10:00:00Z'));
    try {
      const { wrapper } = await makeWrapper('/team-aurora/-/settings');

      expect(wrapper.find('[data-testid="group-address-consequences"]').text()).toContain(
        'Tot en met 7 november 2026 kun je het oude adres terugzetten.',
      );
    } finally {
      vi.useRealTimers();
    }
  });
});

describe('Group: changing the address', () => {
  const ADDRESS = 'section[aria-labelledby="heading-group-address"]';

  beforeEach(() => {
    // Today is the 8th of October 2026 in Amsterdam.
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date('2026-10-08T10:00:00Z'));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  function typeAddress(wrapper: ReturnType<typeof mount>, slug: string): void {
    fireDetailEvent(wrapper.find('[data-testid="group-address"]').element, 'input', { value: slug });
  }

  async function changeAddress(wrapper: ReturnType<typeof mount>, slug: string): Promise<void> {
    typeAddress(wrapper, slug);
    await wrapper.find('[data-testid="group-address-form"]').trigger('submit');
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
    const { wrapper, router } = await makeWrapper('/team-aurora/-/settings');

    await changeAddress(wrapper, 'aurora');

    expect(backend.data.groups[0]!.slug).toBe('aurora');
    expect(router.currentRoute.value.name).toBe('group-settings');
    expect(router.currentRoute.value.path).toBe('/aurora/-/settings');
    expect(wrapper.find('[data-testid="tab-settings"]').attributes('current')).toBeDefined();
  });

  it('leaves the tab where it is: nothing is rebuilt and nothing loads in front of it', async () => {
    const { wrapper } = await makeWrapper('/team-aurora/-/settings');
    const bar = wrapper.find('[data-testid="group-tabs"]').element;
    const heading = wrapper.find('h1').element;
    const section = wrapper.find(ADDRESS).element;
    const button = wrapper.find('[data-testid="group-address-change"]').element;
    const status = wrapper.find('[data-testid="group-address-notice"]').element;

    await changeAddress(wrapper, 'aurora');

    expect(wrapper.find('[data-testid="group-tabs"]').element).toBe(bar);
    expect(wrapper.find('h1').element).toBe(heading);
    expect(wrapper.find(ADDRESS).element).toBe(section);
    expect(wrapper.find('[data-testid="group-address-change"]').element).toBe(button);
    expect(wrapper.find('[data-testid="group-address-notice"]').element).toBe(status);
    expect(wrapper.find('nldd-inline-dialog[variant="loading"]').exists()).toBe(false);
  });

  it('keeps the forms of the other sections of the tab: the group admin is still one', async () => {
    const { wrapper } = await makeWrapper('/team-aurora/-/settings');
    const name = wrapper.find('[data-testid="group-name-form"]').element;
    const danger = wrapper.find('[data-testid="delete-group"]').element;

    await changeAddress(wrapper, 'aurora');

    expect(wrapper.find('[data-testid="group-name-form"]').element).toBe(name);
    expect(wrapper.find('[data-testid="delete-group"]').element).toBe(danger);
    expect(wrapper.find('[data-testid="group-publish"]').exists()).toBe(true);
  });

  it('says what happened where the member is, and leaves the focus on the button', async () => {
    const { wrapper } = await makeWrapper('/team-aurora/-/settings');
    await changeAddress(wrapper, 'aurora');
    const focus = vi.spyOn(HTMLElement.prototype, 'focus');

    await dialogClosed(wrapper);

    expect(wrapper.find('[data-testid="group-address-notice"]').text()).toBe(
      'Het adres is gewijzigd. team-aurora stuurt tot en met 7 november 2026 door naar aurora. Gebruik je automatisch publiceren of plak publish? Pas het adres daar nu aan.',
    );
    expect(focus.mock.contexts).toEqual([
      wrapper.find('[data-testid="group-address-change"]').element,
    ]);
    focus.mockRestore();
  });

  it('shows the new address in the tab bar and the section, and the sites below it', async () => {
    const { wrapper, router } = await makeWrapper('/team-aurora/-/settings');

    await changeAddress(wrapper, 'aurora');

    expect(wrapper.find('[data-testid="tab-sites"]').attributes('href')).toBe('/aurora');
    expect(wrapper.find('[data-testid="tab-settings"]').attributes('href')).toBe('/aurora/-/settings');
    expect(wrapper.find('[data-testid="group-address-current"]').text()).toContain(
      'Het adres van deze groep is aurora.',
    );
    expect(wrapper.find('[data-testid="group-address-current"]').text()).toContain(
      `${MOCK_CONTENT_BASE}/aurora/website/`,
    );
    expect(wrapper.find('[data-testid="group-address-previous"]').text()).toBe(
      'team-aurora stuurt door tot en met 7 november 2026.',
    );

    await router.push('/aurora');
    await untilIdle();
    expect(
      wrapper.find('[data-testid="site-website"]').find('nldd-link').attributes('href'),
    ).toBe('/aurora/website');
  });

  it('keeps the title of the browser tab after the router has put the new address in it', async () => {
    const { wrapper, router } = await makeWrapper('/team-aurora/-/settings');
    // As the app does on every navigation, before the page has had its say.
    router.afterEach(titleAfterNavigation);

    await changeAddress(wrapper, 'aurora');

    expect(document.title).toBe('Team Aurora - Instellingen - Plak');
  });

  it('keeps the name in the header: only the address has changed', async () => {
    const { wrapper } = await makeWrapper('/team-aurora/-/settings');

    await changeAddress(wrapper, 'aurora');

    expect(wrapper.find('h1').text()).toBe('Team Aurora');
    expect(document.title).toBe('Team Aurora - Instellingen - Plak');
    expect(breadcrumbsFor('/aurora/-/settings')).toEqual([
      { text: 'Overzicht', href: '/' },
      { text: 'Team Aurora' },
    ]);
  });

  it('can change the address once more straight away, from the address it has now', async () => {
    const { wrapper, router } = await makeWrapper('/team-aurora/-/settings');
    await changeAddress(wrapper, 'aurora');
    await dialogClosed(wrapper);

    await changeAddress(wrapper, 'zonsopgang');

    expect(backend.data.groups[0]!.slug).toBe('zonsopgang');
    expect(router.currentRoute.value.path).toBe('/zonsopgang/-/settings');
    expect(backend.data.groups[0]!.previousSlugs.map((p) => p.slug)).toEqual([
      'aurora',
      'team-aurora',
    ]);
  });

  it('changes an address back from the list of the old ones', async () => {
    const { wrapper, router } = await makeWrapper('/team-aurora/-/settings');
    await changeAddress(wrapper, 'aurora');
    await dialogClosed(wrapper);

    await wrapper.find('[data-testid="group-address-restore-team-aurora"]').trigger('click');
    await untilIdle();
    await wrapper.find(`${ADDRESS} [data-testid="confirm-continue"]`).trigger('click');
    await untilIdle();

    expect(backend.data.groups[0]!.slug).toBe('team-aurora');
    expect(router.currentRoute.value.path).toBe('/team-aurora/-/settings');
  });

  it('loads the page again for any other address, so the old one finds nothing', async () => {
    const { wrapper, router } = await makeWrapper('/team-aurora/-/settings');
    await changeAddress(wrapper, 'aurora');

    // Back to the old address, as the back button does: the API follows no old address.
    await router.push('/team-aurora/-/settings');
    await untilIdle();
    expect(wrapper.find('nldd-banner').attributes('text')).toBe('Onbekende groep');
    expect(wrapper.find('[data-testid="group-tabs"]').exists()).toBe(false);
    // Nothing of the group it showed before is left to name this address.
    expect(breadcrumbsFor('/team-aurora/-/settings')).toEqual([
      { text: 'Overzicht', href: '/' },
      { text: 'team-aurora' },
    ]);
    expect(document.title).toBe('team-aurora - Instellingen - Plak');

    // And forward again to the new one: a page that loaded in between does load again.
    await router.push('/aurora/-/settings');
    await untilIdle();
    expect(wrapper.find('[data-testid="group-tabs"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="tab-sites"]').attributes('href')).toBe('/aurora');
  });

  it('moves on even when the roles cannot be fetched again, and the member can go on from there', async () => {
    const { wrapper, router } = await makeWrapper('/team-aurora/-/settings');
    typeAddress(wrapper, 'aurora');
    await wrapper.find('[data-testid="group-address-form"]').trigger('submit');
    await untilIdle();
    const form = wrapper.find('[data-testid="group-address-form"]').element;
    vi.stubGlobal('fetch', (input: RequestInfo | URL, init?: RequestInit) =>
      String(input).endsWith('/me')
        ? Promise.reject(new TypeError('network down'))
        : backend.fetch(input, init),
    );
    const focus = vi.spyOn(HTMLElement.prototype, 'focus');

    await wrapper.find(`${ADDRESS} [data-testid="confirm-continue"]`).trigger('click');
    await untilIdle();
    await dialogClosed(wrapper);

    expect(backend.data.groups[0]!.slug).toBe('aurora');
    expect(router.currentRoute.value.path).toBe('/aurora/-/settings');
    // The roles still name the group by its old address, and are read as such:
    // the admin of the group is still one, in the whole page.
    expect(wrapper.find('[data-testid="group-address-form"]').element).toBe(form);
    expect(wrapper.find('[data-testid="group-name-form"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="delete-group"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="group-publish"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="group-address-notice"]').text()).toContain(
      'Het adres is gewijzigd.',
    );
    expect(focus.mock.contexts).toEqual([
      wrapper.find('[data-testid="group-address-change"]').element,
    ]);
    focus.mockRestore();
  });
});

describe('Group: another group in the address', () => {
  it('loads the other group when the route changes under the page, which it did not before', async () => {
    backend.data.groups.push({
      slug: 'andere',
      name: 'Andere groep',
      defaultAccess: { base: 'public', keys: false, invitees: false },
      previousSlugs: [],
    });
    const { wrapper, router } = await makeWrapper('/team-aurora');
    expect(wrapper.find('h1').text()).toBe('Team Aurora');

    await router.push('/andere');
    await untilIdle();

    expect(wrapper.find('h1').text()).toBe('Andere groep');
    expect(wrapper.find('[data-testid="tab-sites"]').attributes('href')).toBe('/andere');
    expect(breadcrumbsFor('/andere')).toEqual([
      { text: 'Overzicht', href: '/' },
      { text: 'Andere groep' },
    ]);
  });

  it('shows an unknown group as such, and no tabs', async () => {
    const { wrapper, router } = await makeWrapper('/team-aurora');

    await router.push('/onbekend');
    await untilIdle();

    expect(wrapper.find('nldd-banner').attributes('text')).toBe('Onbekende groep');
    expect(wrapper.find('[data-testid="group-tabs"]').exists()).toBe(false);
  });
});

describe('Group: deleting the group', () => {
  it('offers it to a group admin and goes to the overview once the group is gone', async () => {
    const { wrapper, router } = await makeWrapper('/team-aurora/-/settings');
    expect(
      wrapper.findAll('[data-testid="group-sites-list"] nldd-text-cell').map((c) => c.attributes('supporting-text')),
    ).toContain('team-aurora/website');

    await wrapper.find('[data-testid="delete-group"]').trigger('click');
    fireDetailEvent(wrapper.find('[data-testid="confirm-phrase"]').element, 'input', {
      value: 'team-aurora',
    });
    // The address section has a dialog of its own, earlier on the page.
    await wrapper
      .find('section[aria-labelledby="heading-danger-zone-group"] [data-testid="confirm-continue"]')
      .trigger('click');
    await untilIdle();

    expect(backend.data.groups.map((g) => g.slug)).not.toContain('team-aurora');
    expect(backend.data.sites.filter((s) => s.groupSlug === 'team-aurora')).toHaveLength(0);
    expect(router.currentRoute.value.name).toBe('overview');
  });

  it('keeps the danger zone away from an editor of the group', async () => {
    // lid-3 (Ada Vermeer) is editor on team-aurora.
    backend.data.loggedInMemberId = 'lid-3';
    const { wrapper } = await makeWrapper('/team-aurora/-/settings');

    expect(wrapper.find('[data-testid="default-access-group"]').exists()).toBe(true);
    expect(wrapper.find('nldd-box[background="critical"]').exists()).toBe(false);
  });

  it('keeps the danger zone away while the role is unknown', async () => {
    vi.stubGlobal('fetch', (input: RequestInfo | URL, init?: RequestInit) =>
      String(input).endsWith('/me') ? serverErrorFetch()(input, init) : backend.fetch(input, init),
    );
    const { wrapper } = await makeWrapper('/team-aurora/-/settings');

    expect(wrapper.find('[data-testid="default-access-group"]').exists()).toBe(true);
    expect(wrapper.find('nldd-box[background="critical"]').exists()).toBe(false);
  });
});

describe('Group: empty', () => {
  it('puts the empty states in the empty slots of their own tab', async () => {
    backend.data.groups.push({
      slug: 'leeg',
      name: 'Lege groep',
      defaultAccess: { base: 'public', keys: false, invitees: false },
      previousSlugs: [],
    });

    const { wrapper, router } = await makeWrapper('/leeg');
    expect(wrapper.find('[data-testid="sites-empty"]').attributes('text')).toBe(
      'Nog geen sites in deze groep',
    );

    await router.push('/leeg/-/members');
    await untilIdle();
    expect(
      wrapper.find('nldd-table > nldd-inline-dialog[slot="empty"]').attributes('text'),
    ).toBe('Nog geen groepsleden');
  });
});
