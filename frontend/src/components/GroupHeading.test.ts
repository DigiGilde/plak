import '@nldd/design-system';

import { flushPromises, mount } from '@vue/test-utils';
import { describe, expect, it } from 'vitest';

import type { Group } from '../api/types';
import GroupHeading from './GroupHeading.vue';

const group: Group = { slug: 'nldd', name: 'NLDD', defaultAccess: { base: 'public', keys: false, invitees: false } };

describe('GroupHeading', () => {
  it('links the group name as a heading to the group page', async () => {
    const wrapper = mount(GroupHeading, { props: { group }, attachTo: document.body });
    await flushPromises();

    const nameLink = wrapper.find('h2 nldd-link');
    expect(nameLink.exists()).toBe(true);
    expect(nameLink.attributes('href')).toBe('/nldd');
    expect(nameLink.text()).toBe('NLDD');

    wrapper.unmount();
  });

  it('carries no action itself: that sits below the table for this group', async () => {
    const wrapper = mount(GroupHeading, { props: { group }, attachTo: document.body });
    await flushPromises();

    // The standalone icon button hung at the right of the heading, far past the
    // end of the table, and a plus sign never said what it did. Overview.vue
    // now places the action as a button with text below the table.
    expect(wrapper.find('nldd-icon-button').exists()).toBe(false);
    expect(wrapper.find('nldd-button').exists()).toBe(false);

    wrapper.unmount();
  });
});
