import '@nldd/design-system';

import { flushPromises, mount } from '@vue/test-utils';
import axe from 'axe-core';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createMemoryHistory, createRouter, type Router } from 'vue-router';

import * as plakApi from '../api/plak';
import type { Group, Overview as OverviewData, Site } from '../api/types';
import NewGroupSheet from '../components/NewGroupSheet.vue';
import { _resetBreadcrumbs, breadcrumbsFor } from '../composables/breadcrumbs';
import Groups from './Groups.vue';

/**
 * jsdom 25 reflects the ARIA properties of ElementInternals onto a
 * `_internalContentAttributeMap` it never creates itself, so writing throws a
 * TypeError. The fields in NewGroupSheet set those properties as soon as they
 * are attached to the document; see Overview.test.ts.
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
  createGroup: vi.fn(),
}));

function site(slug: string, live: boolean): Site {
  return {
    id: `id-${slug}`,
    groupSlug: 'team-aurora',
    slug,
    title: slug,
    access: { base: 'public', keys: false, invitees: false },
    externalSources: false,
    sandbox: true,
    liveVersionsKept: null,
    liveVersionId: live ? 'versie-1' : null,
    createdBy: 'dev-beheerder',
    hasLiveVersion: live,
    lastPublishedAt: live ? '2026-07-17T14:32:00.000Z' : null,
    previewCount: 0,
    previousSlugs: [],
  };
}

const filledData: OverviewData = {
  groups: [
    {
      group: { slug: 'team-aurora', name: 'Team Aurora', defaultAccess: { base: 'public', keys: false, invitees: false }, previousSlugs: [] },
      sites: [site('website', true), site('handboek', false)],
    },
    {
      group: { slug: 'leeg', name: 'Lege groep', defaultAccess: { base: 'site_team', keys: false, invitees: false }, previousSlugs: [] },
      sites: [],
    },
  ],
};

const newGroup: Group = { slug: 'team', name: 'Team', defaultAccess: { base: 'site_team', keys: false, invitees: false }, previousSlugs: [] };

function makeRouter(): Router {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'overview', component: { template: '<div />' } },
      { path: '/-/groups', name: 'groups', component: Groups },
    ],
  });
}

/**
 * `hecht` only for the axe runs: axe judges the DOM inside the document. If the
 * sheet really hangs in there it calls showModal(), which jsdom does not have;
 * that is why the sheet tests run detached, just as in Overview.test.
 */
async function mountComponent(options: { teleport?: boolean; attach?: boolean } = {}) {
  const router = makeRouter();
  await router.push('/-/groups');
  await router.isReady();
  const wrapper = mount(Groups, {
    ...(options.attach ? { attachTo: document.body } : {}),
    global: { plugins: [router], stubs: options.teleport ? { teleport: true } : {} },
  });
  await flushPromises();
  return wrapper;
}

function property(el: Element, name: string): string {
  return (el as unknown as Record<string, string | undefined>)[name] ?? '';
}

function cellTexts(wrapper: ReturnType<typeof mount>, selector: string): string[] {
  return wrapper.findAll(selector).map((cell) => property(cell.element, 'text'));
}

describe('Groups', () => {
  beforeEach(() => {
    _resetBreadcrumbs();
  });

  afterEach(() => {
    vi.mocked(plakApi.overview).mockReset();
    vi.mocked(plakApi.createGroup).mockReset();
    document.body.innerHTML = '';
  });

  it('loading: shows an indicator and no rows yet', async () => {
    let release!: (data: OverviewData) => void;
    vi.mocked(plakApi.overview).mockImplementation(
      () => new Promise((resolve) => (release = resolve)),
    );

    const wrapper = await mountComponent();

    expect(wrapper.find('nldd-activity-indicator').exists()).toBe(true);
    expect(wrapper.findAll('nldd-list-item')).toHaveLength(0);

    release(filledData);
    await flushPromises();

    expect(wrapper.find('nldd-activity-indicator').exists()).toBe(false);
    expect(wrapper.findAll('nldd-list-item')).toHaveLength(2);

    wrapper.unmount();
  });

  it('shows per group the name, the slug and what the overview already knows', async () => {
    vi.mocked(plakApi.overview).mockResolvedValue(structuredClone(filledData));

    const wrapper = await mountComponent();

    expect(cellTexts(wrapper, 'nldd-title-cell')).toEqual(['Team Aurora', 'Lege groep']);
    expect(wrapper.findAll('nldd-title-cell').map((c) => property(c.element, 'supportingText')))
      .toEqual(['team-aurora', 'leeg']);
    // Both counts come out of the same response; no second request goes out
    // per group.
    expect(cellTexts(wrapper, 'nldd-text-cell')).toEqual([
      '2 sites',
      '1 site online',
      'Nog geen sites',
      'Niets online',
    ]);
    expect(plakApi.overview).toHaveBeenCalledTimes(1);

    wrapper.unmount();
  });

  it('counts a single site and a single live site in the singular', async () => {
    vi.mocked(plakApi.overview).mockResolvedValue({
      groups: [
        {
          group: { slug: 'team-aurora', name: 'Team Aurora', defaultAccess: { base: 'public', keys: false, invitees: false }, previousSlugs: [] },
          sites: [site('website', true)],
        },
      ],
    });

    const wrapper = await mountComponent();

    expect(cellTexts(wrapper, 'nldd-text-cell')).toEqual(['1 site', '1 site online']);

    wrapper.unmount();
  });

  it('counts more than one live site in the plural, distinct from the total', async () => {
    vi.mocked(plakApi.overview).mockResolvedValue({
      groups: [
        {
          group: { slug: 'team-aurora', name: 'Team Aurora', defaultAccess: { base: 'public', keys: false, invitees: false }, previousSlugs: [] },
          sites: [site('website', true), site('handboek', true), site('intranet', false)],
        },
      ],
    });

    const wrapper = await mountComponent();

    expect(cellTexts(wrapper, 'nldd-text-cell')).toEqual(['3 sites', '2 sites online']);

    wrapper.unmount();
  });

  it('links each row to the group page', async () => {
    vi.mocked(plakApi.overview).mockResolvedValue(structuredClone(filledData));

    const wrapper = await mountComponent();

    expect(wrapper.findAll('nldd-list-item').map((row) => property(row.element, 'href'))).toEqual(
      ['/team-aurora', '/leeg'],
    );

    wrapper.unmount();
  });

  it('sets the breadcrumb path under the overview', async () => {
    vi.mocked(plakApi.overview).mockResolvedValue(structuredClone(filledData));

    const wrapper = await mountComponent();

    expect(breadcrumbsFor('/-/groups')).toEqual([
      { text: 'Overzicht', href: '/' },
      { text: 'Groepen' },
    ]);

    wrapper.unmount();
  });

  it('empty: says you are not a member of anything yet and offers the same single action', async () => {
    vi.mocked(plakApi.overview).mockResolvedValue({ groups: [] });

    const wrapper = await mountComponent({ teleport: true });

    const notice = wrapper.find('nldd-list nldd-inline-dialog[slot="empty"]');
    expect(property(notice.element, 'text')).toBe('Je zit nog in geen enkele groep.');
    expect(wrapper.findAll('nldd-list-item')).toHaveLength(0);
    expect(wrapper.find('[data-testid="groups-create"]').exists()).toBe(true);

    wrapper.unmount();
  });

  it('error: shows an error banner instead of an empty list', async () => {
    vi.mocked(plakApi.overview).mockRejectedValue(new Error('Kon groepen niet laden'));

    const wrapper = await mountComponent();

    expect(wrapper.find('nldd-banner').exists()).toBe(true);
    expect(wrapper.find('nldd-list').exists()).toBe(false);
    // A page that could not load its groups offers no action on them either.
    expect(wrapper.find('[data-testid="groups-create"]').exists()).toBe(false);

    wrapper.unmount();
  });

  it('creates a group beside the heading and adds it right away', async () => {
    vi.mocked(plakApi.overview).mockResolvedValue(structuredClone(filledData));
    vi.mocked(plakApi.createGroup).mockResolvedValue(newGroup);

    const wrapper = await mountComponent({ teleport: true });
    expect(wrapper.findComponent(NewGroupSheet).props('open')).toBe(false);

    // Beside the h1, like on the overview: below a long list the action ends
    // up out of reach.
    const button = wrapper.find('[data-testid="groups-create"]');
    expect(wrapper.find('.page-header [data-testid="groups-create"]').exists()).toBe(true);
    expect(property(button.element, 'variant')).toBe('primary');

    await button.trigger('click');
    expect(wrapper.findComponent(NewGroupSheet).props('open')).toBe(true);

    const form = wrapper.find('nldd-sheet[accessible-label="Nieuwe groep"]').element;
    for (const [name, value] of [
      ['name', 'Team'],
      ['slug', 'team'],
    ]) {
      [...form.querySelectorAll('nldd-text-field')]
        .find((field) => property(field, 'name') === name)!
        .dispatchEvent(new CustomEvent('input', { detail: { value: value } }));
    }
    form
      .querySelector('nldd-form')!
      .dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    await flushPromises();

    expect(plakApi.createGroup).toHaveBeenCalledWith('Team', 'team');
    expect(cellTexts(wrapper, 'nldd-title-cell')).toEqual(['Team Aurora', 'Lege groep', 'Team']);
    // A fresh group has nothing yet; the counts should say so.
    expect(cellTexts(wrapper, 'nldd-text-cell').slice(-2)).toEqual([
      'Nog geen sites',
      'Niets online',
    ]);

    wrapper.unmount();
  });

  describe('a11y', () => {
    it('has no axe violations in the filled state', async () => {
      vi.mocked(plakApi.overview).mockResolvedValue(structuredClone(filledData));

      const wrapper = await mountComponent({ attach: true });

      const result = await axe.run(wrapper.element as HTMLElement);
      expect(result.violations, JSON.stringify(result.violations, null, 2)).toEqual([]);

      wrapper.unmount();
    });

    it('has no axe violations in the empty state', async () => {
      vi.mocked(plakApi.overview).mockResolvedValue({ groups: [] });

      const wrapper = await mountComponent({ teleport: true, attach: true });

      const result = await axe.run(wrapper.element as HTMLElement);
      expect(result.violations, JSON.stringify(result.violations, null, 2)).toEqual([]);

      wrapper.unmount();
    });
  });
});
