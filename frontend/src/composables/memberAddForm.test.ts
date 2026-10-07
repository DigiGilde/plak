import { flushPromises, mount } from '@vue/test-utils';
import { describe, expect, it, vi } from 'vitest';
import { defineComponent, h } from 'vue';

import { ApiError } from '@/api/client';
import type { MemberSuggestion, Role } from '@/api/types';
import { useMemberAddForm, type MemberAddFormOptions } from './memberAddForm';

interface Added {
  identifier: string;
  role: Role;
}

const ADA: MemberSuggestion = {
  identifier: 'ada@voorbeeld.nl',
  name: 'Ada Vermeer',
  email: 'ada@voorbeeld.nl',
  alreadyMember: false,
  groupRole: null,
};

function mountForm(overrides: Partial<MemberAddFormOptions<Added>> = {}) {
  const listed = new Set<string>();
  const provisional: string[] = [];
  const options: MemberAddFormOptions<Added> = {
    search: () => Promise.resolve([]),
    emptyText: {
      tooShort: 'group.members.add.suggestions.tooShort',
      searching: 'group.members.add.suggestions.searching',
      none: 'group.members.add.suggestions.empty',
    },
    addFailed: 'group.members.addFailed',
    add: (identifier, role) => Promise.resolve({ identifier, role }),
    onAdded: vi.fn(),
    isListed: (identifier) => listed.has(identifier),
    addProvisional: (identifier) => provisional.push(identifier),
    dropProvisional: (identifier) => provisional.splice(provisional.indexOf(identifier), 1),
    ...overrides,
  };
  let form!: ReturnType<typeof useMemberAddForm<Added>>;
  mount(
    defineComponent({
      setup() {
        form = useMemberAddForm(options);
        return () => h('div');
      },
    }),
  );
  return { form, options, listed, provisional };
}

const detail = (value?: string) => new CustomEvent('x', { detail: { value } });

describe('useMemberAddForm: the field', () => {
  it('takes what is typed as the label and takes an earlier pick back', () => {
    const { form } = mountForm();
    form.newIdentifier.value = 'ada@voorbeeld.nl';

    form.onIdentifierInput(detail('ad'));

    expect(form.newLabel.value).toBe('ad');
    expect(form.newIdentifier.value).toBe('');
  });

  it('ignores the bare native events that bubble out of the inner field', () => {
    const { form } = mountForm();
    form.newIdentifier.value = 'ada@voorbeeld.nl';
    form.newLabel.value = 'Ada Vermeer';

    form.onIdentifierInput(new CustomEvent('input'));
    form.onIdentifierChange(new CustomEvent('change'));

    expect(form.newIdentifier.value).toBe('ada@voorbeeld.nl');
    expect(form.newLabel.value).toBe('Ada Vermeer');
  });

  it('shows the name of a pick from the starting list, and the address when nobody is known', () => {
    const { form } = mountForm({ starting: () => [ADA] });

    form.onIdentifierChange(detail('ada@voorbeeld.nl'));
    expect(form.newIdentifier.value).toBe('ada@voorbeeld.nl');
    expect(form.newLabel.value).toBe('Ada Vermeer');

    form.onIdentifierChange(detail('onbekend@voorbeeld.nl'));
    expect(form.newLabel.value).toBe('onbekend@voorbeeld.nl');
  });

  it('shows the starting list while the field is empty and the search answers once typing starts', () => {
    const { form } = mountForm({ starting: () => [ADA] });
    expect(form.shownSuggestions.value).toEqual([ADA]);

    form.onIdentifierInput(detail('ve'));

    expect(form.shownSuggestions.value).toEqual([]);
  });

  it('has no starting list unless it is given one', () => {
    const { form } = mountForm();

    expect(form.shownSuggestions.value).toEqual([]);
  });

  it('puts a failed address back in the field on reopen and drops the notice', async () => {
    const { form } = mountForm({ add: () => Promise.reject(new TypeError('network down')) });
    form.onIdentifierChange(detail('ada@voorbeeld.nl'));
    await form.onAdd();

    const [notice] = form.notices.value;
    expect(notice!.retry).toBe('ada@voorbeeld.nl');

    form.reopen(notice!);

    expect(form.newIdentifier.value).toBe('ada@voorbeeld.nl');
    expect(form.newLabel.value).toBe('ada@voorbeeld.nl');
    expect(form.notices.value).toEqual([]);
  });
});

describe('useMemberAddForm: adding', () => {
  it('refuses an empty field without asking the API', async () => {
    const add = vi.fn();
    const { form } = mountForm({ add });

    await form.onAdd();

    expect(form.emptyField.value).toBe(true);
    expect(add).not.toHaveBeenCalled();
  });

  it('runs a row ahead of the answer, with the chosen role, and takes it away afterwards', async () => {
    let release!: () => void;
    const added = vi.fn();
    const { form, provisional } = mountForm({
      add: (identifier, role) =>
        new Promise((resolve) => {
          release = () => resolve({ identifier, role });
        }),
      onAdded: added,
    });
    form.newRole.value = 'editor';
    form.onIdentifierChange(detail('ada@voorbeeld.nl'));

    const pending = form.onAdd();
    expect(provisional).toEqual(['ada@voorbeeld.nl']);
    expect(form.newIdentifier.value).toBe('');
    release();
    await pending;
    await flushPromises();

    expect(provisional).toEqual([]);
    expect(added).toHaveBeenCalledWith({ identifier: 'ada@voorbeeld.nl', role: 'editor' });
  });

  it('gets ahead of nobody who is already on the list', async () => {
    const { form, listed, provisional, options } = mountForm();
    listed.add('ada@voorbeeld.nl');
    form.onIdentifierChange(detail('ada@voorbeeld.nl'));

    await form.onAdd();

    expect(provisional).toEqual([]);
    expect(options.onAdded).toHaveBeenCalledOnce();
  });

  it('takes the row away again and notifies by name when the API refuses', async () => {
    const refusal = new ApiError({
      type: 'about:blank',
      title: 'Geweigerd',
      status: 409,
      detail: 'Al lid.',
    });
    const { form, provisional, options } = mountForm({ add: () => Promise.reject(refusal) });
    form.onIdentifierChange(detail('ada@voorbeeld.nl'));
    form.onIdentifierInput(detail('Ada'));
    form.onIdentifierChange(detail('ada@voorbeeld.nl'));

    await form.onAdd();

    expect(provisional).toEqual([]);
    expect(options.onAdded).not.toHaveBeenCalled();
    expect(form.notices.value).toHaveLength(1);
    expect(form.notices.value[0]!.detail).toBe('Al lid.');
    // A refusal on content: another go would get the same answer.
    expect(form.notices.value[0]!.retry).toBe('');
  });
});

describe('useMemberAddForm: the empty menu', () => {
  it('tells the three situations apart', async () => {
    let answer!: (people: MemberSuggestion[]) => void;
    const { form } = mountForm({
      search: () =>
        new Promise((resolve) => {
          answer = resolve;
        }),
    });
    const tooShort = form.emptyText.value;

    form.onIdentifierInput(detail('ada'));
    await new Promise((resolve) => setTimeout(resolve, 400));
    const searching = form.emptyText.value;
    answer([]);
    await flushPromises();

    expect(new Set([tooShort, searching, form.emptyText.value]).size).toBe(3);
  });
});
