import '@nldd/design-system';

import { mount } from '@vue/test-utils';
import { describe, expect, it } from 'vitest';

import type { Site } from '@/api/types';

import TabSites from './TabSites.vue';

function site(slug: string, title: string): Site {
  return {
    id: `id-${slug}`,
    groupSlug: 'team-aurora',
    slug,
    title,
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
}

function makeWrapper(sites: Site[] = []) {
  return { wrapper: mount(TabSites, { props: { sites } }) };
}

describe('TabSites', () => {

  it('links every site of the group as a row to its own site page', () => {
    const { wrapper } = makeWrapper([site('website', 'Team Aurora website')]);

    // The link sits in the title cell, as on the site overview: the row is a
    // table row now, not a list item that is itself a link. Unmounted, Lit does
    // not reflect its attributes, so read the property Vue sets.
    const link = wrapper.find('[data-testid="site-website"]').find('nldd-link');
    expect((link.element as HTMLElement & { href?: string }).href).toBe('/team-aurora/website');
    expect(link.text()).toBe('Team Aurora website');
  });

  it('offers no publish button of its own: the group page already has one in the header', () => {
    const { wrapper } = makeWrapper([site('website', 'Team Aurora website')]);

    // Two buttons for the same task, one blue and one grey, only invite
    // the question of which of the two you need.
    expect(wrapper.findAll('nldd-button')).toHaveLength(0);
    expect(wrapper.find('[data-testid="sites-publish"]').exists()).toBe(false);
  });

  it('shows the empty state in the list\'s empty slot when there are no sites', () => {
    const { wrapper } = makeWrapper();

    const empty = wrapper.find('[data-testid="sites-empty"]');
    expect(empty.attributes('slot')).toBe('empty');
    expect((empty.element as HTMLElement & { text?: string }).text).toBe(
      'Nog geen sites in deze groep',
    );
    expect(wrapper.findAll('nldd-list-item')).toHaveLength(0);
  });
});
