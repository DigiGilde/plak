import { mount } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { defineComponent, h } from 'vue';
import { createMemoryHistory, createRouter, RouterView, type Router } from 'vue-router';

import { makeMockBackend, type MockBackend } from '@/api/mock';
import TabSettings from '@/components/group/TabSettings.vue';
import TabMembers from '@/components/group/TabMembers.vue';
import TabSites from '@/components/group/TabSites.vue';
import PublishSheet from '@/components/PublishSheet.vue';
import { serverErrorFetch, untilIdle, fireDetailEvent } from '@/components/site/testHelpers';
import { _resetCurrentMemberCache } from '@/composables/currentMember';
import { _resetBreadcrumbs, breadcrumbsFor } from '@/composables/breadcrumbs';
import { _resetAddActions, useAddActions } from '@/composables/addActions';
import { takePublishedMark } from '@/composables/publishedMark';

import Group from './Group.vue';

let backend: MockBackend;

const Empty = defineComponent({ render: () => h('div', { 'data-testid': 'elders' }) });
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
      ['/team-aurora/-/settings', ['Standaardtoegang voor nieuwe sites', 'Gevarenzone']],
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
    expect(wrapper.find('[data-testid="groep-tabs"]').exists()).toBe(false);
  });
});

describe('Group: tab navigation', () => {
  it('navigates per tab to the corresponding subpath', async () => {
    const { wrapper, router } = await makeWrapper('/team-aurora');

    await wrapper.find('[data-testid="tab-members"]').trigger('click');
    await untilIdle();
    expect(router.currentRoute.value.name).toBe('group-members');
    expect(router.currentRoute.value.path).toBe('/team-aurora/-/members');
    expect(wrapper.find('[data-testid="leden-lijst"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="tab-members"]').attributes('current')).toBeDefined();
    expect(wrapper.find('[data-testid="tab-sites"]').attributes('current')).toBeUndefined();

    await wrapper.find('[data-testid="tab-settings"]').trigger('click');
    await untilIdle();
    expect(router.currentRoute.value.path).toBe('/team-aurora/-/settings');
    expect(wrapper.find('[data-testid="standaardtoegang-groep"]').exists()).toBe(true);

    await wrapper.find('[data-testid="tab-sites"]').trigger('click');
    await untilIdle();
    expect(router.currentRoute.value.path).toBe('/team-aurora');
    expect(wrapper.find('[data-testid="site-website"]').exists()).toBe(true);
  });

  it('also loads each tab directly via its URL', async () => {
    const { wrapper } = await makeWrapper('/team-aurora/-/settings');

    expect(wrapper.find('[data-testid="standaardtoegang-public"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="tab-settings"]').attributes('current')).toBeDefined();
  });

  it('keeps "Zet een site online" in the header on every tab', async () => {
    const { wrapper, router } = await makeWrapper('/team-aurora');

    for (const path of ['/team-aurora', '/team-aurora/-/members', '/team-aurora/-/settings']) {
      await router.push(path);
      await untilIdle();
      expect(wrapper.find('[data-testid="groep-publiceren"]').attributes('text')).toBe(
        'Zet een site online',
      );
    }
  });

  it('gives the site table the work width and the other tabs the reading width', async () => {
    const { wrapper, router } = await makeWrapper('/nldd');
    const section = () => wrapper.find('nldd-simple-section');
    expect(section().classes()).not.toContain('leesbreedte');

    for (const path of ['/nldd/-/members', '/nldd/-/settings']) {
      await router.push(path);
      await untilIdle();
      expect(section().classes()).toContain('leesbreedte');
    }
  });

  it('does not rebuild the group header when switching tabs', async () => {
    const { wrapper, router } = await makeWrapper('/team-aurora');
    const bar = wrapper.find('[data-testid="groep-tabs"]').element;
    const title = wrapper.find('h1').element;

    await router.push('/team-aurora/-/members');
    await untilIdle();

    expect(wrapper.find('[data-testid="groep-tabs"]').element).toBe(bar);
    expect(wrapper.find('h1').element).toBe(title);
  });
});

describe('Group: put a site online', () => {
  it('names the button after the task, not after the data model', async () => {
    const { wrapper } = await makeWrapper('/team-aurora');

    expect(wrapper.find('[data-testid="groep-publiceren"]').attributes('text')).toBe(
      'Zet een site online',
    );
    expect(wrapper.html()).not.toContain('Nieuw site');
  });

  it('shows the button for an editor of the group', async () => {
    // lid-3 (Ada Vermeer) is editor on team-aurora, not a platform admin.
    backend.data.loggedInMemberId = 'lid-3';
    const { wrapper } = await makeWrapper('/team-aurora');

    expect(wrapper.find('[data-testid="groep-publiceren"]').exists()).toBe(true);
  });

  it('hides the button for someone with only the reader role in the group', async () => {
    // lid-4 (Zoë de Wit) is reader on team-aurora: may not create a site.
    backend.data.loggedInMemberId = 'lid-4';
    const { wrapper } = await makeWrapper('/team-aurora');

    expect(wrapper.find('[data-testid="groep-publiceren"]').exists()).toBe(false);
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
        { slug: 'team-aurora', name: 'Team Aurora', defaultAccess: expect.anything() },
      ]);
    });

    it('does nothing for someone without an editor or admin role', async () => {
      backend.data.loggedInMemberId = 'lid-4';
      const { wrapper } = await makeWrapper('/team-aurora');

      const section = wrapper.find('nldd-simple-section').element;
      section.dispatchEvent(dragEvent('dragenter', fakeDataTransfer([droppableFile()])));
      await untilIdle();
      expect(wrapper.find('[data-testid="groep-sleep-actief"]').exists()).toBe(false);

      section.dispatchEvent(dragEvent('drop', fakeDataTransfer([droppableFile()])));
      await untilIdle();
      expect(wrapper.findComponent(PublishSheet).props('open')).toBe(false);
    });

    it('shows the drag state on dragenter and hides it again on dragleave', async () => {
      const { wrapper } = await makeWrapper('/team-aurora');

      const section = wrapper.find('nldd-simple-section').element;
      section.dispatchEvent(dragEvent('dragenter', fakeDataTransfer([droppableFile()])));
      await untilIdle();
      expect(wrapper.find('[data-testid="groep-sleep-actief"]').exists()).toBe(true);

      section.dispatchEvent(dragEvent('dragleave', fakeDataTransfer([droppableFile()])));
      await untilIdle();
      expect(wrapper.find('[data-testid="groep-sleep-actief"]').exists()).toBe(false);
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

      expect(wrapper.find('[data-testid="groep-sleep-actief"]').exists()).toBe(false);
    });

    it('shows a dismissible error for a dropped file of an unsupported type', async () => {
      const { wrapper } = await makeWrapper('/team-aurora');
      const badFile = new File(['hoi'], 'site.pdf', { type: 'application/pdf' });

      wrapper
        .find('nldd-simple-section')
        .element.dispatchEvent(dragEvent('drop', fakeDataTransfer([badFile])));
      await untilIdle();

      const banner = wrapper.find('[data-testid="groep-sleep-fout"]');
      expect(banner.exists()).toBe(true);
      expect(banner.attributes('text')).toContain('Sleep één bestand');
      expect(wrapper.findComponent(PublishSheet).props('open')).toBe(false);

      banner.element.dispatchEvent(new CustomEvent('dismiss'));
      await untilIdle();

      expect(wrapper.find('[data-testid="groep-sleep-fout"]').exists()).toBe(false);
    });

    it('ignores a drop that carries nothing file-shaped, such as dragged text', async () => {
      const { wrapper } = await makeWrapper('/team-aurora');

      wrapper
        .find('nldd-simple-section')
        .element.dispatchEvent(dragEvent('drop', fakeDataTransfer([])));
      await untilIdle();

      expect(wrapper.find('[data-testid="groep-sleep-fout"]').exists()).toBe(false);
      expect(wrapper.findComponent(PublishSheet).props('open')).toBe(false);
    });
  });

  it('puts a site online from the sheet and takes the user to the result', async () => {
    const { wrapper, router } = await makeWrapper('/team-aurora');

    await wrapper.find('[data-testid="groep-publiceren"]').trigger('click');
    await untilIdle();

    // The group is known, so the sheet does not ask for it; the address is
    // there, built from the content origin out of /me.
    const sheet = wrapper.find('nldd-sheet[accessible-label="Zet een site online"]');
    expect(sheet.find('nldd-dropdown').exists()).toBe(false);

    chooseFile(wrapper, 'documentatie.zip');
    await untilIdle();
    expect(wrapper.find('[data-testid="publiceer-adres"]').text()).toContain(
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

    await wrapper.find('[data-testid="groep-publiceren"]').trigger('click');
    chooseFile(wrapper, 'documentatie.zip');
    await untilIdle();

    expect(wrapper.find('[data-testid="publiceer-adres"]').text()).toContain('/team-aurora/documentatie/');
    expect(wrapper.find('[data-testid="publiceer-adres"]').text()).not.toContain('https://');

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
    expect(wrapper.find('[data-testid="groep-publiceren"]').exists()).toBe(true);
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
    await wrapper.find('[data-testid="lid-formulier"]').trigger('submit');
    await untilIdle();

    expect(
      backend.data.groupMembers.find((l) => l.identifier === 'nieuw@voorbeeld.nl')?.role,
    ).toBe('reader');
    expect(wrapper.find('[data-testid="lid-verwijderen-nieuw@voorbeeld.nl"]').exists()).toBe(true);
  });

  it('removes a member from the row and keeps the group in sync', async () => {
    const { wrapper, router } = await makeWrapper('/team-aurora/-/members');

    await runRowAction(wrapper, 'lid-verwijderen-dev-beheerder');
    await wrapper.find('[data-testid="bevestig-doorgaan"]').trigger('click');
    await untilIdle();

    expect(backend.data.groupMembers.map((l) => l.identifier)).not.toContain('dev-beheerder');
    expect(wrapper.find('[data-testid="lid-verwijderen-dev-beheerder"]').exists()).toBe(false);

    // The page owns the source of truth: a detour via another tab does not
    // bring the member back.
    await router.push('/team-aurora/-/settings');
    await untilIdle();
    await router.push('/team-aurora/-/members');
    await untilIdle();
    expect(wrapper.find('[data-testid="lid-verwijderen-dev-beheerder"]').exists()).toBe(false);
  });

  it('changes a role from the row and keeps the group in sync', async () => {
    const { wrapper, router } = await makeWrapper('/team-aurora/-/members');

    expect(roleOf(wrapper, 'ada@voorbeeld.nl')).toBe('Redacteur');

    await runRowAction(wrapper, 'lid-rol-ada@voorbeeld.nl-admin');

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

    const group = wrapper.find('[data-testid="standaardtoegang-groep"]');
    expect(group.attributes('type')).toBe('radiogroup');
    expect(group.findAll('nldd-list-item')).toHaveLength(4);
    expect(
      wrapper.find('[data-testid="standaardtoegang-public"]').attributes('checked'),
    ).toBeDefined();

    fireDetailEvent(
      wrapper.find('[data-testid="standaardtoegang-site_team"]').element,
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
      wrapper.find('[data-testid="standaardtoegang-site_team"]').attributes('checked'),
    ).toBeDefined();
    expect(
      wrapper.find('nldd-notification[text="Standaardtoegang opgeslagen"]').exists(),
    ).toBe(true);
  });

  it('turns on an exception in the default without touching the base', async () => {
    const { wrapper } = await makeWrapper('/team-aurora/-/settings');

    fireDetailEvent(
      wrapper.find('[data-testid="standaardtoegang-sleutels"]').element,
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
      wrapper.find('[data-testid="standaardtoegang-site_team"]').element,
      'change',
      { checked: true },
    );
    await untilIdle();

    expect(
      wrapper.find('[data-testid="standaardtoegang-public"]').attributes('checked'),
    ).toBeDefined();
    expect(
      wrapper.find('[data-testid="standaardtoegang-site_team"]').attributes('checked'),
    ).toBeUndefined();
    expect(
      wrapper.find('nldd-notification[text="Standaardtoegang niet opgeslagen"]').exists(),
    ).toBe(true);
  });

  it('leaves the already chosen base alone, without bothering the API', async () => {
    const { wrapper } = await makeWrapper('/team-aurora/-/settings');

    fireDetailEvent(wrapper.find('[data-testid="standaardtoegang-public"]').element, 'change', {
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

describe('Group: deleting the group', () => {
  it('offers it to a group admin and goes to the overview once the group is gone', async () => {
    const { wrapper, router } = await makeWrapper('/team-aurora/-/settings');
    expect(
      wrapper.findAll('[data-testid="groep-sites-lijst"] nldd-text-cell').map((c) => c.attributes('supporting-text')),
    ).toContain('team-aurora/website');

    await wrapper.find('[data-testid="verwijder-groep"]').trigger('click');
    fireDetailEvent(wrapper.find('[data-testid="bevestig-zin"]').element, 'input', {
      value: 'team-aurora',
    });
    await wrapper.find('[data-testid="bevestig-doorgaan"]').trigger('click');
    await untilIdle();

    expect(backend.data.groups.map((g) => g.slug)).not.toContain('team-aurora');
    expect(backend.data.sites.filter((s) => s.groupSlug === 'team-aurora')).toHaveLength(0);
    expect(router.currentRoute.value.name).toBe('overview');
  });

  it('keeps the danger zone away from an editor of the group', async () => {
    // lid-3 (Ada Vermeer) is editor on team-aurora.
    backend.data.loggedInMemberId = 'lid-3';
    const { wrapper } = await makeWrapper('/team-aurora/-/settings');

    expect(wrapper.find('[data-testid="standaardtoegang-groep"]').exists()).toBe(true);
    expect(wrapper.find('nldd-box[background="critical"]').exists()).toBe(false);
  });

  it('keeps the danger zone away while the role is unknown', async () => {
    vi.stubGlobal('fetch', (input: RequestInfo | URL, init?: RequestInit) =>
      String(input).endsWith('/me') ? serverErrorFetch()(input, init) : backend.fetch(input, init),
    );
    const { wrapper } = await makeWrapper('/team-aurora/-/settings');

    expect(wrapper.find('[data-testid="standaardtoegang-groep"]').exists()).toBe(true);
    expect(wrapper.find('nldd-box[background="critical"]').exists()).toBe(false);
  });
});

describe('Group: empty', () => {
  it('puts the empty states in the empty slots of their own tab', async () => {
    backend.data.groups.push({
      slug: 'leeg',
      name: 'Lege groep',
      defaultAccess: { base: 'public', keys: false, invitees: false },
    });

    const { wrapper, router } = await makeWrapper('/leeg');
    expect(wrapper.find('[data-testid="sites-leeg"]').attributes('text')).toBe(
      'Nog geen sites in deze groep',
    );

    await router.push('/leeg/-/members');
    await untilIdle();
    expect(
      wrapper.find('nldd-table > nldd-inline-dialog[slot="empty"]').attributes('text'),
    ).toBe('Nog geen groepsleden');
  });
});
