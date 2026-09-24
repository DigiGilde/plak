import { flushPromises, mount } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { nextTick } from 'vue';

import { ApiError } from '@/api/client';
import type { GroupMember, MemberSuggestion, Role } from '@/api/types';
import { SEARCH_DEBOUNCE_MS } from '@/composables/memberSearch';

import GroupMembersManager from './GroupMembersManager.vue';

const member: GroupMember = {
  groupSlug: 'nldd',
  memberId: 'lid-7',
  identifier: 'lid@voorbeeld.nl',
  name: 'lid@voorbeeld.nl',
  email: 'lid@voorbeeld.nl',
  role: 'reader',
};

function memberWith(overrides: Partial<GroupMember>): GroupMember {
  return { ...member, ...overrides };
}

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
  members?: GroupMember[];
  add?: (identifier: string, role: Role) => Promise<GroupMember>;
  remove?: (memberId: string) => Promise<void>;
  setRole?: (identifier: string, role: Role) => Promise<GroupMember>;
  search?: (query: string) => Promise<MemberSuggestion[]>;
}) {
  return mount(GroupMembersManager, {
    props: {
      members: props.members ?? [],
      add: props.add ?? vi.fn(),
      remove: props.remove ?? vi.fn(),
      setRole: props.setRole ?? vi.fn(),
      search: props.search ?? vi.fn().mockResolvedValue([]),
    },
    // Nothing teleports to body any more, but a sheet that comes back by
    // accident stays visible in the wrapper this way.
    global: { stubs: { teleport: true } },
  });
}

type Wrapper = ReturnType<typeof mountComponent>;

function pageRows(wrapper: Wrapper) {
  return wrapper
    .find('[data-testid="leden-lijst"]')
    .findAll('nldd-table-row:not([slot="header"])');
}

/** The text per cell of one row, in column order. */
function cellTexts(row: ReturnType<typeof pageRows>[number]) {
  return row.findAll('nldd-text-cell').map((cell) => cell.attributes('text'));
}

function rowOf(wrapper: Wrapper, identifier: string) {
  const row = pageRows(wrapper).find((candidate) => candidate.html().includes(identifier));
  expect(row, `row for ${identifier} missing`).toBeDefined();
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

/** The labels in the menu of one row, in the order it offers them. */
function actionTexts(wrapper: Wrapper, identifier: string) {
  return rowOf(wrapper, identifier)
    .findAll('nldd-menu-item')
    .map((item) => item.attributes('text'));
}

/** A menu item fires `select`, not `click`. */
async function runAction(wrapper: Wrapper, identifier: string, testid: string): Promise<void> {
  const item = actionOf(wrapper, identifier, testid);
  expect(item.exists(), `action "${testid}" missing for ${identifier}`).toBe(true);
  item.element.dispatchEvent(new CustomEvent('select'));
  await flushPromises();
}

async function pickRole(wrapper: Wrapper, role: Role): Promise<void> {
  await wrapper.find('[data-testid="lid-rol-nieuw"]').setValue(role);
}

function comboBox(wrapper: Wrapper) {
  return wrapper.find('nldd-combo-box[name="identifier"]');
}

/** The help text under the role picker, scoped so the identifier field's own
 *  help text (also an nldd-form-field-help-text) is not picked up instead. */
function roleHelpText(wrapper: Wrapper): string {
  return wrapper
    .find('nldd-form-field[label="Rol"] > nldd-form-field-help-text')
    .text();
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
  return wrapper.find('[data-testid="lid-suggesties"]').findAll('nldd-menu-item');
}

/** Waits out the debounce and lets the answer land. */
async function afterDebounce(): Promise<void> {
  vi.advanceTimersByTime(SEARCH_DEBOUNCE_MS);
  await flushPromises();
}

async function fillInAndSubmit(wrapper: Wrapper, value: string): Promise<void> {
  await typeIn(wrapper, value);
  await wrapper.find('[data-testid="lid-formulier"]').trigger('submit');
}

describe('GroupMembersManager (empty)', () => {
  it('puts the empty state in the empty slot of the table', () => {
    const wrapper = mountComponent({});

    const empty = wrapper.find('nldd-table > nldd-inline-dialog[slot="empty"]');
    expect(empty.attributes('text')).toBe('Nog geen groepsleden');
    expect(pageRows(wrapper)).toHaveLength(0);
    // The list is empty, the form below it is there: that is the route to the
    // first member.
    expect(wrapper.find('[data-testid="lid-formulier"]').exists()).toBe(true);
  });
});

describe('GroupMembersManager (filled)', () => {
  it('puts member and role in their own columns, with a header row above', () => {
    const wrapper = mountComponent({ members: [member] });

    const headings = wrapper.find('nldd-table-row[slot="header"]').findAll('nldd-text-cell');
    expect(headings.map((cell) => cell.attributes('text'))).toEqual(['Lid', 'Rol', undefined]);
    // The action column carries a header too: an empty columnheader lands in
    // the accessibility tree without a name.
    expect(headings[2]!.find('.alleen-schermlezer').text()).toBe('Acties');

    const rows = pageRows(wrapper);
    expect(rows).toHaveLength(1);
    expect(cellTexts(rows[0]!)).toEqual(['lid@voorbeeld.nl', 'Lezer']);
  });

  it('puts the email address under the name, not in a column next to it', () => {
    const wrapper = mountComponent({
      members: [memberWith({ name: 'Ada Vermeer', email: 'ada@voorbeeld.nl' })],
    });

    const nameCell = rowOf(wrapper, 'ada@voorbeeld.nl').find('nldd-text-cell');
    expect(nameCell.attributes('text')).toBe('Ada Vermeer');
    expect(nameCell.attributes('supporting-text')).toBe('ada@voorbeeld.nl');
  });

  it('names every role in Dutch', () => {
    const wrapper = mountComponent({
      members: [
        memberWith({ identifier: 'lezer@voorbeeld.nl', role: 'reader' }),
        memberWith({ identifier: 'redacteur@voorbeeld.nl', role: 'editor' }),
        memberWith({ identifier: 'beheerder@voorbeeld.nl', role: 'admin' }),
      ],
    });

    expect(pageRows(wrapper).map((row) => cellTexts(row)[1])).toEqual([
      'Lezer',
      'Redacteur',
      'Beheerder',
    ]);
  });

  it('puts the actions in the row menu, without a side panel', () => {
    const wrapper = mountComponent({ members: [member] });

    expect(wrapper.find('nldd-sheet').exists()).toBe(false);

    const menuButton = rowOf(wrapper, 'lid@voorbeeld.nl').find('nldd-icon-button');
    expect(menuButton.attributes('accessible-label')).toBe('Acties voor lid@voorbeeld.nl');

    const action = actionOf(wrapper, 'lid@voorbeeld.nl', 'lid-verwijderen-lid@voorbeeld.nl');
    expect(action.element.tagName.toLowerCase()).toBe('nldd-menu-item');
    expect(action.attributes('text')).toBe('Uit de groep halen');
    expect(action.attributes('destructive')).toBeDefined();
    expect(action.attributes('disabled')).toBeUndefined();
  });

  it('offers in the menu only the roles the member does not have yet', () => {
    const wrapper = mountComponent({
      members: [
        memberWith({ identifier: 'lezer@voorbeeld.nl', role: 'reader' }),
        memberWith({ identifier: 'beheerder@voorbeeld.nl', role: 'admin' }),
      ],
    });

    expect(actionTexts(wrapper, 'lezer@voorbeeld.nl')).toEqual([
      'Maak redacteur',
      'Maak beheerder',
      'Uit de groep halen',
    ]);
    expect(
      actionOf(wrapper, 'lezer@voorbeeld.nl', 'lid-rol-lezer@voorbeeld.nl-reader').exists(),
    ).toBe(false);

    expect(actionTexts(wrapper, 'beheerder@voorbeeld.nl')).toEqual([
      'Maak lezer',
      'Maak redacteur',
      'Uit de groep halen',
    ]);
  });

  it('removes optimistically: the row is gone right away, the API confirms afterward', async () => {
    let confirm!: () => void;
    const remove = vi.fn().mockImplementation(
      () =>
        new Promise<void>((resolve) => {
          confirm = resolve;
        }),
    );
    const wrapper = mountComponent({ members: [member], remove });

    await runAction(wrapper, 'lid@voorbeeld.nl', 'lid-verwijderen-lid@voorbeeld.nl');

    expect(pageRows(wrapper)).toHaveLength(0);
    expect(remove).toHaveBeenCalledWith('lid-7');
    expect(wrapper.emitted('removed')).toBeUndefined();

    confirm();
    await flushPromises();

    expect(wrapper.emitted('removed')?.[0]).toEqual(['lid@voorbeeld.nl']);
  });
});

describe('GroupMembersManager (changing role)', () => {
  it('changes the role from the row menu and reports the confirmed member', async () => {
    const promoted = memberWith({ role: 'editor' });
    const setRole = vi.fn().mockResolvedValue(promoted);
    const wrapper = mountComponent({ members: [member], setRole });

    await runAction(wrapper, 'lid@voorbeeld.nl', 'lid-rol-lid@voorbeeld.nl-editor');

    expect(setRole).toHaveBeenCalledWith('lid-7', 'editor');
    expect(wrapper.emitted('roleChanged')?.[0]).toEqual([promoted]);
  });

  it('keeps the row menu short: a label with an icon, no explanation per role', () => {
    const wrapper = mountComponent({ members: [member] });
    const item = actionOf(wrapper, 'lid@voorbeeld.nl', 'lid-rol-lid@voorbeeld.nl-admin');

    expect(item.attributes('text')).toBe('Maak beheerder');
    expect(item.attributes('icon')).toBe('key');
    // What a role means sits under the choice in the form; three sentences
    // in a menu bury the very thing it was opened for.
    expect(item.attributes('details')).toBeUndefined();
  });

  it('leaves the row on the old role when changing fails, with a notification', async () => {
    const setRole = vi.fn().mockRejectedValue(
      new ApiError({
        type: 'about:blank',
        title: 'Laatste beheerder',
        status: 409,
        detail: 'De groep moet minstens één beheerder houden.',
      }),
    );
    const wrapper = mountComponent({
      members: [memberWith({ name: 'Ada Vermeer', role: 'admin' })],
      setRole,
    });

    await runAction(wrapper, 'lid@voorbeeld.nl', 'lid-rol-lid@voorbeeld.nl-reader');

    expect(cellTexts(pageRows(wrapper)[0]!)).toEqual(['Ada Vermeer', 'Beheerder']);
    const notice = wrapper.find('nldd-notification');
    expect(notice.attributes('variant')).toBe('critical');
    expect(notice.attributes('text')).toBe('De rol van Ada Vermeer wijzigen is niet gelukt');
    expect(notice.attributes('supporting-text')).toBe(
      'De groep moet minstens één beheerder houden.',
    );
    expect(wrapper.emitted('roleChanged')).toBeUndefined();
  });
});

describe('GroupMembersManager (adding)', () => {
  it('puts the add field under the list, in a labeled form field', () => {
    const wrapper = mountComponent({});

    expect(wrapper.find('nldd-sheet').exists()).toBe(false);
    const field = wrapper.find('nldd-form-field[label="Naam of e-mailadres"] > nldd-combo-box');
    expect(field.exists()).toBe(true);
    expect(field.attributes('required')).toBeDefined();
    // With allow-custom: the list holds only whoever has logged in already,
    // and an address that is not in it still has to be submittable.
    expect(field.attributes('allow-custom')).toBeDefined();
    const requirement = wrapper.find(
      'nldd-form-field > nldd-validation-list > nldd-validation-item#lid-toevoegen-vereist',
    );
    expect(requirement.attributes('required')).toBeDefined();
    expect(requirement.text()).toBe('Een naam uit de lijst of een e-mailadres');
  });

  it('puts the address field before the role: first who, then what they may do', () => {
    const wrapper = mountComponent({});

    const fields = wrapper.findAll('nldd-form-field');
    expect(fields.map((field) => field.attributes('label'))).toEqual([
      'Naam of e-mailadres',
      'Rol',
    ]);
  });

  it('offers reader as the default role, with the role explanation at the role field', () => {
    const wrapper = mountComponent({});

    const select = wrapper.find('[data-testid="lid-rol-nieuw"]');
    expect(select.findAll('option').map((option) => option.text().trim())).toEqual([
      'Lezer',
      'Redacteur',
      'Beheerder',
    ]);
    expect((select.element as HTMLSelectElement).value).toBe('reader');
    expect(roleHelpText(wrapper)).toBe('Kijkt mee, verandert niets.');
  });

  it('adds as reader without a choice: anything more is a deliberate step', async () => {
    const add = vi.fn().mockResolvedValue(member);
    const wrapper = mountComponent({ add });

    await fillInAndSubmit(wrapper, 'lid@voorbeeld.nl');
    await flushPromises();

    expect(add).toHaveBeenCalledWith('lid@voorbeeld.nl', 'reader');
  });

  it('passes the chosen role on to the callback', async () => {
    const add = vi.fn().mockResolvedValue(memberWith({ role: 'admin' }));
    const wrapper = mountComponent({ add });

    await pickRole(wrapper, 'admin');
    expect(roleHelpText(wrapper)).toBe('Bepaalt wie erbij mag en wat de groep doet.');

    await fillInAndSubmit(wrapper, 'lid@voorbeeld.nl');
    await flushPromises();

    expect(add).toHaveBeenCalledWith('lid@voorbeeld.nl', 'admin');
  });

  it('keeps the buttons usable: no disabled on the submit button', () => {
    const wrapper = mountComponent({});

    expect(wrapper.find('nldd-button[type="submit"]').attributes('disabled')).toBeUndefined();
  });

  it('puts no cancel button next to the primary button: there is nothing to cancel', () => {
    const wrapper = mountComponent({});

    const buttons = wrapper.find('nldd-form-actions').findAll('nldd-button');
    expect(buttons).toHaveLength(1);
    expect(buttons[0]!.attributes('variant')).toBe('primary');
    expect(wrapper.html()).not.toContain('Annuleren');
  });

  it('rejects an empty field via the form, without calling the callback', async () => {
    const add = vi.fn();
    const wrapper = mountComponent({ add });

    await wrapper.find('[data-testid="lid-formulier"]').trigger('submit');
    await flushPromises();

    expect(add).not.toHaveBeenCalled();
    expect(comboBox(wrapper).attributes('invalid')).toBeDefined();
  });

  it('adds optimistically: the row is there with the chosen role before the API answers', async () => {
    let confirm!: (member: GroupMember) => void;
    const add = vi.fn().mockImplementation(
      () =>
        new Promise<GroupMember>((resolve) => {
          confirm = resolve;
        }),
    );
    const wrapper = mountComponent({ add });

    await pickRole(wrapper, 'editor');
    await fillInAndSubmit(wrapper, 'lid@voorbeeld.nl');

    expect(add).toHaveBeenCalledWith('lid@voorbeeld.nl', 'editor');
    const rows = pageRows(wrapper);
    expect(rows).toHaveLength(1);
    expect(cellTexts(rows[0]!)).toEqual(['lid@voorbeeld.nl', 'Redacteur']);

    confirm(member);
    await flushPromises();

    expect(wrapper.emitted('added')?.[0]).toEqual([member]);
  });

  it('does not get ahead of an address already in the list', async () => {
    let confirm!: (member: GroupMember) => void;
    const add = vi.fn().mockImplementation(
      () =>
        new Promise<GroupMember>((resolve) => {
          confirm = resolve;
        }),
    );
    const wrapper = mountComponent({ members: [member], add });

    await fillInAndSubmit(wrapper, 'lid@voorbeeld.nl');

    expect(add).toHaveBeenCalledWith('lid@voorbeeld.nl', 'reader');
    expect(pageRows(wrapper)).toHaveLength(1);

    confirm(member);
    await flushPromises();

    expect(pageRows(wrapper)).toHaveLength(1);
  });
});

describe('GroupMembersManager (name without a choice)', () => {
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
    expect(comboBox(wrapper).attributes('unmet')).toBe('lid-toevoegen-geen-email');
  });

  it('clears the notice as soon as typing resumes', async () => {
    const wrapper = mountComponent({});

    await fillInAndSubmit(wrapper, 'Cato Jansen');
    expect(comboBox(wrapper).attributes('invalid')).toBeDefined();

    await typeIn(wrapper, 'Cato Jansen c');
    expect(comboBox(wrapper).attributes('invalid')).toBeUndefined();
  });

  it('still submits a chosen suggestion', async () => {
    const search = vi.fn().mockResolvedValue([suggestionOf()]);
    const add = vi.fn().mockResolvedValue(member);
    const wrapper = mountComponent({ search, add });

    await typeIn(wrapper, 'ada');
    await pickSuggestion(wrapper, 'ada@voorbeeld.nl');
    await wrapper.find('[data-testid="lid-formulier"]').trigger('submit');
    await flushPromises();

    expect(add).toHaveBeenCalledWith('ada@voorbeeld.nl', 'reader');
  });

  it('submits a typed email address that appears in no suggestion', async () => {
    const add = vi.fn().mockResolvedValue(member);
    const wrapper = mountComponent({ add });

    await fillInAndSubmit(wrapper, 'nieuw@voorbeeld.nl');
    await flushPromises();

    expect(add).toHaveBeenCalledWith('nieuw@voorbeeld.nl', 'reader');
  });
});

describe('GroupMembersManager (error)', () => {
  it('rolls back a failed add and reports it with a notification', async () => {
    const add = vi.fn().mockRejectedValue(
      new ApiError({
        type: 'about:blank',
        title: 'Ongeldige identifier',
        status: 422,
        detail: 'Verwacht een e-mailadres.',
      }),
    );
    const wrapper = mountComponent({ add });

    // A typed address, not a name: names are blocked client-side before the
    // request ever fires (see "GroupMembersManager (name without a choice)").
    await fillInAndSubmit(wrapper, 'onbekend@voorbeeld.nl');
    await flushPromises();

    expect(pageRows(wrapper)).toHaveLength(0);
    const notice = wrapper.find('nldd-notification');
    expect(notice.attributes('variant')).toBe('critical');
    expect(notice.attributes('text')).toBe('Groepslid onbekend@voorbeeld.nl toevoegen is niet gelukt');
    expect(notice.attributes('supporting-text')).toBe('Verwacht een e-mailadres.');
    expect(notice.find('nldd-button[slot="actions"]').attributes('text')).toBe(
      'Opnieuw proberen',
    );
    expect(wrapper.emitted('added')).toBeUndefined();
  });

  it('puts the address back in the field with "Opnieuw proberen" and clears the notice', async () => {
    const add = vi
      .fn()
      .mockRejectedValue(new ApiError({ type: 'about:blank', title: 'Serverfout', status: 500 }));
    const wrapper = mountComponent({ add });

    await fillInAndSubmit(wrapper, 'lid@voorbeeld.nl');
    await flushPromises();
    await wrapper.find('nldd-notification nldd-button[slot="actions"]').trigger('click');

    expect(comboBox(wrapper).attributes('value')).toBe('lid@voorbeeld.nl');
    expect(wrapper.find('nldd-notification').exists()).toBe(false);
  });

  it('puts a failed removal back in the list, with a notification', async () => {
    const remove = vi
      .fn()
      .mockRejectedValue(new ApiError({ type: 'about:blank', title: 'Serverfout', status: 500 }));
    const wrapper = mountComponent({ members: [member], remove });

    await runAction(wrapper, 'lid@voorbeeld.nl', 'lid-verwijderen-lid@voorbeeld.nl');

    expect(pageRows(wrapper)).toHaveLength(1);
    expect(wrapper.find('nldd-notification').attributes('text')).toBe(
      'Groepslid lid@voorbeeld.nl verwijderen is niet gelukt',
    );
    expect(wrapper.emitted('removed')).toBeUndefined();
  });
});

describe('GroupMembersManager (suggestions)', () => {
  // Only the timers of the debounce: flushPromises rides on setImmediate, and
  // faking that too would make every await in these tests hang.
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('searches only from two characters, not on every keystroke', async () => {
    const search = vi.fn().mockResolvedValue([suggestionOf()]);
    const wrapper = mountComponent({ search });

    await typeIn(wrapper, 'a');
    await afterDebounce();

    // One letter would fetch the whole directory of the organisation, which
    // is not what this field is for, and the backend refuses it too.
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

  it('puts the address in the field on picking a suggestion, with the name in view', async () => {
    const search = vi.fn().mockResolvedValue([suggestionOf()]);
    const add = vi.fn().mockResolvedValue(member);
    const wrapper = mountComponent({ search, add });

    await typeIn(wrapper, 'ada');
    await afterDebounce();
    await pickSuggestion(wrapper, 'ada@voorbeeld.nl');

    // The name reads, the address counts: the address goes to the add route.
    expect(comboBox(wrapper).attributes('text')).toBe('Ada Vermeer');
    expect(comboBox(wrapper).attributes('value')).toBe('ada@voorbeeld.nl');

    await wrapper.find('[data-testid="lid-formulier"]').trigger('submit');
    await flushPromises();

    expect(add).toHaveBeenCalledWith('ada@voorbeeld.nl', 'reader');
    expect(comboBox(wrapper).attributes('value')).toBe('');
  });

  it('marks whoever is already a member and does not let them be chosen', async () => {
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
    // Whoever is already a member stays in the list: saying so beats leaving
    // the row out and looking broken.
    expect(items.map((item) => item.attributes('text'))).toEqual([
      'Ada Vermeer (al lid)',
      'Sanne Vermeulen',
    ]);
    expect(items[0]!.attributes('disabled')).toBeDefined();
    expect(items[1]!.attributes('disabled')).toBeUndefined();
    // The address is always shown, otherwise two namesakes cannot be told
    // apart.
    expect(items.map((item) => item.attributes('details'))).toEqual([
      'ada@voorbeeld.nl',
      'sanne@voorbeeld.nl',
    ]);
  });

  it('submits a typed address that appears in no suggestion', async () => {
    const search = vi.fn().mockResolvedValue([suggestionOf()]);
    const add = vi.fn().mockResolvedValue(member);
    const wrapper = mountComponent({ search, add });

    await typeIn(wrapper, 'nieuw@voorbeeld.nl');
    await afterDebounce();
    await wrapper.find('[data-testid="lid-formulier"]').trigger('submit');
    await flushPromises();

    expect(add).toHaveBeenCalledWith('nieuw@voorbeeld.nl', 'reader');
  });

  it('keeps the form usable when the search fails', async () => {
    const search = vi
      .fn()
      .mockRejectedValue(new ApiError({ type: 'about:blank', title: 'Serverfout', status: 500 }));
    const add = vi.fn().mockResolvedValue(member);
    const wrapper = mountComponent({ search, add });

    await typeIn(wrapper, 'ada@voorbeeld.nl');
    await afterDebounce();

    // Suggestions are help, never the route itself: a failed search ends in an
    // empty list, not in a message over the head of whoever is typing.
    expect(suggestionItems(wrapper)).toHaveLength(0);
    expect(wrapper.find('nldd-notification').exists()).toBe(false);

    await wrapper.find('[data-testid="lid-formulier"]').trigger('submit');
    await flushPromises();

    expect(add).toHaveBeenCalledWith('ada@voorbeeld.nl', 'reader');
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
