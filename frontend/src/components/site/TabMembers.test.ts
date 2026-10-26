import { flushPromises, mount } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { makeMockBackend, type MockBackend } from '@/api/mock';
import { SEARCH_DEBOUNCE_MS } from '@/composables/memberSearch';
import TabMembers from './TabMembers.vue';
import { provideSiteGroup, serverErrorFetch, untilIdle } from './testHelpers';

let backend: MockBackend;

beforeEach(() => {
  backend = makeMockBackend();
  vi.stubGlobal('fetch', backend.fetch);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function makeWrapper() {
  return mount(TabMembers, {
    props: { group: 'team-aurora', site: 'website' },
    global: { stubs: { teleport: true }, provide: provideSiteGroup(backend) },
  });
}

type Wrapper = ReturnType<typeof makeWrapper>;

function inheritedNames(wrapper: Wrapper): (string | undefined)[] {
  return wrapper
    .find('[data-testid="members-via-group-list"]')
    .findAll('nldd-table-row:not([slot="header"])')
    .map((row) => row.find('nldd-text-cell').attributes('text'));
}

function siteRows(wrapper: Wrapper) {
  return wrapper.find('[data-testid="members-list"]').findAll('nldd-table-row:not([slot="header"])');
}

function siteRowTexts(wrapper: Wrapper): (string | undefined)[][] {
  return siteRows(wrapper).map((row) =>
    row.findAll('nldd-text-cell').map((cell) => cell.attributes('text')),
  );
}

/** Scoped to the row: every row carries its own menu in the light DOM. */
async function runAction(wrapper: Wrapper, identifier: string, testid: string): Promise<void> {
  const row = siteRows(wrapper).find((candidate) => candidate.html().includes(identifier));
  expect(row, `row for ${identifier} is missing`).toBeDefined();
  row!.find(`[data-testid="${testid}"]`).element.dispatchEvent(new CustomEvent('select'));
  await untilIdle();
}

async function fillInAndSubmit(wrapper: Wrapper, value: string): Promise<void> {
  const field = wrapper.find('nldd-combo-box[name="identifier"]').element;
  field.dispatchEvent(new CustomEvent('input', { detail: { value } }));
  // Only a pick is an identifier: the combo box reports one as a `change`.
  field.dispatchEvent(new CustomEvent('change', { detail: { value } }));
  await wrapper.find('[data-testid="site-role-form"]').trigger('submit');
  await untilIdle();
}

describe('TabMembers', () => {
  it('fetches everyone who has access to this site and splits them over the two blocks', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    expect(wrapper.find('[data-testid="members-via-group"]').find('summary').text()).toBe(
      '2 leden via de groep Team Aurora',
    );
    expect(inheritedNames(wrapper)).toEqual(['Bea Heerder', 'Ada Vermeer']);
    // Zoë is reader in the group and admin on this site: admin counts.
    expect(siteRowTexts(wrapper)).toEqual([
      ['Zoë de Wit', 'Beheerder'],
      ['Wim Weg', 'Redacteur'],
    ]);
  });

  it('gives an existing member a role on only this site', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    await fillInAndSubmit(wrapper, 'oud@voorbeeld.nl');

    expect(siteRowTexts(wrapper)).toContainEqual(['Karel Oud', 'Lezer']);
    expect(backend.data.siteRoles).toContainEqual({
      groupSlug: 'team-aurora',
      siteSlug: 'website',
      identifier: 'oud@voorbeeld.nl',
      role: 'reader',
    });
  });

  it('leaves a group member in the via-group block after removing the site role', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    await runAction(wrapper, 'zoe@voorbeeld.nl', 'site-role-remove-zoe@voorbeeld.nl');
    await wrapper.find('[data-testid="confirm-continue"]').trigger('click');
    await untilIdle();

    expect(siteRowTexts(wrapper)).toEqual([['Wim Weg', 'Redacteur']]);
    expect(inheritedNames(wrapper)).toContain('Zoë de Wit');
    expect(wrapper.find('[data-testid="members-via-group"]').find('summary').text()).toBe(
      '3 leden via de groep Team Aurora',
    );
  });

  it('reports that someone must have logged in themselves first', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    await fillInAndSubmit(wrapper, 'nooit-ingelogd@voorbeeld.nl');

    expect(siteRows(wrapper)).toHaveLength(2);
    expect(wrapper.find('nldd-notification').attributes('supporting-text')).toBe(
      'Onbekend lid; diegene moet eerst zelf inloggen op het beheer.',
    );
  });

  it('changes the role of an existing site member in place', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    await runAction(wrapper, 'weg@voorbeeld.nl', 'site-role-weg@voorbeeld.nl-reader');

    expect(siteRowTexts(wrapper)).toContainEqual(['Wim Weg', 'Lezer']);
    expect(backend.data.siteRoles).toContainEqual({
      groupSlug: 'team-aurora',
      siteSlug: 'website',
      identifier: 'weg@voorbeeld.nl',
      role: 'reader',
    });
  });

  it('drops a site-only member entirely once the site role is removed', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    await runAction(wrapper, 'weg@voorbeeld.nl', 'site-role-remove-weg@voorbeeld.nl');
    await wrapper.find('[data-testid="confirm-continue"]').trigger('click');
    await untilIdle();

    expect(siteRowTexts(wrapper)).toEqual([['Zoë de Wit', 'Beheerder']]);
    expect(inheritedNames(wrapper)).not.toContain('Wim Weg');
  });

  it('reports a server error while loading the members', async () => {
    vi.stubGlobal('fetch', serverErrorFetch());
    const wrapper = makeWrapper();
    await untilIdle();

    expect(wrapper.find('nldd-banner').attributes('text')).toBe('Serverfout');
  });

  it('searches the site itself for suggestions as the group does', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    try {
      const wrapper = makeWrapper();
      await untilIdle();

      wrapper
        .find('nldd-combo-box[name="identifier"]')
        .element.dispatchEvent(new CustomEvent('input', { detail: { value: 'Sanne' } }));
      vi.advanceTimersByTime(SEARCH_DEBOUNCE_MS);
      await flushPromises();

      expect(
        wrapper
          .find('[data-testid="site-role-suggestions"]')
          .findAll('nldd-menu-item')
          .map((item) => item.attributes('value')),
      ).toContain('sanne@voorbeeld.nl');
    } finally {
      vi.useRealTimers();
    }
  });
});
