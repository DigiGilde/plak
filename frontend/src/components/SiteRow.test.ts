import '@nldd/design-system';

import { flushPromises, mount } from '@vue/test-utils';
import { describe, expect, it } from 'vitest';

import type { Site } from '../api/types';
import SiteRow from './SiteRow.vue';

const site: Site = {
  groupSlug: 'nldd',
  slug: 'website',
  title: 'NLDD website',
  access: { base: 'public', keys: false, invitees: false },
  externalSources: false,
  sandbox: true,
  liveVersionId: 'versie-1',
  createdBy: 'dev-beheerder',
  hasLiveVersion: true,
  lastPublishedAt: '2026-07-17T14:32:00.000Z',
  previewCount: 2,
};

describe('SiteRow', () => {
  it('is a table row with the title as a link to the site detail', async () => {
    const wrapper = mount(SiteRow, { props: { site }, attachTo: document.body });
    await flushPromises();

    expect(wrapper.element.tagName.toLowerCase()).toBe('nldd-table-row');
    // A table row is not a link itself (nldd-table-row has no href), so the
    // title carries the navigation.
    const link = wrapper.find('nldd-title-cell nldd-link');
    expect(link.attributes('href')).toBe('/nldd/website');
    expect(link.text()).toBe('NLDD website');

    wrapper.unmount();
  });

  it('shows title, slug, visibility and last publication', async () => {
    const wrapper = mount(SiteRow, { props: { site }, attachTo: document.body });
    await flushPromises();

    expect(wrapper.find('nldd-title-cell').attributes('supporting-text')).toBe('website');

    const badges = wrapper.findAll('nldd-badge');
    // status dot (first column) + visibility badge
    expect(badges.length).toBeGreaterThanOrEqual(2);
    expect(badges.find((b) => b.attributes('text') === 'Publiek')).toBeDefined();

    const texts = wrapper.findAll('nldd-text-cell').map((cell) => cell.attributes('text'));
    // The preview count lives on the site's own previews tab, not in the row.
    expect(texts).not.toContain('2');
    expect(texts.some((text) => text?.includes('17'))).toBe(true);

    wrapper.unmount();
  });

  it('shows "Geen live versie" when the site has no live version', async () => {
    const wrapper = mount(SiteRow, {
      props: { site: { ...site, hasLiveVersion: false, liveVersionId: null } },
      attachTo: document.body,
    });
    await flushPromises();

    const statusDot = wrapper.find('nldd-badge');
    expect(statusDot.attributes('color')).toBe('neutral');
    expect(statusDot.attributes('accessible-label')).toBe('Geen live versie');

    wrapper.unmount();
  });

  it('shows "Nog niet gepubliceerd" when there has not been a deploy yet', async () => {
    const wrapper = mount(SiteRow, {
      props: { site: { ...site, lastPublishedAt: null } },
      attachTo: document.body,
    });
    await flushPromises();

    const texts = wrapper.findAll('nldd-text-cell').map((cell) => cell.attributes('text'));
    expect(texts).toContain('Nog niet gepubliceerd');

    wrapper.unmount();
  });

  it('places the cells in the table\'s column order', async () => {
    const wrapper = mount(SiteRow, { props: { site }, attachTo: document.body });
    await flushPromises();

    // The row places its cells in order in the table's subgrid: if this order
    // differs from the columns in Overview.vue, everything behind the
    // divergence shifts one column along.
    const cells = [...wrapper.element.children].map((cell) => cell.tagName.toLowerCase());
    expect(cells).toEqual([
      'nldd-cell',
      'nldd-title-cell',
      'nldd-cell',
      'nldd-text-cell',
    ]);

    wrapper.unmount();
  });

  it('omits the secondary cells while the table is narrow', async () => {
    const wrapper = mount(SiteRow, { props: { site }, attachTo: document.body });
    await flushPromises();

    // Below the md breakpoint the table keeps only status and title; the other
    // two cells drop out, exactly as the sm column list in Overview.vue leaves
    // just two. hide-below is measured against the width of the table, not that
    // of the screen.
    const cells = [...wrapper.element.children];
    expect(cells.map((cell) => cell.getAttribute('hide-below'))).toEqual([
      null,
      null,
      'md',
      'md',
    ]);

    wrapper.unmount();
  });

  it('shows no action buttons at row level', async () => {
    const wrapper = mount(SiteRow, { props: { site }, attachTo: document.body });
    await flushPromises();

    expect(wrapper.findAll('nldd-button')).toHaveLength(0);

    wrapper.unmount();
  });
});
