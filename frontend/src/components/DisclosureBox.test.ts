import { mount } from '@vue/test-utils';
import { describe, expect, it } from 'vitest';

import DisclosureBox from './DisclosureBox.vue';

describe('DisclosureBox', () => {
  it('is a native details with the summary and the content inside', () => {
    const wrapper = mount(DisclosureBox, {
      props: { summary: 'Veiligheid', summaryTestid: 'box-summary' },
      attrs: { 'data-testid': 'box' },
      slots: { default: '<p>Inhoud</p>' },
    });

    const box = wrapper.find('[data-testid="box"]');
    expect(box.element.tagName.toLowerCase()).toBe('details');
    expect(box.attributes('open')).toBeUndefined();
    expect(box.find('summary').text()).toBe('Veiligheid');
    expect(box.find('[data-testid="box-summary"]').exists()).toBe(true);
    expect(box.find('p').text()).toBe('Inhoud');
  });

  it('needs no test id on the summary', () => {
    const wrapper = mount(DisclosureBox, { props: { summary: 'Meer' } });

    expect(wrapper.find('summary').attributes('data-testid')).toBeUndefined();
  });
});
