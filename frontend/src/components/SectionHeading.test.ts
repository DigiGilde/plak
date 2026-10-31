import { mount } from '@vue/test-utils';
import { describe, expect, it } from 'vitest';

import SectionHeading from './SectionHeading.vue';

describe('SectionHeading', () => {
  it('is an h2 at size 4 by default, carrying the id', () => {
    const wrapper = mount(SectionHeading, { props: { text: 'Toegang', id: 'heading-access' } });

    expect(wrapper.find('nldd-title').attributes('size')).toBe('4');
    const heading = wrapper.find('h2');
    expect(heading.text()).toBe('Toegang');
    expect(heading.attributes('id')).toBe('heading-access');
    expect(wrapper.find('[slot]').exists()).toBe(false);
  });

  it('puts the intro in the supporting-text slot of the title', () => {
    const wrapper = mount(SectionHeading, { props: { text: 'Leden', intro: 'Wie erbij mag.' } });

    const intro = wrapper.find('span[slot="supporting-text"]');
    expect(intro.text()).toBe('Wie erbij mag.');
    expect(wrapper.find('span[slot="subtitle"]').exists()).toBe(false);
  });

  it('takes an intro that is more than a string through its slot', () => {
    const wrapper = mount(SectionHeading, {
      props: { text: 'Sessies', level: 1, size: 1 },
      slots: { intro: 'Via <code>plak login</code>' },
    });

    expect(wrapper.find('h1').exists()).toBe(true);
    expect(wrapper.find('nldd-title').attributes('size')).toBe('1');
    expect(wrapper.find('span[slot="supporting-text"] code').text()).toBe('plak login');
  });
});
