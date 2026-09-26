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
    groupRole: null,
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

/** The confirmation the remove action opens; teleport is stubbed away. */
function confirmation(wrapper: Wrapper) {
  return wrapper.find('nldd-modal-dialog');
}

/** Asking to remove a site role and going through with it. */
async function removeRole(wrapper: Wrapper, identifier: string): Promise<void> {
  await runAction(wrapper, identifier, `siterol-weghalen-${identifier}`);
  await wrapper.find('[data-testid="bevestig-doorgaan"]').trigger('click');
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

/**
 * Clicking into the field, with a stand-in for the Popover API that jsdom
 * lacks. Answers whether the menu was asked to open.
 */
async function clickField(wrapper: Wrapper) {
  // The listeners go on once the template ref is filled, a tick after mount.
  await nextTick();
  const menu = wrapper.find('[data-testid="siterol-suggesties"]').element as HTMLElement & {
    showPopover?: () => void;
  };
  const opened = vi.fn();
  menu.showPopover = opened;
  comboBox(wrapper).element.dispatchEvent(new MouseEvent('click', { composed: true }));
  return opened;
}

/** Waits out the debounce and lets the answer land. */
async function afterDebounce(): Promise<void> {
  vi.advanceTimersByTime(SEARCH_DEBOUNCE_MS);
  await flushPromises();
}

/** Typing and submitting without ever picking: what the form now refuses. */
async function typeAndSubmit(wrapper: Wrapper, value: string): Promise<void> {
  await typeIn(wrapper, value);
  await wrapper.find('[data-testid="siterol-formulier"]').trigger('submit');
}

/** The whole way in: type, pick the person the list answered with, submit. */
async function fillInAndSubmit(wrapper: Wrapper, value: string): Promise<void> {
  await typeIn(wrapper, value);
  await pickSuggestion(wrapper, value);
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

  it('leaves the consequence of removal out of the menu item', () => {
    const wrapper = mountComponent({ members: [siteOnly, both] });

    for (const identifier of ['buiten@voorbeeld.nl', 'zoe@voorbeeld.nl']) {
      expect(
        actionOf(wrapper, identifier, `siterol-weghalen-${identifier}`).attributes('details'),
      ).toBeUndefined();
    }
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

    await removeRole(wrapper, 'zoe@voorbeeld.nl');

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

    await removeRole(wrapper, 'buiten@voorbeeld.nl');

    // No group role to fall back on: the row does not come back anywhere.
    expect(siteRows(wrapper)).toHaveLength(0);
    expect(details(wrapper).exists()).toBe(false);

    confirm();
    await flushPromises();

    expect(wrapper.emitted('removed')?.[0]).toEqual(['buiten@voorbeeld.nl']);
  });
});

describe('SiteMembersManager (confirming a removal)', () => {
  it('asks first and takes nothing away until the answer is yes', async () => {
    const remove = vi.fn().mockResolvedValue(undefined);
    const wrapper = mountComponent({ members: [siteOnly], remove });

    await runAction(wrapper, 'buiten@voorbeeld.nl', 'siterol-weghalen-buiten@voorbeeld.nl');

    expect(siteRows(wrapper)).toHaveLength(1);
    expect(remove).not.toHaveBeenCalled();

    await wrapper.find('[data-testid="bevestig-annuleren"]').trigger('click');
    await flushPromises();

    expect(remove).not.toHaveBeenCalled();
    expect(siteRows(wrapper)).toHaveLength(1);
  });

  it('names the role someone keeps through the group', async () => {
    const wrapper = mountComponent({ members: [both] });

    await runAction(wrapper, 'zoe@voorbeeld.nl', 'siterol-weghalen-zoe@voorbeeld.nl');

    expect(confirmation(wrapper).attributes('text')).toBe('Siterol van Zoë de Wit weghalen?');
    expect(confirmation(wrapper).attributes('supporting-text')).toBe(
      'Zoë de Wit houdt toegang tot deze site als lezer via de groep.',
    );
  });

  it('says outright that someone without a group role loses this site', async () => {
    const wrapper = mountComponent({ members: [siteOnly] });

    await runAction(wrapper, 'buiten@voorbeeld.nl', 'siterol-weghalen-buiten@voorbeeld.nl');

    expect(confirmation(wrapper).attributes('supporting-text')).toBe(
      'Bo Buiten verliest de toegang tot deze site: er is geen rol via de groep die dat opvangt.',
    );
  });
});

describe('SiteMembersManager (what the starting list says about itself)', () => {
  it('says in the list itself that typing searches beyond the group', () => {
    const wrapper = mountComponent({});

    const note = wrapper.find('[data-testid="siterol-verder-zoeken"]');
    expect(note.text()).toBe('Typ twee letters om verder te zoeken, ook buiten deze groep.');
    // Outside role="menu" and holding no control: not an option, not a tab stop.
    expect(note.element.closest('nldd-menu-item')).toBeNull();
    expect(note.element.closest('[slot="footer"]')).not.toBeNull();
    expect(note.findAll('nldd-button, a, input, button')).toHaveLength(0);
  });

  it('drops the note once the typing has taken over from the group', async () => {
    const wrapper = mountComponent({ search: vi.fn().mockResolvedValue([]) });

    await typeIn(wrapper, 'wi');

    expect(wrapper.find('[data-testid="siterol-verder-zoeken"]').exists()).toBe(false);
  });
});

describe('SiteMembersManager (a site role that would change nothing)', () => {
  /** Narrowest first, so index order is rank order, as ROLES is. */
  const RANKS: Role[] = ['reader', 'editor', 'admin'];

  /** Every group role against every site role, straight from widest(). */
  const COMBINATIONS = RANKS.flatMap((groupRole) =>
    RANKS.map((siteRole) => ({
      groupRole,
      siteRole,
      widens: RANKS.indexOf(siteRole) > RANKS.indexOf(groupRole),
    })),
  );

  it.each(COMBINATIONS)(
    'says a $siteRole role changes nothing for a $groupRole of the group: $widens',
    async ({ groupRole, siteRole, widens }) => {
      const wrapper = mountComponent({
        members: [memberWith({ identifier: 'zoe@voorbeeld.nl', groupRole, siteRole: null })],
      });

      await pickSuggestion(wrapper, 'zoe@voorbeeld.nl');
      await wrapper.find('[data-testid="siterol-nieuw"]').setValue(siteRole);

      expect(wrapper.find('[data-testid="siterol-geen-effect"]').exists()).toBe(!widens);
    },
  );

  it('finds the group role on the row when neither list holds the person', async () => {
    // Someone with a site role of their own is in no starting list, and after a
    // failed add the search answers are cleared, so the rows are what is left.
    const wrapper = mountComponent({ members: [both] });

    await pickSuggestion(wrapper, 'zoe@voorbeeld.nl');
    await wrapper.find('[data-testid="siterol-nieuw"]').setValue('reader');

    expect(wrapper.find('[data-testid="siterol-geen-effect"]').text()).toContain('lezer');
  });

  it('says nothing about a role for someone the group does not know', async () => {
    const wrapper = mountComponent({
      search: vi.fn().mockResolvedValue([
        {
          identifier: 'buiten@voorbeeld.nl',
          name: 'Bo Buiten',
          email: 'buiten@voorbeeld.nl',
          alreadyMember: false,
          groupRole: null,
        },
      ]),
    });

    await typeIn(wrapper, 'buiten');
    await vi.waitFor(() => expect(suggestionItems(wrapper)).toHaveLength(1));
    await pickSuggestion(wrapper, 'buiten@voorbeeld.nl');

    expect(wrapper.find('[data-testid="siterol-geen-effect"]').exists()).toBe(false);
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

  it('says in the hint what the list holds and what typing adds to it', () => {
    const help = mountComponent({})
      .find('nldd-form-field[label="Naam of e-mailadres"] > nldd-form-field-help-text')
      .text();

    expect(help).toContain('begint met de leden van deze groep');
    expect(help).toContain('ook buiten de groep');
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

describe('SiteMembersManager (nothing picked)', () => {
  it('refuses what was only typed, name or address, without calling the callback', async () => {
    const add = vi.fn();
    const wrapper = mountComponent({ add });

    await typeAndSubmit(wrapper, 'Cato Jansen');
    await flushPromises();
    await typeAndSubmit(wrapper, 'nieuw@voorbeeld.nl');
    await flushPromises();

    expect(add).not.toHaveBeenCalled();
    expect(comboBox(wrapper).attributes('invalid')).toBeDefined();

    // A refused submit leaves the search it triggered standing; unmounting is
    // what disposes that timer, and a stray one fires into a later test.
    wrapper.unmount();
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

  it('takes an earlier pick back once typing goes on', async () => {
    const search = vi.fn().mockResolvedValue([suggestionOf()]);
    const add = vi.fn();
    const wrapper = mountComponent({ search, add });

    await typeIn(wrapper, 'ada');
    await pickSuggestion(wrapper, 'ada@voorbeeld.nl');
    await typeAndSubmit(wrapper, 'ada@voorbeeld.n');
    await flushPromises();

    expect(add).not.toHaveBeenCalled();
    expect(comboBox(wrapper).attributes('invalid')).toBeDefined();

    wrapper.unmount();
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

    await removeRole(wrapper, 'buiten@voorbeeld.nl');

    expect(siteRows(wrapper)).toHaveLength(1);
    expect(wrapper.find('nldd-notification').attributes('text')).toBe(
      'De siterol van Bo Buiten weghalen is niet gelukt',
    );
    expect(wrapper.emitted('removed')).toBeUndefined();
  });

  it('adds where the role is changed to a refusal that only says what happened', async () => {
    const add = vi.fn().mockRejectedValue(
      new ApiError({
        type: 'about:blank',
        title: 'Conflict',
        status: 409,
        detail: 'Dit lid heeft al een eigen rol op deze site.',
        code: 'ALREADY_SITE_MEMBER',
      }),
    );
    const wrapper = mountComponent({ add });

    await fillInAndSubmit(wrapper, 'lid@voorbeeld.nl');
    await flushPromises();

    expect(wrapper.find('nldd-notification').attributes('supporting-text')).toBe(
      'Dit lid heeft al een eigen rol op deze site. Die rol wijzig je in het menu achter de regel van dit lid.',
    );
  });
});

describe('SiteMembersManager (the list before anything is typed)', () => {
  it('starts with the members of this group, each with the role they have there', () => {
    const wrapper = mountComponent({ members: [viaGroup, both, siteOnly] });

    // Zoë and Bo have a site role of their own: this form is not where that
    // changes, so only Ada is left to offer.
    const items = suggestionItems(wrapper);
    expect(items.map((item) => item.attributes('text'))).toEqual([
      'Ada Vermeer, ada@voorbeeld.nl, redacteur via de groep',
    ]);
    // Nothing in `details`: that cell does not shrink and wrecks a 320 px row.
    expect(items[0]!.attributes('details')).toBeUndefined();
    expect(items[0]!.attributes('value')).toBe('ada@voorbeeld.nl');
    expect(items[0]!.attributes('disabled')).toBeUndefined();
  });

  it('shows a group member without a name in their profile by their address', () => {
    const wrapper = mountComponent({
      members: [
        memberWith({
          identifier: 'kaal@voorbeeld.nl',
          name: '',
          email: 'kaal@voorbeeld.nl',
          groupRole: 'reader',
        }),
      ],
    });

    const item = suggestionItems(wrapper)[0]!;
    expect(item.attributes('text')).toBe('kaal@voorbeeld.nl, lezer via de groep');
    expect(item.attributes('details')).toBeUndefined();
  });

  it('offers nothing where the group has no one left to offer', () => {
    expect(suggestionItems(mountComponent({ members: [siteOnly] }))).toHaveLength(0);
    expect(suggestionItems(mountComponent({}))).toHaveLength(0);
  });

  it('opens the list on arrival, and leaves it shut when there is nothing in it', async () => {
    expect(await clickField(mountComponent({ members: [viaGroup] }))).toHaveBeenCalledTimes(1);

    // An empty menu opening on its own would only read as a broken dropdown.
    expect(await clickField(mountComponent({ members: [siteOnly] }))).not.toHaveBeenCalled();
  });

  it('submits the address of a group member picked from the starting list', async () => {
    const add = vi.fn().mockResolvedValue({ ...viaGroup, siteRole: 'reader' });
    const wrapper = mountComponent({ members: [viaGroup], add });

    await pickSuggestion(wrapper, 'ada@voorbeeld.nl');

    expect(comboBox(wrapper).attributes('text')).toBe('Ada Vermeer');
    expect(comboBox(wrapper).attributes('value')).toBe('ada@voorbeeld.nl');

    await wrapper.find('[data-testid="siterol-formulier"]').trigger('submit');
    await flushPromises();

    expect(add).toHaveBeenCalledWith('ada@voorbeeld.nl', 'reader');
  });

  it('says which of the three empty lists it is looking at', async () => {
    const search = vi.fn().mockResolvedValue([]);
    const wrapper = mountComponent({ search });
    const menu = () => wrapper.find('[data-testid="siterol-suggesties"]').attributes('empty-text');

    expect(menu()).toBe('Typ twee letters om te zoeken');

    await typeIn(wrapper, 'ad');
    expect(menu()).toBe('Zoeken...');

    wrapper.unmount();
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
      'Ada Vermeer, ada@voorbeeld.nl',
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

  it('says nobody was found only once the answer is in', async () => {
    const search = vi.fn().mockResolvedValue([]);
    const wrapper = mountComponent({ search });
    const menu = () => wrapper.find('[data-testid="siterol-suggesties"]').attributes('empty-text');

    await typeIn(wrapper, 'ad');
    expect(menu()).toBe('Zoeken...');

    await afterDebounce();
    expect(menu()).toBe('Niemand gevonden');
  });

  it('marks whoever already has a site role, and still lets them be chosen', async () => {
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
    // Name, address and note as three separated facts, so the name is a name.
    expect(items.map((item) => item.attributes('text'))).toEqual([
      'Ada Vermeer, ada@voorbeeld.nl, heeft al een siterol',
      'Sanne Vermeulen, sanne@voorbeeld.nl',
    ]);
    // No greying out: acting on it answers with what the server says about it,
    // which a row that cannot be clicked never gets to say.
    expect(items[0]!.attributes('disabled')).toBeUndefined();
    expect(items[1]!.attributes('disabled')).toBeUndefined();
    // Nothing in `details`: that cell does not shrink and wrecks a 320 px row.
    expect(items.map((item) => item.attributes('details'))).toEqual([undefined, undefined]);
  });

  it('offers a groepslid, with the role it already reaches this site with', async () => {
    const search = vi.fn().mockResolvedValue([
      suggestionOf({ groupRole: 'reader' }),
      suggestionOf({
        identifier: 'sanne@voorbeeld.nl',
        name: 'Sanne Vermeulen',
        email: 'sanne@voorbeeld.nl',
        groupRole: 'admin',
      }),
    ]);
    const wrapper = mountComponent({ search });

    await typeIn(wrapper, 'ver');
    await afterDebounce();

    const items = suggestionItems(wrapper);
    expect(items.map((item) => item.attributes('text'))).toEqual([
      'Ada Vermeer, ada@voorbeeld.nl, lezer via de groep',
      'Sanne Vermeulen, sanne@voorbeeld.nl, beheerder via de groep',
    ]);
    expect(items[0]!.attributes('disabled')).toBeUndefined();
    expect(items[1]!.attributes('disabled')).toBeUndefined();
  });

  it('refuses a typed address that appears in no suggestion', async () => {
    const search = vi.fn().mockResolvedValue([]);
    const add = vi.fn();
    const wrapper = mountComponent({ search, add });

    await typeIn(wrapper, 'buiten@voorbeeld.nl');
    await afterDebounce();
    await wrapper.find('[data-testid="siterol-formulier"]').trigger('submit');
    await flushPromises();

    expect(add).not.toHaveBeenCalled();
  });

  it('ends a failed search in an empty list, not in a message of its own', async () => {
    const search = vi
      .fn()
      .mockRejectedValue(new ApiError({ type: 'about:blank', title: 'Serverfout', status: 500 }));
    const wrapper = mountComponent({ search });

    await typeIn(wrapper, 'buiten@voorbeeld.nl');
    await afterDebounce();

    expect(suggestionItems(wrapper)).toHaveLength(0);
    expect(wrapper.find('[data-testid="siterol-suggesties"]').attributes('empty-text')).toBe(
      'Niemand gevonden',
    );
    expect(wrapper.find('nldd-notification').exists()).toBe(false);
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

  it('names whoever was picked, not the address they were picked by', async () => {
    const search = vi.fn().mockResolvedValue([suggestionOf()]);
    const add = vi
      .fn()
      .mockRejectedValue(new ApiError({ type: 'about:blank', title: 'Serverfout', status: 500 }));
    const wrapper = mountComponent({ search, add });

    await typeIn(wrapper, 'ada');
    await afterDebounce();
    await pickSuggestion(wrapper, 'ada@voorbeeld.nl');
    await wrapper.find('[data-testid="siterol-formulier"]').trigger('submit');
    await flushPromises();

    expect(wrapper.find('nldd-notification').attributes('text')).toBe(
      'Ada Vermeer een rol op deze site geven is niet gelukt',
    );
  });
});
