import { mount } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { makeMockBackend, type MockBackend } from '@/api/mock';
import TabMembers from './TabMembers.vue';
import { untilIdle } from './testHelpers';

let backend: MockBackend;

beforeEach(() => {
  backend = makeMockBackend();
  vi.stubGlobal('fetch', backend.fetch);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function makeWrapper() {
  return mount(TabMembers, { props: { group: 'nldd', site: 'website' } });
}

type Wrapper = ReturnType<typeof makeWrapper>;

function inheritedNames(wrapper: Wrapper): (string | undefined)[] {
  return wrapper
    .find('[data-testid="leden-via-groep-lijst"]')
    .findAll('nldd-table-row:not([slot="header"])')
    .map((row) => row.find('nldd-text-cell').attributes('text'));
}

function siteRows(wrapper: Wrapper) {
  return wrapper.find('[data-testid="leden-lijst"]').findAll('nldd-table-row:not([slot="header"])');
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
  await wrapper.find('[data-testid="siterol-formulier"]').trigger('submit');
  await untilIdle();
}

describe('TabMembers', () => {
  it('fetches everyone who has access to this site and splits them over the two blocks', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    expect(wrapper.find('[data-testid="leden-via-groep"]').find('summary').text()).toBe(
      '2 leden via de groep NLDD',
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
      groupSlug: 'nldd',
      siteSlug: 'website',
      identifier: 'oud@voorbeeld.nl',
      role: 'reader',
    });
  });

  it('leaves a group member in the via-group block after removing the site role', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    await runAction(wrapper, 'zoe@voorbeeld.nl', 'siterol-weghalen-zoe@voorbeeld.nl');

    expect(siteRowTexts(wrapper)).toEqual([['Wim Weg', 'Redacteur']]);
    expect(inheritedNames(wrapper)).toContain('Zoë de Wit');
    expect(wrapper.find('[data-testid="leden-via-groep"]').find('summary').text()).toBe(
      '3 leden via de groep NLDD',
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
});
