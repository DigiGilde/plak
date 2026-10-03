import { mount } from '@vue/test-utils';
import { describe, expect, it } from 'vitest';

import ConfirmModal from './ConfirmModal.vue';
import { fireDetailEvent } from './site/testHelpers';

const props = {
  open: false,
  title: 'Site verwijderen',
  text: 'Dit kan niet ongedaan worden gemaakt.',
  confirmLabel: 'Verwijderen',
};

describe('ConfirmModal (teleport)', () => {
  it('teleports the dialog to document.body and cleans it up on unmount', () => {
    const wrapper = mount(ConfirmModal, { props });

    const dialog = document.body.querySelector('nldd-modal-dialog');
    expect(dialog).not.toBeNull();
    expect(dialog?.parentElement).toBe(document.body);

    wrapper.unmount();
    expect(document.body.querySelector('nldd-modal-dialog')).toBeNull();
  });

  it('confirms only when the dialog is open', async () => {
    const wrapper = mount(ConfirmModal, {
      props,
      global: { stubs: { teleport: true } },
    });

    await wrapper.find('[data-testid="confirm-continue"]').trigger('click');
    expect(wrapper.emitted('confirm')).toBeFalsy();

    await wrapper.setProps({ open: true });
    await wrapper.find('[data-testid="confirm-continue"]').trigger('click');
    expect(wrapper.emitted('confirm')).toHaveLength(1);
  });

  it('emits close when the dialog closes itself, such as on Escape', async () => {
    const wrapper = mount(ConfirmModal, {
      props: { ...props, open: true },
      global: { stubs: { teleport: true } },
    });

    await wrapper.find('nldd-modal-dialog').trigger('close');

    expect(wrapper.emitted('close')).toHaveLength(1);
  });

  it('puts the safe way out on top as the primary button and disables nothing', async () => {
    const wrapper = mount(ConfirmModal, {
      props: { ...props, open: true, keepLabel: 'Behoud site', busy: true },
      global: { stubs: { teleport: true } },
    });

    const actions = wrapper.findAll('nldd-button');
    expect(actions[0]!.attributes('variant')).toBe('primary');
    expect(actions[0]!.attributes('text')).toBe('Behoud site');
    expect(actions[1]!.attributes('variant')).toBe('destructive');
    // Action in flight: a loading state on the button, not a disabled one.
    expect(actions[1]!.attributes('loading')).toBeDefined();
    expect(actions[1]!.attributes('disabled')).toBeUndefined();
  });
});

describe('ConfirmModal (typed confirmation)', () => {
  function makeWrapper(open = true) {
    return mount(ConfirmModal, {
      props: { ...props, open, confirmPhrase: 'team-aurora/website' },
      global: { stubs: { teleport: true } },
    });
  }

  function type(wrapper: ReturnType<typeof makeWrapper>, value: string): void {
    fireDetailEvent(wrapper.find('[data-testid="confirm-phrase"]').element, 'input', { value });
  }

  const field = (wrapper: ReturnType<typeof makeWrapper>) =>
    wrapper.find('[data-testid="confirm-phrase"]');

  it('asks for no text without a phrase', () => {
    const wrapper = mount(ConfirmModal, {
      props: { ...props, open: true },
      global: { stubs: { teleport: true } },
    });
    expect(wrapper.find('[data-testid="confirm-phrase"]').exists()).toBe(false);
  });

  it('stacks slotted content and the field in one container, for an even gap', () => {
    const wrapper = mount(ConfirmModal, {
      props: { ...props, open: true, confirmPhrase: 'team-aurora' },
      slots: { default: '<p data-testid="extra">Sites</p>' },
      global: { stubs: { teleport: true } },
    });

    const stack = wrapper.find('[data-testid="confirm-content"]');
    expect(stack.attributes('gap')).toBe('16');
    expect(stack.find('[data-testid="extra"]').exists()).toBe(true);
    expect(stack.find('[data-testid="confirm-phrase"]').exists()).toBe(true);
  });

  it('adds no container without a phrase, so a bare message keeps its layout', () => {
    const wrapper = mount(ConfirmModal, {
      props: { ...props, open: true },
      slots: { default: '<p data-testid="extra">Meer</p>' },
      global: { stubs: { teleport: true } },
    });

    expect(wrapper.find('[data-testid="confirm-content"]').exists()).toBe(false);
    expect(wrapper.find('nldd-modal-dialog > [data-testid="extra"]').exists()).toBe(true);
  });

  it('names the phrase to type in the label', () => {
    const wrapper = makeWrapper();
    expect(wrapper.find('nldd-form-field').attributes('label')).toBe(
      'Typ team-aurora/website om te bevestigen',
    );
  });

  it('refuses an empty field and marks it, without confirming', async () => {
    const wrapper = makeWrapper();

    await wrapper.find('[data-testid="confirm-continue"]').trigger('click');

    expect(wrapper.emitted('confirm')).toBeFalsy();
    expect(field(wrapper).attributes('invalid')).toBeDefined();
    const unmet = field(wrapper).attributes('unmet');
    expect(unmet).toBeTruthy();
    expect(wrapper.find(`nldd-validation-item[id="${unmet}"]`).text()).toBe('Precies team-aurora/website');
  });

  it('refuses only part of the phrase, such as the site without its group', async () => {
    const wrapper = makeWrapper();

    type(wrapper, 'website');
    await wrapper.find('[data-testid="confirm-continue"]').trigger('click');

    expect(wrapper.emitted('confirm')).toBeFalsy();
    expect(field(wrapper).attributes('invalid')).toBeDefined();
  });

  it('refuses the phrase in another case', async () => {
    const wrapper = makeWrapper();

    type(wrapper, 'TEAM-AURORA/website');
    await wrapper.find('[data-testid="confirm-continue"]').trigger('click');

    expect(wrapper.emitted('confirm')).toBeFalsy();
  });

  it('confirms once the phrase is typed, surrounding spaces aside', async () => {
    const wrapper = makeWrapper();

    type(wrapper, '  team-aurora/website ');
    await wrapper.find('[data-testid="confirm-continue"]').trigger('click');

    expect(wrapper.emitted('confirm')).toHaveLength(1);
    expect(field(wrapper).attributes('invalid')).toBeUndefined();
  });

  it('keeps the mark while the value is still wrong, and drops it once it is right', async () => {
    const wrapper = makeWrapper();
    await wrapper.find('[data-testid="confirm-continue"]').trigger('click');

    type(wrapper, 'team-aurora/web');
    await wrapper.vm.$nextTick();
    expect(field(wrapper).attributes('invalid')).toBeDefined();

    type(wrapper, 'team-aurora/website');
    await wrapper.vm.$nextTick();
    expect(field(wrapper).attributes('invalid')).toBeUndefined();
    expect(field(wrapper).attributes('unmet')).toBeUndefined();
    expect(wrapper.emitted('confirm')).toBeFalsy();
  });

  it('does not judge while typing before a first attempt', async () => {
    const wrapper = makeWrapper();

    type(wrapper, 'nl');
    await wrapper.vm.$nextTick();

    expect(field(wrapper).attributes('invalid')).toBeUndefined();
  });

  it('confirms with Enter in the field, under the same condition', async () => {
    const wrapper = makeWrapper();

    await field(wrapper).trigger('keydown', { key: 'Enter' });
    expect(wrapper.emitted('confirm')).toBeFalsy();

    type(wrapper, 'team-aurora/website');
    await field(wrapper).trigger('keydown', { key: 'Enter' });
    expect(wrapper.emitted('confirm')).toHaveLength(1);
  });

  it('reads the value off the input when the event carries no detail', async () => {
    const wrapper = makeWrapper();
    const input = field(wrapper).element as HTMLElement & { value: string };
    input.value = 'team-aurora/website';
    input.dispatchEvent(new Event('input'));

    await wrapper.find('[data-testid="confirm-continue"]').trigger('click');

    expect(wrapper.emitted('confirm')).toHaveLength(1);
  });

  it('starts empty and unjudged every time the dialog opens again', async () => {
    const wrapper = makeWrapper();
    type(wrapper, 'team-aurora/webs');
    await wrapper.find('[data-testid="confirm-continue"]').trigger('click');

    await wrapper.setProps({ open: false });
    await wrapper.setProps({ open: true });

    expect(field(wrapper).attributes('value')).toBe('');
    expect(field(wrapper).attributes('invalid')).toBeUndefined();
    await wrapper.find('[data-testid="confirm-continue"]').trigger('click');
    expect(wrapper.emitted('confirm')).toBeFalsy();
  });
});
