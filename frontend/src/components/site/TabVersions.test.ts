import { mount } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { makeMockBackend, MOCK_CONTENT_BASE, type MockBackend } from '@/api/mock';
import TabVersions from './TabVersions.vue';
import { serverErrorFetch, untilIdle } from './testHelpers';

let backend: MockBackend;

beforeEach(() => {
  backend = makeMockBackend();
  vi.stubGlobal('fetch', backend.fetch);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function makeWrapper() {
  return mount(TabVersions, {
    props: { group: 'team-aurora', site: 'website', contentBase: MOCK_CONTENT_BASE },
  });
}

/** A menu item fires `select`, not `click`. */
async function runAction(wrapper: ReturnType<typeof makeWrapper>, testid: string): Promise<void> {
  const item = wrapper.find(`[data-testid="${testid}"]`);
  expect(item.exists(), `action "${testid}" is missing`).toBe(true);
  item.element.dispatchEvent(new CustomEvent('select'));
  await untilIdle();
}

describe('TabVersions: states', () => {
  it('shows only the live history, with a marker on the current live version', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    expect(wrapper.find('[data-testid="versie-versie-1"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="versie-versie-0"]').exists()).toBe(true);
    // The preview version does not belong in this list.
    expect(wrapper.find('[data-testid="versie-versie-preview-42"]').exists()).toBe(false);

    // Every row carries the marker; the color and the accessible label say
    // which of the two states it is.
    const marker = (id: string) => wrapper.find(`[data-testid="live-marker-${id}"]`);
    expect(marker('versie-1').attributes('color')).toBe('success');
    expect(marker('versie-1').attributes('accessible-label')).toBe('Staat nu live');
    expect(marker('versie-0').attributes('color')).toBe('neutral');
    expect(marker('versie-0').attributes('accessible-label')).toBe('Staat niet live');
  });

  it('opens a version in a new tab, and says so', async () => {
    const open = vi.fn();
    vi.stubGlobal('open', open);

    const wrapper = makeWrapper();
    await untilIdle();

    // WCAG 3.2.5: announce a new window before it opens.
    expect(wrapper.find('[data-testid="bekijk-versie-0"]').attributes('details')).toBe(
      'nieuw tabblad',
    );

    await runAction(wrapper, 'bekijk-versie-0');

    expect(open).toHaveBeenCalledWith(
      'https://sites.plak.test/team-aurora/website/_version/versie-0/',
      '_blank',
      'noopener',
    );
  });

  it('renders the version list and its skeleton as box-tinted', async () => {
    const loadWrapper = makeWrapper();
    // Not finished loading: this is the skeleton behind the indicator.
    expect(loadWrapper.find('[data-testid="versies-skelet"]').attributes('variant')).toBe(
      'box-tinted',
    );

    const wrapper = makeWrapper();
    await untilIdle();

    // The tab sits on the same plain page background as the site overview;
    // box-base is for a list on an already tinted parent.
    expect(wrapper.find('nldd-list').attributes('variant')).toBe('box-tinted');
  });

  it('lets the marker open the row and keeps a single menu on the right', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    // The marker is the first cell, then a 12 spacer, only then the text.
    const live = wrapper.find('[data-testid="versie-versie-1"]');
    const cellen = [...live.element.children].map((c) => c.tagName.toLowerCase());
    expect(cellen[0]).toBe('nldd-cell');
    expect(live.find('nldd-cell').find('nldd-badge').exists()).toBe(true);
    expect(cellen[1]).toBe('nldd-spacer-cell');
    expect(cellen[2]).toBe('nldd-text-cell');
    expect(cellen[3]).toBe('nldd-cell');
    expect(live.findAll('nldd-icon-button')).toHaveLength(1);

    // Two separate buttons did not fit: at 320px and 200% text "Zet deze
    // versie live" stuck out 151px beyond the screen (WCAG 1.4.10). The
    // number of actions may no longer touch the row's width.
    const older = wrapper.find('[data-testid="versie-versie-0"]');
    expect(older.findAll('nldd-icon-button')).toHaveLength(1);
    expect(older.findAll('nldd-spacer-cell').map((c) => c.attributes('size'))).toEqual(['12']);
  });

  it('renders the rows with controls as a form list, without a disabled action', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    expect(wrapper.find('nldd-list').attributes('type')).toBe('form');
    expect(
      wrapper.find('[data-testid="live-zetten-versie-0"]').attributes('disabled'),
    ).toBeUndefined();
  });

  it('shows a static skeleton behind the delayed indicator while loading', async () => {
    vi.stubGlobal('fetch', () => new Promise<Response>(() => {}));

    const wrapper = makeWrapper();
    await untilIdle();

    const indicator = wrapper.find('nldd-activity-indicator');
    expect(indicator.exists()).toBe(true);
    // The default timing="delay" holds the indicator back 1000 ms; do not override.
    expect(indicator.attributes('timing')).toBeUndefined();

    // The skeleton is inside it, so the indicator dims that rather than filling
    // an empty screen; the heading and the list shape are already there.
    const skeleton = indicator.find('[data-testid="versies-skelet"]');
    expect(skeleton.exists()).toBe(true);
    expect(skeleton.attributes('aria-hidden')).toBe('true');
    expect(skeleton.attributes('type')).toBe('form');
    expect(skeleton.findAll('nldd-list-item')).toHaveLength(3);
    expect(wrapper.find('#kop-versies').exists()).toBe(true);
  });

  it('clears the skeleton once the versions are in', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    expect(wrapper.find('[data-testid="versies-skelet"]').exists()).toBe(false);
    expect(wrapper.find('nldd-activity-indicator').exists()).toBe(false);
  });

  it('names the row in the menu\'s accessible label', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    const row = wrapper.find('[data-testid="versie-versie-0"]');
    const stamp = row.find('nldd-text-cell').attributes('text');
    expect(row.find('nldd-icon-button').attributes('accessible-label')).toBe(
      `Acties voor ${stamp}`,
    );
    expect(wrapper.find('[data-testid="live-zetten-versie-0"]').attributes('text')).toBe(
      'Zet deze versie live',
    );
  });

  it('names the repository for a CI deploy, not "Gepubliceerd door CI"', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    const row = wrapper.find('[data-testid="versie-versie-1"]');
    expect(row.find('nldd-text-cell').attributes('supporting-text')).toBe(
      'Gepubliceerd door github.com/team-aurora/website',
    );
  });

  it('falls back to "Handmatig geüpload" without a known uploader', async () => {
    const seeded = backend.data.versions.find((v) => v.id === 'versie-0')!;
    seeded.createdByName = null;

    const wrapper = makeWrapper();
    await untilIdle();

    expect(
      wrapper.find('[data-testid="versie-versie-0"]').find('nldd-text-cell').attributes('supporting-text'),
    ).toBe('Handmatig geüpload');
  });

  it('falls back to "Gepubliceerd door CI" without a known repository', async () => {
    const seeded = backend.data.versions.find((v) => v.id === 'versie-1')!;
    seeded.createdByRepository = null;

    const wrapper = makeWrapper();
    await untilIdle();

    expect(
      wrapper.find('[data-testid="versie-versie-1"]').find('nldd-text-cell').attributes('supporting-text'),
    ).toBe('Gepubliceerd door CI');
  });

  it('shows the empty state without live versions', async () => {
    backend.data.versions = [];

    const wrapper = makeWrapper();
    await untilIdle();

    const empty = wrapper.find('nldd-inline-dialog[data-testid="versies-leeg"]');
    expect(empty.exists()).toBe(true);
    expect(empty.attributes('text')).toBe('Nog geen live-versies');
    expect(empty.attributes('supporting-text')).toContain('tabblad Overzicht');
  });

  it('shows an error message on a server error', async () => {
    vi.stubGlobal('fetch', serverErrorFetch());

    const wrapper = makeWrapper();
    await untilIdle();

    expect(wrapper.html()).toContain('Serverfout');
  });
});

describe('TabVersions: setting live', () => {
  it('sets an older version live and moves the marker', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    // The current live version has no "live zetten" action.
    expect(wrapper.find('[data-testid="live-zetten-versie-1"]').exists()).toBe(false);

    await runAction(wrapper, 'live-zetten-versie-0');

    expect(backend.data.sites[0]!.liveVersionId).toBe('versie-0');
    expect(wrapper.find('[data-testid="live-marker-versie-0"]').attributes('color')).toBe('success');
    expect(wrapper.find('[data-testid="live-marker-versie-1"]').attributes('color')).toBe('neutral');
    expect(wrapper.emitted('changed')).toBeTruthy();
    expect(wrapper.find('nldd-notification[text="Versie live gezet"]').exists()).toBe(true);
  });

  it('reports it when setting live fails', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    vi.stubGlobal('fetch', serverErrorFetch());
    await runAction(wrapper, 'live-zetten-versie-0');

    const notice = wrapper.find('nldd-notification[variant="critical"]');
    expect(notice.attributes('text')).toBe('Versie niet live gezet');
    expect(wrapper.html()).toContain('Serverfout');
  });

  it('reports a generic failure when setting live throws something other than an ApiError', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    vi.stubGlobal('fetch', () => Promise.reject(new TypeError('network down')));
    await runAction(wrapper, 'live-zetten-versie-0');

    const notice = wrapper.find('nldd-notification[variant="critical"]');
    expect(notice.attributes('text')).toBe('Versie niet live gezet');
    expect(notice.attributes('supporting-text')).toBe('Live zetten is niet gelukt.');
  });
});
