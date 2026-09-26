/**
 * The interface has to be as accessible in English as it is in Dutch, and what
 * decides whether it is, is the `lang` attribute: a screen reader picks its
 * phonetics from it, so English text under `lang="nl"` is announced with Dutch
 * sounds and comes out unintelligible (WCAG 3.1.1, 3.1.2).
 *
 * The other axe suites (group-axe, project-axe) run in Dutch, the language the
 * tests default to. This one runs the same kind of check with the language
 * switched, so a screen that only holds up in one language is caught.
 *
 * Registering the real custom elements is what makes an axe run mean anything;
 * see tests/project-axe.test.ts for why that lives apart from the
 * attribute-asserting component tests.
 */
import '@nldd/design-system';

import axe from 'axe-core';
import { flushPromises, mount } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { defineComponent, h, type Component } from 'vue';
import { createMemoryHistory, createRouter, RouterView } from 'vue-router';

import { makeMockBackend, type MockBackend } from '../src/api/mock';
import TabMembers from '../src/components/group/TabMembers.vue';
import TabSites from '../src/components/group/TabSites.vue';
import TabSettings from '../src/components/group/TabSettings.vue';
import { _resetBreadcrumbs } from '../src/composables/breadcrumbs';
import { _resetCurrentMemberCache } from '../src/composables/currentMember';
import { _setLocaleForTest, currentLocale } from '../src/i18n';
import Group from '../src/pages/Group.vue';
import Profiel from '../src/pages/Profiel.vue';

/**
 * jsdom 25 reflects the ARIA properties of ElementInternals onto a map it
 * never creates itself, so writing one throws. Plain properties instead, as in
 * tests/group-axe.test.ts.
 */
{
  const proto = (globalThis as { ElementInternals?: { prototype: object } }).ElementInternals
    ?.prototype;
  for (const name of proto ? Object.getOwnPropertyNames(proto) : []) {
    if (name !== 'role' && !name.startsWith('aria')) continue;
    Object.defineProperty(proto, name, { value: null, writable: true, configurable: true });
  }
}

const AXE_OPTIONS: axe.RunOptions = {
  rules: {
    // jsdom has no renderer or canvas; contrast is covered by the design
    // system's own tokens, not per page.
    'color-contrast': { enabled: false },
    // A standalone page without the surrounding App shell (skip link, main
    // landmark), exactly as in tests/group-axe.test.ts.
    region: { enabled: false },
    'landmark-one-main': { enabled: false },
  },
};

const OUT_OF_SCOPE: axe.ContextObject['exclude'] = [
  { fromShadowDom: ['nldd-combo-box nldd-menu', '.menu__list'] },
];

let backend: MockBackend;

beforeEach(() => {
  backend = makeMockBackend();
  vi.stubGlobal('fetch', backend.fetch);
  _resetBreadcrumbs();
  _resetCurrentMemberCache();
});

afterEach(() => {
  vi.unstubAllGlobals();
  document.body.innerHTML = '';
  _setLocaleForTest('nl');
});

const Host = defineComponent({ render: () => h(RouterView) });

async function mountPage(path: string, routes: Parameters<typeof createRouter>[0]['routes']) {
  const router = createRouter({ history: createMemoryHistory(), routes });
  await router.push(path);
  await router.isReady();
  const wrapper = mount(Host, { global: { plugins: [router] }, attachTo: document.body });
  await flushPromises();
  return wrapper;
}

const GROUP_ROUTES = [
  {
    path: '/:group',
    component: Group as Component,
    children: [
      { path: '', name: 'group-sites', component: TabSites as Component },
      { path: '-/members', name: 'group-members', component: TabMembers as Component },
      { path: '-/settings', name: 'group-settings', component: TabSettings as Component },
    ],
  },
];

const PROFILE_ROUTES = [{ path: '/-/profile', component: Profiel as Component }];

async function expectNoViolations(element: Element): Promise<void> {
  const result = await axe.run({ include: [element], exclude: OUT_OF_SCOPE }, AXE_OPTIONS);
  expect(result.violations, JSON.stringify(result.violations, null, 2)).toEqual([]);
}

describe('axe: the admin in English', () => {
  it.each([
    ['Sites', '/nldd'],
    ['Members', '/nldd/-/members'],
    ['Settings', '/nldd/-/settings'],
  ])('the %s tab has no violations in English', async (_name, path) => {
    _setLocaleForTest('en');
    const wrapper = await mountPage(path, GROUP_ROUTES);

    expect(currentLocale.value).toBe('en');
    expect(document.documentElement.lang).toBe('en');
    await expectNoViolations(wrapper.element);
    wrapper.unmount();
  });

  it.each(['nl', 'en'] as const)('the profile in %s has no violations', async (locale) => {
    backend.data.myLanguage = locale;
    const wrapper = await mountPage('/-/profile', PROFILE_ROUTES);

    expect(currentLocale.value).toBe(locale);
    expect(document.documentElement.lang).toBe(locale);
    await expectNoViolations(wrapper.element);
    wrapper.unmount();
  });

  it('keeps lang in step the moment the member switches language', async () => {
    // The whole point of the switch: a screen reader already reading the page
    // has to be told, and only `lang` tells it.
    const wrapper = await mountPage('/-/profile', PROFILE_ROUTES);
    expect(document.documentElement.lang).toBe('nl');

    await wrapper.find('[data-testid="taal-en"]').trigger('change');
    await flushPromises();

    expect(document.documentElement.lang).toBe('en');
    await expectNoViolations(wrapper.element);
    wrapper.unmount();
  });
});
