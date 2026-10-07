import { mount } from '@vue/test-utils';
import { describe, expect, it } from 'vitest';

import PageHeader from './PageHeader.vue';

describe('PageHeader', () => {
  it('puts the h1 and the actions in a wrap container', () => {
    const wrapper = mount(PageHeader, {
      props: { text: 'Overzicht' },
      slots: { default: '<button data-testid="action">Doe iets</button>' },
    });

    const container = wrapper.find('nldd-container.page-header');
    expect(container.attributes('layout')).toBe('wrap');
    expect(container.find('nldd-title h1').text()).toBe('Overzicht');
    expect(container.find('nldd-title').attributes('size')).toBe('1');
    expect(container.find('[data-testid="action"]').exists()).toBe(true);
  });

  it('takes another size for the title', () => {
    const wrapper = mount(PageHeader, { props: { text: 'Groep', size: 3 } });

    expect(wrapper.find('nldd-title').attributes('size')).toBe('3');
  });
});
