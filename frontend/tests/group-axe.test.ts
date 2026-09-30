/**
 * Axe check for the group page. Separate from Group.test.ts because
 * this registers the real @nldd/design-system custom elements (needed for
 * correct ARIA and roles); that changes how Vue applies attribute versus
 * property bindings, and would break the attribute-based assertions in
 * Group.test.ts if it lived in the same file.
 */
import '@nldd/design-system';

import { flushPromises, mount } from '@vue/test-utils';
import axe from 'axe-core';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { defineComponent, h } from 'vue';
import { createMemoryHistory, createRouter, RouterView } from 'vue-router';

import { makeMockBackend, type MockBackend } from '../src/api/mock';
import TabSettings from '../src/components/group/TabSettings.vue';
import TabMembers from '../src/components/group/TabMembers.vue';
import TabSites from '../src/components/group/TabSites.vue';
import Group from '../src/pages/Group.vue';

/**
 * jsdom 25 reflects the ARIA properties of ElementInternals onto a
 * `_internalContentAttributeMap` it never creates itself, so writing throws a
 * TypeError. nldd-file-field sets `internals.ariaLabel` in its willUpdate as
 * soon as it is attached to the document, and that error comes back as an
 * unhandled rejection that fails the run. Plain properties instead of the
 * reflectors, just as in tests/site-axe.test.ts.
 */
{
  const proto = (globalThis as { ElementInternals?: { prototype: object } }).ElementInternals
    ?.prototype;
  for (const name of proto ? Object.getOwnPropertyNames(proto) : []) {
    if (name !== 'role' && !name.startsWith('aria')) continue;
    Object.defineProperty(proto, name, { value: null, writable: true, configurable: true });
  }
}

let backend: MockBackend;

const AXE_OPTIONS: axe.RunOptions = {
  rules: {
    // jsdom has no renderer or canvas; contrast is covered by the design
    // system's own tokens, not per page.
    'color-contrast': { enabled: false },
    // A standalone page without the surrounding App shell (skip link, main landmark).
    region: { enabled: false },
    'landmark-one-main': { enabled: false },
  },
};

/**
 * The suggestions popup of an nldd-combo-box: the combo box gives its slotted
 * menu role="listbox" without a name. In a browser that menu is a closed
 * popover (display: none until :popover-open) and axe never reaches it; jsdom
 * applies no shadow styles, so it does. Design-system markup either way, and
 * nothing on this page can name a div inside its shadow root.
 */
const OUT_OF_SCOPE: axe.ContextObject['exclude'] = [
  { fromShadowDom: ['nldd-combo-box nldd-menu', '.menu__list'] },
];

beforeEach(() => {
  backend = makeMockBackend();
  vi.stubGlobal('fetch', backend.fetch);
});

afterEach(() => {
  vi.unstubAllGlobals();
  document.body.innerHTML = '';
});

const Host = defineComponent({ render: () => h(RouterView) });

// Through a RouterView, because the tabs are child routes: without that parent
// the router-view in Group.vue would resolve the page itself all over again.
async function mountGroup(path: string) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      {
        path: '/:group',
        component: Group,
        children: [
          { path: '', name: 'group-sites', component: TabSites },
          { path: '-/members', name: 'group-members', component: TabMembers },
          { path: '-/settings', name: 'group-settings', component: TabSettings },
        ],
      },
    ],
  });
  await router.push(path);
  await router.isReady();
  return mount(Host, { global: { plugins: [router] }, attachTo: document.body });
}

describe('axe: groepspagina', () => {
  it.each([
    ['Sites', '/team-aurora'],
    ['Groepsleden', '/team-aurora/-/members'],
    ['Instellingen', '/team-aurora/-/settings'],
  ])('tabblad %s van een gevulde groep is zonder violations', async (_name, path) => {
    const wrapper = await mountGroup(path);
    await flushPromises();

    const result = await axe.run(
      { include: [wrapper.element], exclude: OUT_OF_SCOPE },
      AXE_OPTIONS,
    );
    expect(result.violations).toEqual([]);
    wrapper.unmount();
  });

  it('lege groep is zonder violations', async () => {
    backend.data.groups.push({ slug: 'leeg', name: 'Lege groep', defaultAccess: { base: 'public', keys: false, invitees: false } });
    const wrapper = await mountGroup('/leeg');
    await flushPromises();

    const result = await axe.run(wrapper.element, AXE_OPTIONS);
    expect(result.violations).toEqual([]);
    wrapper.unmount();
  });

  it('foutstaat (onbekende groep) is zonder violations', async () => {
    const wrapper = await mountGroup('/onbekend');
    await flushPromises();

    const result = await axe.run(wrapper.element, AXE_OPTIONS);
    expect(result.violations).toEqual([]);
    wrapper.unmount();
  });
});
