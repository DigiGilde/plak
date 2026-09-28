import { flushPromises, mount } from '@vue/test-utils';
import type { Mock } from 'vitest';
import { describe, expect, it, vi } from 'vitest';

import { ApiError } from '@/api/client';
import type { Group } from '@/api/types';

import NewGroupSheet from './NewGroupSheet.vue';

function typeInText(wrapper: ReturnType<typeof mount>, selector: string, value: string): void {
  const el = wrapper.find(selector).element as HTMLElement;
  el.dispatchEvent(new CustomEvent('input', { detail: { value: value } }));
}

const newGroup: Group = { slug: 'team', name: 'Team', defaultAccess: { base: 'public', keys: false, invitees: false } };

// `Mock` and not `ReturnType<typeof vi.fn>`: since vitest 4 that ReturnType
// resolves the generic to its constraint `Procedure | Constructable`, which no
// longer satisfies a plain call signature, so the prop would not typecheck.
function mountComponent(create: Mock): ReturnType<typeof mount> {
  return mount(NewGroupSheet, {
    props: { open: true, create },
    global: { stubs: { teleport: true } },
  });
}

describe('NewGroupSheet', () => {
  it('derives the slug from the name as long as the slug field has not been edited manually', async () => {
    const wrapper = mountComponent(vi.fn().mockResolvedValue(newGroup));

    typeInText(wrapper, 'nldd-text-field[name="naam"]', 'Team Digitaal!');
    await flushPromises();

    expect(wrapper.find('nldd-text-field[name="slug"]').element.getAttribute('value')).toBe(
      'team-digitaal',
    );
  });

  it('calls create with name and slug and emits created on success', async () => {
    const create = vi.fn().mockResolvedValue(newGroup);
    const wrapper = mountComponent(create);

    typeInText(wrapper, 'nldd-text-field[name="naam"]', 'Team');
    typeInText(wrapper, 'nldd-text-field[name="slug"]', 'team');
    await wrapper.find('nldd-form').trigger('submit');
    await flushPromises();

    expect(create).toHaveBeenCalledWith('Team', 'team');
    expect(wrapper.emitted('created')?.[0]).toEqual([newGroup]);
    expect(wrapper.emitted('update:open')?.at(-1)).toEqual([false]);
  });

  it('turns spaces and capitals into a valid slug immediately while typing', async () => {
    const create = vi.fn().mockResolvedValue(newGroup);
    const wrapper = mountComponent(create);

    typeInText(wrapper, 'nldd-text-field[name="naam"]', 'Team');
    typeInText(wrapper, 'nldd-text-field[name="slug"]', 'Niet Geldig');
    await flushPromises();

    expect(wrapper.find('nldd-text-field[name="slug"]').attributes('value')).toBe('niet-geldig');

    await wrapper.find('nldd-form').trigger('submit');
    await flushPromises();

    expect(create).toHaveBeenCalledWith('Team', 'niet-geldig');
  });

  it('applies the same slug rules as a site for what slips through', async () => {
    const create = vi.fn();
    const wrapper = mountComponent(create);

    typeInText(wrapper, 'nldd-text-field[name="naam"]', 'Team');
    // A hyphen at the start survives the normalisation (it is what the next
    // character gets typed after) and is refused on submit, as before.
    typeInText(wrapper, 'nldd-text-field[name="slug"]', '-begin');
    await wrapper.find('nldd-form').trigger('submit');
    await flushPromises();

    expect(create).not.toHaveBeenCalled();
    const slugField = wrapper.find('nldd-text-field[name="slug"]');
    expect(slugField.attributes('invalid')).toBeDefined();
    const pattern = new RegExp(`^(?:${slugField.attributes('pattern')!})$`, 'u');
    expect(pattern.test('Niet Geldig')).toBe(false);
    expect(pattern.test('-begin')).toBe(false);
    expect(pattern.test('niet-geldig')).toBe(true);
  });

  it('rejects empty fields itself via required, without a disabled submit', async () => {
    const create = vi.fn();
    const wrapper = mountComponent(create);

    expect(wrapper.find('nldd-button[type="submit"]').attributes('disabled')).toBeUndefined();

    await wrapper.find('nldd-form').trigger('submit');
    await flushPromises();

    expect(create).not.toHaveBeenCalled();
    expect(wrapper.find('nldd-text-field[name="naam"]').attributes('invalid')).toBeDefined();
    expect(wrapper.find('nldd-text-field[name="slug"]').attributes('invalid')).toBeDefined();
    expect(wrapper.emitted('update:open')).toBeUndefined();
  });

  it('shows a 409 duplicate as a server requirement on the slug field, not as a banner', async () => {
    const create = vi.fn().mockRejectedValue(
      new ApiError({
        type: 'about:blank',
        title: 'Groep bestaat al',
        status: 409,
        detail: 'Er bestaat al een groep met slug "nldd".',
      }),
    );
    const wrapper = mountComponent(create);

    typeInText(wrapper, 'nldd-text-field[name="naam"]', 'NLDD');
    typeInText(wrapper, 'nldd-text-field[name="slug"]', 'nldd');
    await wrapper.find('nldd-form').trigger('submit');
    await flushPromises();

    expect(wrapper.find('nldd-banner').exists()).toBe(false);
    const slugField = wrapper.find('nldd-text-field[name="slug"]');
    expect(slugField.attributes('invalid')).toBeDefined();
    expect(slugField.attributes('unmet')).toBe('groep-slug-server');
    expect(wrapper.find('nldd-validation-item#groep-slug-server').text()).toContain(
      'bestaat al een groep',
    );
    expect(wrapper.emitted('update:open')).toBeUndefined();
  });

  it('teleports the sheet to document.body and cleans it up on unmount', () => {
    const wrapper = mount(NewGroupSheet, {
      props: { open: false, create: vi.fn() },
    });

    const sheet = document.body.querySelector('nldd-sheet');
    expect(sheet?.parentElement).toBe(document.body);

    wrapper.unmount();
    expect(document.body.querySelector('nldd-sheet')).toBeNull();
  });
});
