import { flushPromises, mount } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { nextTick } from 'vue';

import { ApiError } from '@/api/client';
import type { MemberSuggestion, Role, SiteMember } from '@/api/types';
import { SEARCH_DEBOUNCE_MS } from '@/composables/memberSearch';

import SiteMembersManager from './SiteMembersManager.vue';

function memberWith(overrides: Partial<SiteMember>): SiteMember {
  return {
    groupSlug: 'nldd',
    siteSlug: 'website',
    memberId: 'lid-7',
    identifier: 'lid@voorbeeld.nl',
    name: 'lid@voorbeeld.nl',
    email: 'lid@voorbeeld.nl',
    groupRole: null,
    siteRole: null,
    effectiveRole: 'reader',
    ...overrides,
  };
}

/** Reaches the site through the group only: no site role of their own. */
const viaGroup = memberWith({
  memberId: 'lid-3',
  identifier: 'ada@voorbeeld.nl',
  name: 'Ada Vermeer',
  email: 'ada@voorbeeld.nl',
  groupRole: 'editor',
  effectiveRole: 'editor',
});

/** An outsider: a role on this site and nothing in the group. */
const siteOnly = memberWith({
  memberId: 'lid-8',
  identifier: 'buiten@voorbeeld.nl',
  name: 'Bo Buiten',
  email: 'buiten@voorbeeld.nl',
  siteRole: 'reader',
  effectiveRole: 'reader',
});

/** A group member whose site role widens what the group gives them. */
const both = memberWith({
  memberId: 'lid-4',
  identifier: 'zoe@voorbeeld.nl',
  name: 'Zoë de Wit',
  email: 'zoe@voorbeeld.nl',
  groupRole: 'reader',
  siteRole: 'admin',
  effectiveRole: 'admin',
});

function suggestionOf(overrides: Partial<MemberSuggestion> = {}): MemberSuggestion {
  return {
    identifier: 'ada@voorbeeld.nl',
    name: 'Ada Vermeer',
    email: 'ada@voorbeeld.nl',
    alreadyMember: false,
    ...overrides,
  };
}

function mountComponent(props: {
  members?: SiteMember[];
  add?: (identifier: string, role: Role) => Promise<SiteMember>;
  remove?: (memberId: string) => Promise<void>;
  setRole?: (identifier: string, role: Role) => Promise<SiteMember>;
  search?: (query: string) => Promise<MemberSuggestion[]>;
}) {
  return mount(SiteMembersManager, {
    props: {
      members: props.members ?? [],
      group: 'nldd',
      groupName: 'NLDD',
      add: props.add ?? vi.fn(),
      remove: props.remove ?? vi.fn(),
      setRole: props.setRole ?? vi.fn(),
      search: props.search ?? vi.fn().mockResolvedValue([]),
    },
    global: { stubs: { teleport: true } },
  });
}

type Wrapper = ReturnType<typeof mountComponent>;

function details(wrapper: Wrapper) {
  return wrapper.find('[data-testid="leden-via-groep"]');
}

function inheritedRows(wrapper: Wrapper) {
  return wrapper
    .find('[data-testid="leden-via-groep-lijst"]')
    .findAll('nldd-table-row:not([slot="header"])');
}

function siteRows(wrapper: Wrapper) {
  return wrapper.find('[data-testid="leden-lijst"]').findAll('nldd-table-row:not([slot="header"])');
}

/** The text per cell of one row, in column order. */
function cellTexts(row: ReturnType<typeof siteRows>[number]) {
  return row.findAll('nldd-text-cell').map((cell) => cell.attributes('text'));
}

function rowOf(wrapper: Wrapper, identifier: string) {
  const row = siteRows(wrapper).find((candidate) => candidate.html().includes(identifier));
  expect(row, `row for ${identifier} is missing`).toBeDefined();
  return row!;
}

/**
 * An action from the menu of one row. Scoped to that row on purpose: every row
 * carries its own menu in the light DOM, so an unscoped query finds the first
 * one in the document, not the one you meant.
 */
function actionOf(wrapper: Wrapper, identifier: string, testid: string) {
  return rowOf(wrapper, identifier).find(`[data-testid="${testid}"]`);
}

function actionTexts(wrapper: Wrapper, identifier: string) {
  return rowOf(wrapper, identifier)
    .findAll('nldd-menu-item')
    .map((item) => item.attributes('text'));
}

/** A menu item fires `select`, not `click`. */
async function runAction(wrapper: Wrapper, identifier: string, testid: string): Promise<void> {
  const item = actionOf(wrapper, identifier, testid);
  expect(item.exists(), `action "${testid}" is missing for ${identifier}`).toBe(true);
  item.element.dispatchEvent(new CustomEvent('select'));
  await flushPromises();
}

function comboBox(wrapper: Wrapper) {
  return wrapper.find('nldd-combo-box[name="identifier"]');
}

/** Typing: nldd-combo-box reports what stands in the input as `detail.value`. */
async function typeIn(wrapper: Wrapper, value: string): Promise<void> {
  comboBox(wrapper).element.dispatchEvent(new CustomEvent('input', { detail: { value } }));
  await nextTick();
}

/**
 * Picking a suggestion: the combo box puts the item's `value` in its own value
 * and reports that as a `change`, not as a `select` on the item.
 */
async function pickSuggestion(wrapper: Wrapper, identifier: string): Promise<void> {
  comboBox(wrapper).element.dispatchEvent(
    new CustomEvent('change', { detail: { value: identifier } }),
  );
  await nextTick();
}

function suggestionItems(wrapper: Wrapper) {
  return wrapper.find('[data-testid="siterol-suggesties"]').findAll('nldd-menu-item');
}

/** Waits out the debounce and lets the answer land. */
async function afterDebounce(): Promise<void> {
  vi.advanceTimersByTime(SEARCH_DEBOUNCE_MS);
  await flushPromises();
}

async function fillInAndSubmit(wrapper: Wrapper, value: string): Promise<void> {
  await typeIn(wrapper, value);
  await wrapper.find('[data-testid="siterol-formulier"]').trigger('submit');
}

describe('SiteMembersManager (two blocks)', () => {
  it('separates who comes in via the group from who has their own role here', () => {
    const wrapper = mountComponent({ members: [viaGroup, siteOnly, both] });

    expect(inheritedRows(wrapper).map((row) => cellTexts(row)[0])).toEqual(['Ada Vermeer']);
    expect(siteRows(wrapper).map((row) => cellTexts(row)[0])).toEqual(['Bo Buiten', 'Zoë de Wit']);
  });

  it('collapses the via-group block and states the count in the summary', () => {
    const wrapper = mountComponent({ members: [viaGroup, siteOnly] });

    const block = details(wrapper);
    expect(block.element.tagName.toLowerCase()).toBe('details');
    // Closed on load: open is not set on it.
    expect(block.attributes('open')).toBeUndefined();
    expect(block.find('summary').text()).toBe('1 lid via de groep NLDD');
  });

  it('counts in the plural once more than one member comes in via the group', () => {
    const wrapper = mountComponent({
      members: [viaGroup, memberWith({ identifier: 'bea@voorbeeld.nl', groupRole: 'admin' })],
    });

    expect(details(wrapper).find('summary').text()).toBe('2 leden via de groep NLDD');
  });

  it('omits the block as long as no one comes in via the group', () => {
    const wrapper = mountComponent({ members: [siteOnly] });

    expect(details(wrapper).exists()).toBe(false);
  });

  it('gives the via-group rows no action menu, but a way to the group', () => {
    const wrapper = mountComponent({ members: [viaGroup, siteOnly] });

    expect(inheritedRows(wrapper)[0]!.find('nldd-icon-button').exists()).toBe(false);
    const link = wrapper.find('[data-testid="leden-naar-groep"]');
    expect(link.attributes('href')).toBe('/nldd/-/members');
    expect(link.text()).toBe('leden van de groep NLDD');
  });
});

describe('SiteMembersManager (role that applies)', () => {
  it('shows the widest of the two roles, not just the site role', () => {
    const wrapper = mountComponent({ members: [viaGroup, both] });

    // Group role reader, site role admin: admin is what counts.
    expect(cellTexts(rowOf(wrapper, 'zoe@voorbeeld.nl'))).toEqual(['Zoë de Wit', 'Beheerder']);
    expect(cellTexts(inheritedRows(wrapper)[0]!)).toEqual(['Ada Vermeer', 'Redacteur']);
  });

  it('says so when the group role overrules the site role', () => {
    const wrapper = mountComponent({
      members: [
        memberWith({ groupRole: 'admin', siteRole: 'reader', effectiveRole: 'admin' }),
        siteOnly,
      ],
    });

    const overruled = rowOf(wrapper, 'lid@voorbeeld.nl').findAll('nldd-text-cell')[1]!;
    expect(overruled.attributes('text')).toBe('Beheerder');
    expect(overruled.attributes('supporting-text')).toBe('via de groep');
    // Without a group role there is nothing to explain.
    expect(
      rowOf(wrapper, 'buiten@voorbeeld.nl').findAll('nldd-text-cell')[1]!.attributes(
        'supporting-text',
      ),
    ).toBeUndefined();
  });
});

describe('SiteMembersManager (row menu)', () => {
  it('offers the two site roles this member does not have yet, plus removal', () => {
    const wrapper = mountComponent({ members: [siteOnly] });

    expect(actionTexts(wrapper, 'buiten@voorbeeld.nl')).toEqual([
      'Maak redacteur',
      'Maak beheerder',
      'Rol weghalen',
    ]);
    // An icon per role, so the short label can still be told apart.
    expect(
      actionOf(wrapper, 'buiten@voorbeeld.nl', 'siterol-buiten@voorbeeld.nl-admin').attributes('icon'),
    ).toBe('key');
    expect(
      actionOf(wrapper, 'buiten@voorbeeld.nl', 'siterol-weghalen-buiten@voorbeeld.nl').attributes(
        'destructive',
      ),
    ).toBeDefined();
  });

  it('warns only where a choice widens nothing', () => {
    const wrapper = mountComponent({ members: [both] });

    // Group role reader: setting reader on this site removes nothing, and
    // the short label cannot say that.
    expect(
      actionOf(wrapper, 'zoe@voorbeeld.nl', 'siterol-zoe@voorbeeld.nl-reader').attributes(
        'details',
      ),
    ).toBe('Blijft lezer via de groep');
    // A choice that does widen speaks for itself and gets no second line.
    expect(
      actionOf(wrapper, 'zoe@voorbeeld.nl', 'siterol-zoe@voorbeeld.nl-editor').attributes(
        'details',
      ),
    ).toBeUndefined();
  });

  it('says on removal whether someone can still reach this site afterwards', () => {
    const wrapper = mountComponent({ members: [siteOnly, both] });

    expect(
      actionOf(wrapper, 'buiten@voorbeeld.nl', 'siterol-weghalen-buiten@voorbeeld.nl').attributes(
        'details',
      ),
    ).toBe('Kan daarna niet meer bij deze site');
    expect(
      actionOf(wrapper, 'zoe@voorbeeld.nl', 'siterol-weghalen-zoe@voorbeeld.nl').attributes(
        'details',
      ),
    ).toBe('Blijft lezer via de groep');
  });

  it('changes the site role from the row menu and reports the confirmed member', async () => {
    const changed = { ...siteOnly, siteRole: 'editor' as Role, effectiveRole: 'editor' as Role };
    const setRole = vi.fn().mockResolvedValue(changed);
    const wrapper = mountComponent({ members: [siteOnly], setRole });

    await runAction(wrapper, 'buiten@voorbeeld.nl', 'siterol-buiten@voorbeeld.nl-editor');

    expect(setRole).toHaveBeenCalledWith('lid-8', 'editor');
    expect(wrapper.emitted('roleChanged')?.[0]).toEqual([changed]);
  });

  it('removes the site role and puts the group member straight into the block above', async () => {
    let confirm!: () => void;
    const remove = vi.fn().mockImplementation(
      () =>
        new Promise<void>((resolve) => {
          confirm = resolve;
        }),
    );
    const wrapper = mountComponent({ members: [both], remove });

    await runAction(wrapper, 'zoe@voorbeeld.nl', 'siterol-weghalen-zoe@voorbeeld.nl');

    expect(siteRows(wrapper)).toHaveLength(0);
    expect(cellTexts(inheritedRows(wrapper)[0]!)).toEqual(['Zoë de Wit', 'Lezer']);
    expect(remove).toHaveBeenCalledWith('lid-4');

    confirm();
    await flushPromises();

    expect(wrapper.emitted('removed')?.[0]).toEqual(['zoe@voorbeeld.nl']);
  });

  it('makes whoever has no group role disappear from both lists', async () => {
    let confirm!: () => void;
    const remove = vi.fn().mockImplementation(
      () =>
        new Promise<void>((resolve) => {
          confirm = resolve;
        }),
    );
    const wrapper = mountComponent({ members: [siteOnly], remove });

    await runAction(wrapper, 'buiten@voorbeeld.nl', 'siterol-weghalen-buiten@voorbeeld.nl');

    // No group role to fall back on: the row does not come back anywhere.
    expect(siteRows(wrapper)).toHaveLength(0);
    expect(details(wrapper).exists()).toBe(false);

    confirm();
    await flushPromises();

    expect(wrapper.emitted('removed')?.[0]).toEqual(['buiten@voorbeeld.nl']);
  });
});

describe('SiteMembersManager (granting a role)', () => {
  it('puts the address field before the role: first who, then what they may do', () => {
    const wrapper = mountComponent({});

    const fields = wrapper.findAll('nldd-form-field');
    expect(fields.map((field) => field.attributes('label'))).toEqual([
      'Naam of e-mailadres',
      'Rol',
    ]);
  });

  it('offers reader as the default role', () => {
    const wrapper = mountComponent({});

    const select = wrapper.find('[data-testid="siterol-nieuw"]');
    expect(select.findAll('option').map((option) => option.text().trim())).toEqual([
      'Lezer',
      'Redacteur',
      'Beheerder',
    ]);
    expect((select.element as HTMLSelectElement).value).toBe('reader');
  });

  it('states in the form that a site role only widens access', () => {
    const wrapper = mountComponent({});

    const section = wrapper.find('nldd-form-section');
    expect(section.attributes('supporting-text')).toContain('verbreedt alleen');
    expect(section.attributes('supporting-text')).toContain('bij de groep');
  });

  it('grants a role of reader without a choice and gets ahead of the row', async () => {
    let confirm!: (member: SiteMember) => void;
    const add = vi.fn().mockImplementation(
      () =>
        new Promise<SiteMember>((resolve) => {
          confirm = resolve;
        }),
    );
    const wrapper = mountComponent({ add });

    await fillInAndSubmit(wrapper, 'buiten@voorbeeld.nl');

    expect(add).toHaveBeenCalledWith('buiten@voorbeeld.nl', 'reader');
    expect(cellTexts(siteRows(wrapper)[0]!)).toEqual(['buiten@voorbeeld.nl', 'Lezer']);

    confirm(siteOnly);
    await flushPromises();

    expect(wrapper.emitted('added')?.[0]).toEqual([siteOnly]);
  });

  it('does not get ahead for someone already in the list via the group', async () => {
    let confirm!: (member: SiteMember) => void;
    const add = vi.fn().mockImplementation(
      () =>
        new Promise<SiteMember>((resolve) => {
          confirm = resolve;
        }),
    );
    const wrapper = mountComponent({ members: [viaGroup], add });

    await fillInAndSubmit(wrapper, 'ada@voorbeeld.nl');

    expect(add).toHaveBeenCalledWith('ada@voorbeeld.nl', 'reader');
    expect(siteRows(wrapper)).toHaveLength(0);
    expect(inheritedRows(wrapper)).toHaveLength(1);

    confirm({ ...viaGroup, siteRole: 'reader' });
    await flushPromises();

    expect(wrapper.emitted('added')).toHaveLength(1);
  });
});

describe('SiteMembersManager (name without a choice)', () => {
  it('refuses a typed name without a chosen suggestion, without calling the callback', async () => {
    const add = vi.fn();
    const wrapper = mountComponent({ add });

    await fillInAndSubmit(wrapper, 'Cato Jansen');
    await flushPromises();

    expect(add).not.toHaveBeenCalled();
    expect(comboBox(wrapper).attributes('invalid')).toBeDefined();
    // Named on the control, not set on the item directly: the real
    // nldd-validation-list reads `unmet` off its control and reflects it onto
    // the matching item itself.
    expect(comboBox(wrapper).attributes('unmet')).toBe('siterol-toevoegen-geen-email');
  });

  it('still submits a chosen suggestion', async () => {
    const search = vi.fn().mockResolvedValue([suggestionOf()]);
    const add = vi.fn().mockResolvedValue(siteOnly);
    const wrapper = mountComponent({ search, add });

    await typeIn(wrapper, 'ada');
    await pickSuggestion(wrapper, 'ada@voorbeeld.nl');
    await wrapper.find('[data-testid="siterol-formulier"]').trigger('submit');
    await flushPromises();

    expect(add).toHaveBeenCalledWith('ada@voorbeeld.nl', 'reader');
  });

  it('submits a typed email address that appears in no suggestion', async () => {
    const add = vi.fn().mockResolvedValue(siteOnly);
    const wrapper = mountComponent({ add });

    await fillInAndSubmit(wrapper, 'nieuw@voorbeeld.nl');
    await flushPromises();

    expect(add).toHaveBeenCalledWith('nieuw@voorbeeld.nl', 'reader');
  });
});

describe('SiteMembersManager (error)', () => {
  it('rolls back a failed addition and reports it with a notification', async () => {
    const add = vi.fn().mockRejectedValue(
      new ApiError({
        type: 'about:blank',
        title: 'Onbekend lid',
        status: 404,
        detail: 'Onbekend lid; diegene moet eerst zelf inloggen op het beheer.',
      }),
    );
    const wrapper = mountComponent({ add });

    await fillInAndSubmit(wrapper, 'onbekend@voorbeeld.nl');
    await flushPromises();

    expect(siteRows(wrapper)).toHaveLength(0);
    const notice = wrapper.find('nldd-notification');
    expect(notice.attributes('variant')).toBe('critical');
    expect(notice.attributes('text')).toBe(
      'onbekend@voorbeeld.nl een rol op deze site geven is niet gelukt',
    );
    expect(notice.attributes('supporting-text')).toBe(
      'Onbekend lid; diegene moet eerst zelf inloggen op het beheer.',
    );
    expect(wrapper.emitted('added')).toBeUndefined();
  });

  it('leaves the row on the old role when changing fails, with a notification', async () => {
    const setRole = vi
      .fn()
      .mockRejectedValue(new ApiError({ type: 'about:blank', title: 'Serverfout', status: 500 }));
    const wrapper = mountComponent({ members: [both], setRole });

    await runAction(wrapper, 'zoe@voorbeeld.nl', 'siterol-zoe@voorbeeld.nl-editor');

    expect(cellTexts(rowOf(wrapper, 'zoe@voorbeeld.nl'))).toEqual(['Zoë de Wit', 'Beheerder']);
    expect(wrapper.find('nldd-notification').attributes('text')).toBe(
      'De siterol van Zoë de Wit wijzigen is niet gelukt',
    );
    expect(wrapper.emitted('roleChanged')).toBeUndefined();
  });

  it('puts a failed removal back in the list, with a notification', async () => {
    const remove = vi
      .fn()
      .mockRejectedValue(new ApiError({ type: 'about:blank', title: 'Serverfout', status: 500 }));
    const wrapper = mountComponent({ members: [siteOnly], remove });

    await runAction(wrapper, 'buiten@voorbeeld.nl', 'siterol-weghalen-buiten@voorbeeld.nl');

    expect(siteRows(wrapper)).toHaveLength(1);
    expect(wrapper.find('nldd-notification').attributes('text')).toBe(
      'De siterol van Bo Buiten weghalen is niet gelukt',
    );
    expect(wrapper.emitted('removed')).toBeUndefined();
  });
});

describe('SiteMembersManager (suggestions)', () => {
  // Only the timers of the debounce: flushPromises rides on setImmediate, and
  // faking that too would make every await in these tests hang.
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('searches only from two characters, and not on every keystroke', async () => {
    const search = vi.fn().mockResolvedValue([suggestionOf()]);
    const wrapper = mountComponent({ search });

    await typeIn(wrapper, 'a');
    await afterDebounce();

    expect(search).not.toHaveBeenCalled();
    expect(suggestionItems(wrapper)).toHaveLength(0);

    await typeIn(wrapper, 'ad');
    await typeIn(wrapper, 'ada');
    expect(search).not.toHaveBeenCalled();

    await afterDebounce();

    expect(search).toHaveBeenCalledTimes(1);
    expect(search).toHaveBeenCalledWith('ada');
    expect(suggestionItems(wrapper).map((item) => item.attributes('text'))).toEqual([
      'Ada Vermeer',
    ]);
  });

  it('puts the address in the field when picking a suggestion, with the name shown', async () => {
    const search = vi.fn().mockResolvedValue([suggestionOf()]);
    const add = vi.fn().mockResolvedValue(viaGroup);
    const wrapper = mountComponent({ search, add });

    await typeIn(wrapper, 'ada');
    await afterDebounce();
    await pickSuggestion(wrapper, 'ada@voorbeeld.nl');

    expect(comboBox(wrapper).attributes('text')).toBe('Ada Vermeer');
    expect(comboBox(wrapper).attributes('value')).toBe('ada@voorbeeld.nl');

    await wrapper.find('[data-testid="siterol-formulier"]').trigger('submit');
    await flushPromises();

    expect(add).toHaveBeenCalledWith('ada@voorbeeld.nl', 'reader');
    expect(comboBox(wrapper).attributes('value')).toBe('');
  });

  it('marks whoever already has a role here and does not let them be chosen', async () => {
    const search = vi.fn().mockResolvedValue([
      suggestionOf({ alreadyMember: true }),
      suggestionOf({
        identifier: 'sanne@voorbeeld.nl',
        name: 'Sanne Vermeulen',
        email: 'sanne@voorbeeld.nl',
      }),
    ]);
    const wrapper = mountComponent({ search });

    await typeIn(wrapper, 'ver');
    await afterDebounce();

    const items = suggestionItems(wrapper);
    expect(items.map((item) => item.attributes('text'))).toEqual([
      'Ada Vermeer (al lid)',
      'Sanne Vermeulen',
    ]);
    expect(items[0]!.attributes('disabled')).toBeDefined();
    expect(items[1]!.attributes('disabled')).toBeUndefined();
    expect(items.map((item) => item.attributes('details'))).toEqual([
      'ada@voorbeeld.nl',
      'sanne@voorbeeld.nl',
    ]);
  });

  it('submits a typed address that appears in no suggestion', async () => {
    const search = vi.fn().mockResolvedValue([suggestionOf()]);
    const add = vi.fn().mockResolvedValue(siteOnly);
    const wrapper = mountComponent({ search, add });

    await typeIn(wrapper, 'buiten@voorbeeld.nl');
    await afterDebounce();
    await wrapper.find('[data-testid="siterol-formulier"]').trigger('submit');
    await flushPromises();

    expect(add).toHaveBeenCalledWith('buiten@voorbeeld.nl', 'reader');
  });

  it('keeps the form usable when the search fails', async () => {
    const search = vi
      .fn()
      .mockRejectedValue(new ApiError({ type: 'about:blank', title: 'Serverfout', status: 500 }));
    const add = vi.fn().mockResolvedValue(siteOnly);
    const wrapper = mountComponent({ search, add });

    await typeIn(wrapper, 'buiten@voorbeeld.nl');
    await afterDebounce();

    expect(suggestionItems(wrapper)).toHaveLength(0);
    expect(wrapper.find('nldd-notification').exists()).toBe(false);

    await wrapper.find('[data-testid="siterol-formulier"]').trigger('submit');
    await flushPromises();

    expect(add).toHaveBeenCalledWith('buiten@voorbeeld.nl', 'reader');
  });

  it('shows someone without a name in their profile by their address', async () => {
    const search = vi.fn().mockResolvedValue([
      suggestionOf({
        identifier: 'kaal@voorbeeld.nl',
        name: '',
        email: 'kaal@voorbeeld.nl',
      }),
    ]);
    const wrapper = mountComponent({ search });

    await typeIn(wrapper, 'kaal');
    await afterDebounce();

    // An SSO profile does not have to carry a name. The address then stands
    // where the name would, and not twice on one row.
    const item = suggestionItems(wrapper)[0]!;
    expect(item.attributes('text')).toBe('kaal@voorbeeld.nl');
    expect(item.attributes('details')).toBeUndefined();

    await pickSuggestion(wrapper, 'kaal@voorbeeld.nl');

    expect(comboBox(wrapper).attributes('text')).toBe('kaal@voorbeeld.nl');
  });

  it('leaves no search behind after the form is gone', async () => {
    const search = vi.fn().mockResolvedValue([suggestionOf()]);
    const wrapper = mountComponent({ search });

    await typeIn(wrapper, 'ada');
    wrapper.unmount();
    await afterDebounce();

    // The debounce outlives the component otherwise, and fires at a form that
    // is no longer on screen.
    expect(search).not.toHaveBeenCalled();
  });
});
