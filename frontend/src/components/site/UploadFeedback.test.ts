import { mount } from '@vue/test-utils';
import { describe, expect, it } from 'vitest';

import UploadFeedback from './UploadFeedback.vue';

function render(props: Partial<InstanceType<typeof UploadFeedback>['$props']> = {}) {
  return mount(UploadFeedback, {
    props: { step: '', progress: null, ready: '', error: null, ...props },
  });
}

describe('UploadFeedback', () => {
  it('shows nothing while there is nothing to tell', () => {
    const wrapper = render();

    expect(wrapper.find('[data-testid="drag-progress"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="drag-done"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="drag-error"]').exists()).toBe(false);
  });

  it('shows reading as indeterminate: how much there is, is not known yet', () => {
    const wrapper = render({ step: 'reading' });

    const bar = wrapper.find('[data-testid="drag-progress"]');
    expect(bar.attributes('text')).toBe('Map uitlezen');
    expect(bar.attributes('indeterminate')).toBeDefined();
    expect(bar.attributes('max')).toBe('1');
    expect(bar.attributes('value')).toBe('0');
  });

  it('counts the files while packing', () => {
    const wrapper = render({ step: 'packing', progress: { done: 2, total: 5 } });

    const bar = wrapper.find('[data-testid="drag-progress"]');
    expect(bar.attributes('text')).toBe('Map inpakken');
    expect(bar.attributes('indeterminate')).toBeUndefined();
    expect(bar.attributes('max')).toBe('5');
    expect(bar.attributes('value')).toBe('2');
  });

  it('says what is ready, once nothing is running any more', async () => {
    const wrapper = render({ step: 'packing', ready: 'Een eerdere map.' });
    expect(wrapper.find('[data-testid="drag-done"]').exists()).toBe(false);

    await wrapper.setProps({ step: '' });

    expect(wrapper.find('[data-testid="drag-done"]').attributes('supporting-text')).toBe(
      'Een eerdere map.',
    );
  });

  it('says what is wrong as a warning', () => {
    const wrapper = render({ error: 'Nee.' });

    const banner = wrapper.find('[data-testid="drag-error"]');
    expect(banner.attributes('supporting-text')).toBe('Nee.');
    expect(banner.attributes('variant')).toBe('warning');
  });
});
