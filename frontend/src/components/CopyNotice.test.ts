import { mount } from '@vue/test-utils';
import { describe, expect, it } from 'vitest';

import CopyNotice from './CopyNotice.vue';

describe('CopyNotice', () => {
  it('is a status line that carries the text and the test id it is given', () => {
    const wrapper = mount(CopyNotice, {
      props: { text: 'Gekopieerd' },
      attrs: { 'data-testid': 'copy-notice' },
    });

    const line = wrapper.find('[data-testid="copy-notice"]');
    expect(line.attributes('role')).toBe('status');
    expect(line.text()).toBe('Gekopieerd');
  });

  it('is present but empty until something is copied', () => {
    const wrapper = mount(CopyNotice, { props: { text: '' } });

    expect(wrapper.find('[role="status"]').text()).toBe('');
  });
});
