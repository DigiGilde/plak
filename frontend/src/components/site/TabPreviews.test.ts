import { mount } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { makeMockBackend, MOCK_CONTENT_BASE, type MockBackend } from '@/api/mock';
import TabPreviews from './TabPreviews.vue';
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
  return mount(TabPreviews, {
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

describe('TabPreviews: states', () => {
  it('shows ref, URL, last updated and expiry date (filled)', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    const row = wrapper.find('[data-testid="preview-pr-42"]');
    expect(row.exists()).toBe(true);
    expect(row.html()).toContain('pr-42');
    // The API returns a content path; the link belongs absolute on the content host.
    expect(row.find('nldd-link').attributes('href')).toBe(
      'https://sites.plak.test/team-aurora/website/_preview/pr-42/',
    );
    expect(row.html()).toContain('Bijgewerkt');
    expect(row.html()).toContain('Vervalt');
  });

  it('renders the rows with controls as a form list, with the preview icon', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    const list = wrapper.find('nldd-list');
    expect(list.attributes('type')).toBe('form');
    expect(
      wrapper.find('[data-testid="preview-pr-42"]').find('nldd-icon-cell').attributes('icon'),
    ).toBe('git-pull-request');
  });

  it('shows the empty state without previews', async () => {
    backend.data.previews = [];

    const wrapper = makeWrapper();
    await untilIdle();

    const empty = wrapper.find('nldd-inline-dialog[data-testid="previews-leeg"]');
    expect(empty.exists()).toBe(true);
    expect(empty.attributes('text')).toBe('Geen previews');
    expect(empty.attributes('icon')).toBe('git-pull-request');
    expect(empty.attributes('supporting-text')).toContain('tabblad Deploy');
  });

  it('shows an error message on a server error', async () => {
    vi.stubGlobal('fetch', serverErrorFetch());

    const wrapper = makeWrapper();
    await untilIdle();

    expect(wrapper.html()).toContain('Serverfout');
  });
});

describe('TabPreviews: actions', () => {
  it('sets a base override via the row menu', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    expect(backend.data.previews[0]!.accessOverride).toEqual({
      base: 'nobody',
      keys: false,
      invitees: true,
    });
    // The set value shows as an overline on the row, not just in the menu.
    expect(wrapper.find('nldd-title-cell').attributes('overline')).toBe(
      'Toegang: Alleen genodigden',
    );
    // Exactly one option is checked: it is a radio group over the base.
    const checked = wrapper
      .findAll('nldd-menu-item[type="radio"]')
      .filter((item) => item.attributes('selected') !== undefined);
    expect(checked.map((item) => item.attributes('text'))).toEqual(['Alleen via een link of uitnodiging']);

    await runAction(wrapper, 'override-pr-42-public');

    // Choosing a base sets the whole override, so the exceptions turn off:
    // a half override would be a base from the preview with exceptions
    // from the site, and that does not exist.
    expect(backend.data.previews[0]!.accessOverride).toEqual({
      base: 'public',
      keys: false,
      invitees: false,
    });
    expect(wrapper.find('nldd-title-cell').attributes('overline')).toBe('Toegang: Publiek');
  });

  it('turns on an exception to the override without touching the base', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    await runAction(wrapper, 'override-pr-42-sleutels');

    expect(backend.data.previews[0]!.accessOverride).toEqual({
      base: 'nobody',
      keys: true,
      invitees: true,
    });
    expect(wrapper.find('nldd-title-cell').attributes('overline')).toBe(
      'Toegang: Alleen geheime links en genodigden',
    );
  });

  it('toggles the genodigden exception both ways', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    // Seeded with invitees already on: the first toggle turns it off.
    await runAction(wrapper, 'override-pr-42-genodigden');
    expect(backend.data.previews[0]!.accessOverride).toMatchObject({ invitees: false });

    await runAction(wrapper, 'override-pr-42-genodigden');
    expect(backend.data.previews[0]!.accessOverride).toMatchObject({ invitees: true });
  });

  it('does not offer the exceptions while the preview follows the site', async () => {
    backend.data.previews[0]!.accessOverride = null;
    const wrapper = makeWrapper();
    await untilIdle();

    // Without its own access there is nothing to widen, and turning it on
    // would silently pick a base that no one chose.
    expect(wrapper.find('[data-testid="override-pr-42-sleutels"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="override-pr-42-genodigden"]').exists()).toBe(false);
  });

  it('removes the override with the "Zelfde als site" option', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    await runAction(wrapper, 'override-pr-42-site');

    expect(backend.data.previews[0]!.accessOverride).toBeNull();
    expect(wrapper.find('nldd-title-cell').attributes('overline')).toBe(
      'Toegang: Zelfde als site',
    );
  });

  it('leaves other previews untouched when one override is set', async () => {
    backend.data.previews.push({
      ...backend.data.previews[0]!,
      ref: 'pr-43',
      accessOverride: { base: 'sso', keys: false, invitees: false },
    });
    const wrapper = makeWrapper();
    await untilIdle();

    await runAction(wrapper, 'override-pr-42-public');

    expect(backend.data.previews[0]!.accessOverride).toEqual({
      base: 'public',
      keys: false,
      invitees: false,
    });
    expect(backend.data.previews[1]!.accessOverride).toEqual({
      base: 'sso',
      keys: false,
      invitees: false,
    });
  });

  it('removes a preview including its version', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    wrapper
      .find('[data-testid="verwijder-pr-42"]')
      .element.dispatchEvent(new CustomEvent('select'));
    await wrapper.vm.$nextTick();

    // Optimistic: the row is gone before the server has answered.
    expect(wrapper.find('[data-testid="preview-pr-42"]').exists()).toBe(false);

    await untilIdle();
    expect(backend.data.previews).toHaveLength(0);
    expect(backend.data.versions.some((v) => v.id === 'versie-preview-42')).toBe(false);
  });

  it('reverts a failed removal and reports it', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    vi.stubGlobal('fetch', serverErrorFetch());
    await runAction(wrapper, 'verwijder-pr-42');

    expect(wrapper.find('[data-testid="preview-pr-42"]').exists()).toBe(true);
    const notice = wrapper.find('nldd-notification[variant="critical"]');
    expect(notice.attributes('text')).toBe('Preview pr-42 niet verwijderd');
  });

  it('reverts a failed access change and reports it', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    vi.stubGlobal('fetch', serverErrorFetch());
    await runAction(wrapper, 'override-pr-42-public');

    expect(wrapper.find('nldd-title-cell').attributes('overline')).toBe(
      'Toegang: Alleen genodigden',
    );
    expect(wrapper.find('nldd-notification[variant="critical"]').exists()).toBe(true);
  });

  it('reports a generic failure when the access change throws something other than an ApiError', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    vi.stubGlobal('fetch', () => Promise.reject(new TypeError('network down')));
    await runAction(wrapper, 'override-pr-42-public');

    expect(wrapper.find('nldd-notification[variant="critical"]').attributes('supporting-text')).toBe(
      'Opslaan is niet gelukt.',
    );
  });
});
