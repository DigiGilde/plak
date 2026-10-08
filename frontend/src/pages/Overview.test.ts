import '@nldd/design-system';

import { flushPromises, mount } from '@vue/test-utils';
import axe from 'axe-core';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createMemoryHistory, createRouter, type Router } from 'vue-router';

import * as plakApi from '../api/plak';
import type { Group, Overview as OverviewData, Site } from '../api/types';
import NewGroupSheet from '../components/NewGroupSheet.vue';
import PublishSheet from '../components/PublishSheet.vue';
import { _resetCurrentMemberCache } from '../composables/currentMember';
import { takePublishedMark } from '../composables/publishedMark';
import Overview from './Overview.vue';

/**
 * jsdom 25 reflects the ARIA properties of ElementInternals onto a
 * `_internalContentAttributeMap` it never creates itself, so writing throws a
 * TypeError. nldd-file-field sets `internals.ariaLabel` in its willUpdate as
 * soon as it is attached to the document, and that error comes back as an
 * unhandled rejection that fails the run. Plain properties instead of the
 * reflectors, just as in tests/site-axe.test.ts.
 */
{
  const proto = (globalThis as { ElementInternals?: { prototype: object } }).ElementInternals
    ?.prototype;
  for (const name of proto ? Object.getOwnPropertyNames(proto) : []) {
    if (name !== 'role' && !name.startsWith('aria')) continue;
    Object.defineProperty(proto, name, { value: null, writable: true, configurable: true });
  }
}

vi.mock('../api/plak', () => ({
  overview: vi.fn(),
  createSite: vi.fn(),
  createGroup: vi.fn(),
  setAccess: vi.fn(),
  upload: vi.fn(),
  me: vi.fn(),
}));

const filledData: OverviewData = {
  groups: [
    {
      group: { slug: 'team-aurora', name: 'Team Aurora', defaultAccess: { base: 'public', keys: false, invitees: false }, previousSlugs: [] },
      sites: [
        {
          id: '3f2b8a1e-5d4c-4e9a-9b6f-7c1d2e3a4b5c',
          groupSlug: 'team-aurora',
          slug: 'website',
          title: 'Team Aurora website',
          access: { base: 'public', keys: false, invitees: false },
          externalSources: false,
          sandbox: true,
          liveVersionsKept: null,
          liveVersionId: 'versie-1',
          createdBy: 'dev-beheerder',
          hasLiveVersion: true,
          lastPublishedAt: '2026-07-17T14:32:00.000Z',
          previewCount: 1,
          previousSlugs: [],
        },
      ],
    },
  ],
};

function makeRouter(): Router {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'overview', component: Overview },
      { path: '/:group/:site/done', name: 'site-done', component: { template: '<div />' } },
    ],
  });
}

describe('Overview', () => {
  afterEach(() => {
    vi.mocked(plakApi.overview).mockReset();
    _resetCurrentMemberCache();
  });

  it('loading: shows an activity indicator while the request is in flight', async () => {
    let release!: (data: OverviewData) => void;
    vi.mocked(plakApi.overview).mockImplementation(
      () =>
        new Promise((resolve) => {
          release = resolve;
        }),
    );

    const wrapper = mount(Overview, { global: { plugins: [makeRouter()] } });
    await flushPromises();

    expect(wrapper.find('nldd-activity-indicator').exists()).toBe(true);
    expect(wrapper.findAll('nldd-table-row:not([slot="header"])')).toHaveLength(0);
    // While it is unknown whether anything is there, there is no action to offer.
    expect(wrapper.find('[data-testid="overview-publish"]').exists()).toBe(false);

    release(filledData);
    await flushPromises();

    expect(wrapper.find('nldd-activity-indicator').exists()).toBe(false);
    expect(wrapper.findAll('nldd-table-row:not([slot="header"])')).toHaveLength(1);

    wrapper.unmount();
  });

  it('filled: shows groups with their site rows', async () => {
    vi.mocked(plakApi.overview).mockResolvedValue(filledData);

    const wrapper = mount(Overview, { global: { plugins: [makeRouter()] } });
    await flushPromises();

    expect(wrapper.text()).toContain('Team Aurora');
    expect(wrapper.findAll('nldd-table-row:not([slot="header"])')).toHaveLength(1);
    expect(wrapper.find('nldd-banner').exists()).toBe(false);
    // The empty state sits in the list's `empty` slot: the list decides for
    // itself when to show it, so it is in the DOM for filled lists too.
    expect(wrapper.find('nldd-inline-dialog').attributes('slot')).toBe('empty');

    wrapper.unmount();
  });

  it('filled: the page has a primary action that names the whole task', async () => {
    vi.mocked(plakApi.overview).mockResolvedValue(filledData);

    const wrapper = mount(Overview, {
      global: { plugins: [makeRouter()], stubs: { teleport: true } },
    });
    await flushPromises();

    const button = wrapper.find('[data-testid="overview-publish"]');
    const el = button.element as HTMLElement & { text?: string; variant?: string };
    expect(el.text).toBe('Zet een site online');
    expect(el.variant).toBe('primary');
    // Deliberately NOT in the end slot of nldd-title: that slot neither shrinks
    // nor drops, which broke the title letter by letter at 320 px and pushed the
    // button outside the viewport. The button now sits beside the title in a
    // wrap container, so it drops a line as soon as it no longer fits.
    expect(button.attributes('slot')).toBeUndefined();
    const heading = wrapper.find('.page-header');
    expect(heading.exists()).toBe(true);
    // nldd-container does not reflect `layout` back to a DOM attribute, so read
    // the property rather than the attribute.
    expect((heading.element as HTMLElement & { layout?: string }).layout).toBe('wrap');
    expect(heading.find('[data-testid="overview-publish"]').exists()).toBe(true);
    expect(heading.find('nldd-title h1').text()).toBe('Overzicht');
    expect(wrapper.findComponent(PublishSheet).props('open')).toBe(false);

    // The action half of the split button, not the chevron: that is the event
    // the design system fires for it.
    button.element.dispatchEvent(new CustomEvent('action-click'));
    await flushPromises();

    expect(wrapper.findComponent(PublishSheet).props('open')).toBe(true);

    wrapper.unmount();
  });

  it('filled: the site list is a table with a header row and a tinted fill', async () => {
    vi.mocked(plakApi.overview).mockResolvedValue(filledData);

    const wrapper = mount(Overview, {
      attachTo: document.body,
      global: { plugins: [makeRouter()] },
    });
    await flushPromises();

    const table = wrapper.find('nldd-table');
    expect(table.attributes('background')).toBe('tinted');
    // The column widths sit on the table once: that lands the cells of every
    // row, and of every group table on this page, on the same x.
    expect(table.attributes('columns')).toBe('3.25rem minmax(11rem, 1fr) 9.25rem 12rem');
    expect(table.attributes('sm-columns')).toBe('auto minmax(0, 1fr)');

    const headings = table
      .find('nldd-table-row[slot="header"]')
      .findAll('nldd-text-cell')
      .map((cell) => cell.attributes('text'));
    expect(headings).toEqual(['**Live**', '**Site**', '**Toegang**', '**Laatste publicatie**']);
    // No empty column header: it would land in the accessibility tree as a
    // nameless columnheader.
    expect(headings.every((text) => (text ?? '').length > 0)).toBe(true);

    wrapper.unmount();
  });

  it('filled: group heading and table sit closer together than groups do to each other', async () => {
    vi.mocked(plakApi.overview).mockResolvedValue(filledData);

    const wrapper = mount(Overview, {
      attachTo: document.body,
      global: { plugins: [makeRouter()] },
    });
    await flushPromises();

    const groupBlock = wrapper.find('nldd-table').element.parentElement as HTMLElement;
    expect(groupBlock.tagName.toLowerCase()).toBe('nldd-container');
    expect(groupBlock.querySelector('h2')).not.toBeNull();

    const binding = Number(groupBlock.getAttribute('gap'));
    const separation = Number((groupBlock.parentElement as HTMLElement).getAttribute('gap'));
    expect(binding).toBeLessThan(separation);

    wrapper.unmount();
  });

  it('filled: both create actions carry text, no bare plus in the heading', async () => {
    vi.mocked(plakApi.overview).mockResolvedValue(filledData);

    const wrapper = mount(Overview, {
      attachTo: document.body,
      global: { plugins: [makeRouter()] },
    });
    await flushPromises();

    // The icon button hung at the right of the group heading, past the end of
    // the table, and carried nothing but a plus sign. The chevron of the split
    // button lives in its own shadow DOM and names itself.
    expect(wrapper.findAll('nldd-icon-button')).toHaveLength(0);

    const split = wrapper.find('[data-testid="overview-publish"]');
    expect((split.element as HTMLElement & { text?: string }).text).toBe('Zet een site online');

    // Creating a group moved into the menu of that same button: still a line
    // with text, not a plus.
    const item = split.find('[data-testid="overview-group-create"]');
    expect(item.exists()).toBe(true);
    expect((item.element as HTMLElement & { text?: string }).text).toBe('Groep aanmaken');

    // Nothing hangs below a group table any more: both actions sit at the top
    // of the page, so a long list does not put them out of reach.
    const table = wrapper.find('nldd-table').element;
    const groupBlock = table.parentElement as HTMLElement;
    expect([...groupBlock.children].indexOf(table)).toBe(groupBlock.children.length - 1);

    wrapper.unmount();
  });

  it('filled: a group without sites shows an empty-table notice, no crash', async () => {
    vi.mocked(plakApi.overview).mockResolvedValue({
      groups: [
        {
          group: { slug: 'empty', name: 'Lege groep', defaultAccess: { base: 'public', keys: false, invitees: false }, previousSlugs: [] },
          sites: [],
        },
      ],
    });

    const wrapper = mount(Overview, { global: { plugins: [makeRouter()] } });
    await flushPromises();

    expect(wrapper.text()).toContain('Lege groep');
    const emptyNotice = wrapper.find('nldd-table nldd-inline-dialog[slot="empty"]');
    expect(emptyNotice.exists()).toBe(true);
    expect((emptyNotice.element as HTMLElement & { text?: string }).text).toBe(
      'Nog geen sites in deze groep.',
    );
    expect(wrapper.findAll('nldd-table-row:not([slot="header"])')).toHaveLength(0);

    wrapper.unmount();
  });

  it('empty: offers everyone the same one action, and sends no one to a platform admin', async () => {
    vi.mocked(plakApi.overview).mockResolvedValue({ groups: [] });

    const wrapper = mount(Overview, {
      global: { plugins: [makeRouter()], stubs: { teleport: true } },
    });
    await flushPromises();

    const emptyNotice = wrapper.find('nldd-inline-dialog');
    expect((emptyNotice.element as HTMLElement & { text?: string }).text).toBe(
      'Er staat hier nog niets online.',
    );
    expect(wrapper.text()).not.toContain('platformbeheerder');

    const button = wrapper.find('[data-testid="overview-empty-publish"]');
    expect((button.element as HTMLElement & { text?: string }).text).toBe('Zet een site online');
    expect(wrapper.findComponent(PublishSheet).props('open')).toBe(false);

    await button.trigger('click');

    expect(wrapper.findComponent(PublishSheet).props('open')).toBe(true);
    // No group is no roadblock: the sheet asks to create one itself.
    expect(wrapper.findComponent(PublishSheet).props('groups')).toEqual([]);

    wrapper.unmount();
  });

  it('error: a failed request shows an error banner instead of crashing', async () => {
    vi.mocked(plakApi.overview).mockRejectedValue(new Error('Kon overzicht niet laden'));

    const wrapper = mount(Overview, { global: { plugins: [makeRouter()] } });
    await flushPromises();

    const banner = wrapper.find('nldd-banner');
    expect(banner.exists()).toBe(true);
    expect(banner.attributes('supporting-text')).toBe('Kon overzicht niet laden');
    expect(wrapper.find('[data-testid="overview-publish"]').exists()).toBe(false);

    wrapper.unmount();
  });

  it('error: a rejection that is not an Error still yields a readable message', async () => {
    vi.mocked(plakApi.overview).mockRejectedValue('zomaar een string');

    const wrapper = mount(Overview, { global: { plugins: [makeRouter()] } });
    await flushPromises();

    expect(wrapper.find('nldd-banner').attributes('supporting-text')).toBe('Onbekende fout');

    wrapper.unmount();
  });

  it('passes the content origin from /me to the sheet, so the address is correct', async () => {
    vi.mocked(plakApi.overview).mockResolvedValue(structuredClone(filledData));
    vi.mocked(plakApi.me).mockResolvedValue({
      id: 'lid-1',
      ssoSubject: 'sub-1',
      email: 'lid@example.org',
      name: 'Lid',
      platformRole: 'member',
      status: 'active',
      createdAt: '2026-01-01T00:00:00Z',
      lastLoginAt: null,
      contentBaseUrl: 'https://sites.plak.test',
      groupRoles: [],
      siteRoles: [],
      ciForgejoHosts: [],
      ciAudience: 'https://plak.test',
      language: null,
      slugRedirectDays: 30,
    });

    const wrapper = mount(Overview, {
      global: { plugins: [makeRouter()], stubs: { teleport: true } },
    });
    await flushPromises();

    expect(wrapper.findComponent(PublishSheet).props('contentBase')).toBe(
      'https://sites.plak.test',
    );

    wrapper.unmount();
  });

  it('does not let a failed session fetch break the overview', async () => {
    vi.mocked(plakApi.overview).mockResolvedValue(structuredClone(filledData));
    vi.mocked(plakApi.me).mockRejectedValue(new Error('geen sessie'));

    const wrapper = mount(Overview, {
      global: { plugins: [makeRouter()], stubs: { teleport: true } },
    });
    await flushPromises();

    expect(wrapper.findAll('nldd-table-row:not([slot="header"])')).toHaveLength(1);
    expect(wrapper.findComponent(PublishSheet).props('contentBase')).toBe('');

    wrapper.unmount();
  });

  describe('publishing from the overview', () => {
    const newSite: Site = {
      id: 'b4e7d2c1-8a36-4f09-9d15-6c3a0e8f2b57',
      groupSlug: 'team-aurora',
      slug: 'handboek',
      title: 'Handboek',
      access: { base: 'public', keys: false, invitees: false },
      externalSources: false,
      sandbox: true,
      liveVersionsKept: null,
      liveVersionId: null,
      createdBy: 'dev-beheerder',
      hasLiveVersion: false,
      lastPublishedAt: null,
      previewCount: 0,
      previousSlugs: [],
    };
    const newGroup: Group = { slug: 'team', name: 'Team', defaultAccess: { base: 'public', keys: false, invitees: false }, previousSlugs: [] };

    // Teleport stubbed, like the other sheet tests: a sheet that really lands
    // in document.body is connected there as a custom element and then calls
    // showModal(), which jsdom does not have. Unmounted, Lit does not reflect
    // its attributes, so we read the properties Vue sets on it.
    function mountComponent(): void {
      router = makeRouter();
      wrapper = mount(Overview, {
        global: { plugins: [router], stubs: { teleport: true } },
      });
    }

    function property(el: Element, name: string): string {
      return (el as unknown as Record<string, string | undefined>)[name] ?? '';
    }

    function sheet(label: string): HTMLElement {
      const el = wrapper!.find(`nldd-sheet[accessible-label="${label}"]`);
      expect(el.exists(), `sheet "${label}" is missing`).toBe(true);
      return el.element as HTMLElement;
    }

    function titles(): string[] {
      return wrapper!.findAll('nldd-title-cell nldd-link').map((link) => link.text());
    }

    function listLabels(): string[] {
      return wrapper!.findAll('nldd-table').map((t) => property(t.element, 'accessibleLabel'));
    }

    function typeIn(root: HTMLElement, name: string, value: string): void {
      const field = [...root.querySelectorAll('nldd-text-field')].find(
        (el) => property(el, 'name') === name,
      );
      expect(field, `field "${name}" is missing`).toBeDefined();
      field!.dispatchEvent(new CustomEvent('input', { detail: { value: value } }));
    }

    function choose(root: HTMLElement, name: string): File {
      const file = new File(['<h1>hoi</h1>'], name, { type: 'text/html' });
      root
        .querySelector('nldd-file-field')!
        .dispatchEvent(new CustomEvent('change', { detail: { files: [file] } }));
      return file;
    }

    function submit(root: HTMLElement): void {
      root
        .querySelector('nldd-form')!
        .dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    }

    /**
     * The two halves of the split button beside the h1. Detached from the
     * document the custom elements never render their shadow DOM, so there is
     * no inner button to click: dispatch the events the design system fires
     * for them, as in App.test.ts.
     */
    async function clickPublish(): Promise<void> {
      const button = wrapper!.find('[data-testid="overview-publish"]');
      expect(button.exists(), 'the split button is missing').toBe(true);
      button.element.dispatchEvent(new CustomEvent('action-click'));
      await flushPromises();
    }

    async function selectCreateGroup(): Promise<void> {
      const item = wrapper!.find('[data-testid="overview-group-create"]');
      expect(item.exists(), 'the "Groep aanmaken" menu item is missing').toBe(true);
      item.element.dispatchEvent(new CustomEvent('select'));
      await flushPromises();
    }

    function groupChoice(): string[] | null {
      const choice = sheet('Zet een site online').querySelector('nldd-dropdown select');
      return choice === null
        ? null
        : [...choice.querySelectorAll('option')].map((option) => option.value);
    }

    let wrapper: ReturnType<typeof mount> | null = null;
    let router: Router | null = null;

    beforeEach(() => {
      vi.mocked(plakApi.overview).mockResolvedValue(structuredClone(filledData));
      vi.mocked(plakApi.createSite).mockReset();
      vi.mocked(plakApi.createGroup).mockReset();
      vi.mocked(plakApi.upload).mockReset();
      vi.mocked(plakApi.me).mockResolvedValue(undefined as never);
    });

    afterEach(() => {
      wrapper?.unmount();
      wrapper = null;
      router = null;
    });

    it('publishes a file and takes the user to the result screen', async () => {
      vi.mocked(plakApi.createSite).mockResolvedValue(newSite);
      vi.mocked(plakApi.upload).mockResolvedValue({ versionId: 'versie-9' });
      mountComponent();
      await flushPromises();
      expect(titles()).toEqual(['Team Aurora website']);

      await clickPublish();

      const form = sheet('Zet een site online');
      const file = choose(form, 'handboek.zip');
      submit(form);
      await flushPromises();

      expect(plakApi.createSite).toHaveBeenCalledWith('team-aurora', 'Handboek', 'handboek');
      expect(plakApi.upload).toHaveBeenCalledWith('team-aurora', 'handboek', file, 'handboek.zip');
      expect(titles()).toEqual(['Team Aurora website', 'Handboek']);
      expect(router!.currentRoute.value.fullPath).toBe('/team-aurora/handboek/done');
      // Marked as the publish flow, so the result screen may make a secret link.
      expect(takePublishedMark(router!)).toBe(true);
    });

    it('creates a group in the same flow when there are no groups', async () => {
      vi.mocked(plakApi.overview).mockResolvedValue({ groups: [] });
      vi.mocked(plakApi.createGroup).mockResolvedValue(newGroup);
      vi.mocked(plakApi.createSite).mockResolvedValue({
        ...newSite,
        groupSlug: 'team',
      });
      vi.mocked(plakApi.upload).mockResolvedValue({ versionId: 'versie-9' });
      mountComponent();
      await flushPromises();
      expect(listLabels()).toEqual([]);

      const form = sheet('Zet een site online');
      typeIn(form, 'group-name', 'Team');
      choose(form, 'handboek.zip');
      submit(form);
      await flushPromises();

      expect(plakApi.createGroup).toHaveBeenCalledWith('Team', 'team');
      // The fresh group should be in the overview at once, with the site in
      // it: the user will come back to this later.
      expect(listLabels()).toEqual(['Sites in Team']);
      expect(titles()).toEqual(['Handboek']);
      expect(router!.currentRoute.value.fullPath).toBe('/team/handboek/done');
      // Marked as the publish flow, so the result screen may make a secret link.
      expect(takePublishedMark(router!)).toBe(true);
    });

    it('asks for a group as soon as the user has more than one', async () => {
      vi.mocked(plakApi.overview).mockResolvedValue({
        groups: [
          ...structuredClone(filledData).groups,
          { group: newGroup, sites: [] },
        ],
      });
      mountComponent();
      await flushPromises();

      await clickPublish();

      expect(groupChoice()).toEqual(['team-aurora', 'team']);
    });

    it('creates a group from the split button menu and adds it to the overview', async () => {
      vi.mocked(plakApi.createGroup).mockResolvedValue(newGroup);
      mountComponent();
      await flushPromises();

      // No role gates this item: the backend lets every active member create
      // a group (create_group in api/admin.py).
      await selectCreateGroup();
      expect(wrapper!.findComponent(NewGroupSheet).props('open')).toBe(true);

      const form = sheet('Nieuwe groep');
      typeIn(form, 'name', 'Team');
      typeIn(form, 'slug', 'team');
      submit(form);
      await flushPromises();

      expect(plakApi.createGroup).toHaveBeenCalledWith('Team', 'team');
      expect(listLabels()).toEqual(['Sites in Team Aurora', 'Sites in Team']);
    });
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

    function meWithGroupRole(groupSlug: string, role: 'editor' | 'reader') {
      return {
        id: 'lid-1',
        ssoSubject: 'sub-1',
        email: 'lid@example.org',
        name: 'Lid',
        platformRole: 'member' as const,
        status: 'active' as const,
        createdAt: '2026-01-01T00:00:00Z',
        lastLoginAt: null,
        contentBaseUrl: 'https://sites.plak.test',
        groupRoles: [{ groupSlug, role }],
        siteRoles: [],
        ciForgejoHosts: [],
        ciAudience: 'https://plak.test',
        language: null,
        slugRedirectDays: 30,
      };
    }

    it('opens the sheet with the file when it is dropped anywhere on the page', async () => {
      vi.mocked(plakApi.overview).mockResolvedValue(structuredClone(filledData));
      vi.mocked(plakApi.me).mockResolvedValue(meWithGroupRole('team-aurora', 'editor'));

      const wrapper = mount(Overview, {
        global: { plugins: [makeRouter()], stubs: { teleport: true } },
      });
      await flushPromises();

      wrapper.find('nldd-simple-section').element.dispatchEvent(
        dragEvent('drop', fakeDataTransfer([droppableFile()])),
      );
      await flushPromises();

      const sheet = wrapper.findComponent(PublishSheet);
      expect(sheet.props('open')).toBe(true);
      expect(sheet.props('initialFile')).toBeInstanceOf(File);

      wrapper.unmount();
    });

    it('does nothing for a member without an editor or admin role in any group', async () => {
      vi.mocked(plakApi.overview).mockResolvedValue(structuredClone(filledData));
      vi.mocked(plakApi.me).mockResolvedValue(meWithGroupRole('team-aurora', 'reader'));

      const wrapper = mount(Overview, {
        global: { plugins: [makeRouter()], stubs: { teleport: true } },
      });
      await flushPromises();

      const section = wrapper.find('nldd-simple-section').element;
      section.dispatchEvent(dragEvent('dragenter', fakeDataTransfer([droppableFile()])));
      await flushPromises();
      expect(wrapper.find('[data-testid="overview-drag-active"]').exists()).toBe(false);

      section.dispatchEvent(dragEvent('drop', fakeDataTransfer([droppableFile()])));
      await flushPromises();

      expect(wrapper.findComponent(PublishSheet).props('open')).toBe(false);

      wrapper.unmount();
    });

    it('opens the sheet also for someone without groups, who creates one along the way', async () => {
      vi.mocked(plakApi.overview).mockResolvedValue({ groups: [] });
      vi.mocked(plakApi.me).mockResolvedValue({
        ...meWithGroupRole('team-aurora', 'editor'),
        groupRoles: [],
      });

      const wrapper = mount(Overview, {
        global: { plugins: [makeRouter()], stubs: { teleport: true } },
      });
      await flushPromises();

      wrapper.find('nldd-simple-section').element.dispatchEvent(
        dragEvent('drop', fakeDataTransfer([droppableFile()])),
      );
      await flushPromises();

      const sheet = wrapper.findComponent(PublishSheet);
      expect(sheet.props('open')).toBe(true);
      expect(sheet.props('initialFile')).toBeInstanceOf(File);

      wrapper.unmount();
    });

    it('shows the drag state on dragenter and hides it again on dragleave', async () => {
      vi.mocked(plakApi.overview).mockResolvedValue(structuredClone(filledData));
      vi.mocked(plakApi.me).mockResolvedValue(meWithGroupRole('team-aurora', 'editor'));

      const wrapper = mount(Overview, {
        global: { plugins: [makeRouter()], stubs: { teleport: true } },
      });
      await flushPromises();

      const section = wrapper.find('nldd-simple-section').element;
      section.dispatchEvent(dragEvent('dragenter', fakeDataTransfer([droppableFile()])));
      await flushPromises();
      expect(wrapper.find('[data-testid="overview-drag-active"]').exists()).toBe(true);

      section.dispatchEvent(dragEvent('dragleave', fakeDataTransfer([droppableFile()])));
      await flushPromises();
      expect(wrapper.find('[data-testid="overview-drag-active"]').exists()).toBe(false);

      wrapper.unmount();
    });

    it('does not intercept dragleave for a member without an editor or admin role in any group', async () => {
      vi.mocked(plakApi.overview).mockResolvedValue(structuredClone(filledData));
      vi.mocked(plakApi.me).mockResolvedValue(meWithGroupRole('team-aurora', 'reader'));

      const wrapper = mount(Overview, {
        global: { plugins: [makeRouter()], stubs: { teleport: true } },
      });
      await flushPromises();

      const section = wrapper.find('nldd-simple-section').element;
      section.dispatchEvent(dragEvent('dragenter', fakeDataTransfer([droppableFile()])));
      section.dispatchEvent(dragEvent('dragleave', fakeDataTransfer([droppableFile()])));
      await flushPromises();

      expect(wrapper.find('[data-testid="overview-drag-active"]').exists()).toBe(false);

      wrapper.unmount();
    });

    it('prevents the browser default while dragging over the target', async () => {
      vi.mocked(plakApi.overview).mockResolvedValue(structuredClone(filledData));
      vi.mocked(plakApi.me).mockResolvedValue(meWithGroupRole('team-aurora', 'editor'));

      const wrapper = mount(Overview, {
        global: { plugins: [makeRouter()], stubs: { teleport: true } },
      });
      await flushPromises();

      const section = wrapper.find('nldd-simple-section').element;
      const event = dragEvent('dragover', fakeDataTransfer([droppableFile()]));
      section.dispatchEvent(event);

      expect(event.defaultPrevented).toBe(true);

      wrapper.unmount();
    });

    it('does not intercept dragover for a member without an editor or admin role in any group', async () => {
      vi.mocked(plakApi.overview).mockResolvedValue(structuredClone(filledData));
      vi.mocked(plakApi.me).mockResolvedValue(meWithGroupRole('team-aurora', 'reader'));

      const wrapper = mount(Overview, {
        global: { plugins: [makeRouter()], stubs: { teleport: true } },
      });
      await flushPromises();

      const section = wrapper.find('nldd-simple-section').element;
      const event = dragEvent('dragover', fakeDataTransfer([droppableFile()]));
      section.dispatchEvent(event);

      expect(event.defaultPrevented).toBe(false);

      wrapper.unmount();
    });

    it('shows a dismissible error for a dropped file of an unsupported type', async () => {
      vi.mocked(plakApi.overview).mockResolvedValue(structuredClone(filledData));
      vi.mocked(plakApi.me).mockResolvedValue(meWithGroupRole('team-aurora', 'editor'));

      const wrapper = mount(Overview, {
        global: { plugins: [makeRouter()], stubs: { teleport: true } },
      });
      await flushPromises();
      const badFile = new File(['hoi'], 'site.pdf', { type: 'application/pdf' });

      wrapper
        .find('nldd-simple-section')
        .element.dispatchEvent(dragEvent('drop', fakeDataTransfer([badFile])));
      await flushPromises();

      const banner = wrapper.find('[data-testid="overview-drag-error"]');
      expect(banner.exists()).toBe(true);
      // Vue sets a custom element's prop as a DOM property, not an attribute.
      expect((banner.element as unknown as { text: string }).text).toContain('Sleep één bestand');
      expect(wrapper.findComponent(PublishSheet).props('open')).toBe(false);

      banner.element.dispatchEvent(new CustomEvent('dismiss'));
      await flushPromises();

      expect(wrapper.find('[data-testid="overview-drag-error"]').exists()).toBe(false);

      wrapper.unmount();
    });

    it('ignores a drop that carries nothing file-shaped, such as dragged text', async () => {
      vi.mocked(plakApi.overview).mockResolvedValue(structuredClone(filledData));
      vi.mocked(plakApi.me).mockResolvedValue(meWithGroupRole('team-aurora', 'editor'));

      const wrapper = mount(Overview, {
        global: { plugins: [makeRouter()], stubs: { teleport: true } },
      });
      await flushPromises();

      wrapper
        .find('nldd-simple-section')
        .element.dispatchEvent(dragEvent('drop', fakeDataTransfer([])));
      await flushPromises();

      expect(wrapper.find('[data-testid="overview-drag-error"]').exists()).toBe(false);
      expect(wrapper.findComponent(PublishSheet).props('open')).toBe(false);

      wrapper.unmount();
    });
  });

  describe('a11y', () => {
    beforeEach(() => {
      vi.mocked(plakApi.overview).mockResolvedValue(filledData);
    });

    it('has no axe violations in the filled state', async () => {
      const wrapper = mount(Overview, {
        attachTo: document.body,
        global: { plugins: [makeRouter()] },
      });
      await flushPromises();

      const result = await axe.run(wrapper.element as HTMLElement);
      expect(result.violations).toEqual([]);

      wrapper.unmount();
    });

    it('has no axe violations in the empty state with action', async () => {
      vi.mocked(plakApi.overview).mockResolvedValue({ groups: [] });

      const wrapper = mount(Overview, {
        attachTo: document.body,
        global: { plugins: [makeRouter()], stubs: { teleport: true } },
      });
      await flushPromises();

      const result = await axe.run(wrapper.element as HTMLElement);
      expect(result.violations).toEqual([]);

      wrapper.unmount();
    });
  });
});
