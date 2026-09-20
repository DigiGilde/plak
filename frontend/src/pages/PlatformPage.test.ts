import '@nldd/design-system';

import { mount } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { createMemoryHistory, createRouter, type Router } from 'vue-router';

import { expectNoAxeViolations } from '../../tests/a11y';
import { _resetBreadcrumbs, breadcrumbsFor } from '../composables/breadcrumbs';
import About from './About.vue';
import Privacy from './Privacy.vue';
import Accessibility from './Accessibility.vue';

let wrapper: ReturnType<typeof mount> | null = null;
let router: Router;

beforeEach(async () => {
  _resetBreadcrumbs();
  router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/-/:pagina', component: { template: '<div />' } }],
  });
  await router.push('/-/privacy');
  await router.isReady();
});

afterEach(() => {
  wrapper?.unmount();
  wrapper = null;
});

describe.each([
  { name: 'Privacy', component: Privacy, title: 'Privacy' },
  { name: 'Toegankelijkheid', component: Accessibility, title: 'Toegankelijkheid' },
  { name: 'Over', component: About, title: 'Over Plak' },
])('$naam', ({ component, title }) => {
  it('shows the title and content', () => {
    wrapper = mount(component, { attachTo: document.body, global: { plugins: [router] } });
    expect(wrapper.find('h1').text()).toBe(title);
    expect(wrapper.text().length).toBeGreaterThan(50);
  });

  it('places the bare content in nldd-rich-text', () => {
    wrapper = mount(component, { attachTo: document.body, global: { plugins: [router] } });
    const richText = wrapper.find('nldd-rich-text');
    expect(richText.exists()).toBe(true);
    expect(richText.find('h2').exists()).toBe(true);
    expect(richText.find('h1').exists()).toBe(false);
  });

  it('has no axe violations', async () => {
    wrapper = mount(component, { attachTo: document.body, global: { plugins: [router] } });
    await new Promise((resolve) => setTimeout(resolve, 20));

    await expectNoAxeViolations(wrapper.element);
  });
});

describe('PlatformPage: breadcrumbs', () => {
  it('supplies the breadcrumbs to the app shell instead of showing them at the top', async () => {
    wrapper = mount(Privacy, { attachTo: document.body, global: { plugins: [router] } });

    expect(wrapper.find('nldd-breadcrumbs').exists()).toBe(false);
    expect(breadcrumbsFor('/-/privacy')).toEqual([
      { text: 'Overzicht', href: '/' },
      { text: 'Privacy' },
    ]);
  });
});
