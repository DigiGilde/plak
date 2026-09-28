import { mount } from '@vue/test-utils';
import { describe, expect, it } from 'vitest';

import ConfirmModal from './ConfirmModal.vue';

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

    await wrapper.find('[data-testid="bevestig-doorgaan"]').trigger('click');
    expect(wrapper.emitted('confirm')).toBeFalsy();

    await wrapper.setProps({ open: true });
    await wrapper.find('[data-testid="bevestig-doorgaan"]').trigger('click');
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
