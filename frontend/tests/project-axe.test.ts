/**
 * Axe check per tab of the site detail page. jsdom has no layout
 * and no colours, so the rules that depend on those are off; the structural
 * rules (labels, headings, aria references, names) do run. The full WCAG check
 * in a real browser follows in the E2E phase.
 *
 * Importing '@nldd/design-system' registers the real custom elements: without
 * that registration every nldd element stays an un-upgraded inline element
 * without shadow DOM, role or name, and axe only checks the bare HTML around
 * them. As in tests/group-axe.test.ts, this check therefore stands apart from
 * the tab tests, which do assert on attribute bindings.
 */
import '@nldd/design-system';

import axe from 'axe-core';
import { mount } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { defineComponent, h, type Component } from 'vue';
import { createMemoryHistory, createRouter, RouterView } from 'vue-router';

import { makeMockBackend, MOCK_CONTENT_BASE } from '../src/api/mock';
import TabDeploy from '../src/components/site/TabDeploy.vue';
import TabOverview from '../src/components/site/TabOverview.vue';
import TabPreviews from '../src/components/site/TabPreviews.vue';
import TabAccess from '../src/components/site/TabAccess.vue';
import TabMembers from '../src/components/site/TabMembers.vue';
import TabSettings from '../src/components/site/TabSettings.vue';
import TabVersions from '../src/components/site/TabVersions.vue';
import { fireDetailEvent, untilIdle } from '../src/components/site/testHelpers';
import { _resetCurrentMemberCache } from '../src/composables/currentMember';
import Site from '../src/pages/Site.vue';

/**
 * jsdom 25 reflects the ARIA properties of ElementInternals onto a
 * `_internalContentAttributeMap` it never creates itself, so both reading and
 * writing throw a TypeError. nldd-file-field sets `internals.ariaLabel` in its
 * willUpdate, and that error comes back as an unhandled rejection that fails the
 * whole run. Plain properties instead of the reflectors: jsdom's accessibility
 * tree does not read ElementInternals anyway, so axe loses no information here.
 */
{
  const proto = (globalThis as { ElementInternals?: { prototype: object } }).ElementInternals
    ?.prototype;
  for (const name of proto ? Object.getOwnPropertyNames(proto) : []) {
    if (name !== 'role' && !name.startsWith('aria')) continue;
    Object.defineProperty(proto, name, { value: null, writable: true, configurable: true });
  }
}

beforeEach(() => {
  vi.stubGlobal('fetch', makeMockBackend().fetch);
  // Module-level session cache: a test that signs in as someone else must not
  // leave that member behind for the next one.
  _resetCurrentMemberCache();
});

afterEach(() => {
  vi.unstubAllGlobals();
  document.body.innerHTML = '';
});

const AXE_OPTIONS: axe.RunOptions = {
  rules: {
    // No layout or colour in jsdom; and standalone tabs are page fragments (the
    // surrounding page supplies the main landmark, the h1 and the regions).
    'color-contrast': { enabled: false },
    region: { enabled: false },
    'page-has-heading-one': { enabled: false },
    'landmark-one-main': { enabled: false },
  },
};

/**
 * nldd-code-viewer renders CodeMirror in its own shadow DOM with
 * role="document" plus aria-multiline/aria-readonly (aria-allowed-attr). That is
 * design-system markup, not this app's; only that subtree is excluded, so the
 * rule stays on for our own composition.
 */
const OUT_OF_SCOPE: axe.ContextObject['exclude'] = [
  { fromShadowDom: ['nldd-code-viewer', '.cm-content'] },
  // The suggestions popup of an nldd-combo-box: the combo box gives its
  // slotted menu role="listbox" without a name. In a browser that menu is a
  // closed popover (display: none until :popover-open) and axe never reaches
  // it; jsdom applies no shadow styles, so it does. Design-system markup
  // either way, and nothing here can name a div inside its shadow root.
  { fromShadowDom: ['nldd-combo-box nldd-menu', '.menu__list'] },
];

async function expectNoViolationsIn(element: Element): Promise<void> {
  // Without registered custom elements the nldd tags stay empty inline elements
  // and axe only judges the bare HTML around them; a green run then says
  // nothing. Hence this check that they were upgraded first.
  const nlddElementen = [...element.querySelectorAll('*')].filter((el) =>
    el.tagName.toLowerCase().startsWith('nldd-'),
  );
  expect(nlddElementen.length).toBeGreaterThan(0);
  const notUpgraded = nlddElementen
    .filter((el) => Object.getPrototypeOf(el) === HTMLElement.prototype)
    .map((el) => el.tagName.toLowerCase());
  expect([...new Set(notUpgraded)]).toEqual([]);

  const result = await axe.run({ include: [element], exclude: OUT_OF_SCOPE }, AXE_OPTIONS);
  expect(result.violations, JSON.stringify(result.violations, null, 2)).toEqual([]);
}

async function expectNoViolations(component: Component): Promise<void> {
  const wrapper = mount(component, {
    props: { group: 'team-aurora', site: 'website', contentBase: MOCK_CONTENT_BASE },
    attachTo: document.body,
    // Stubbed teleports keep the edit sheets inside the checked fragment, so
    // axe sees the forms in them too.
    global: { stubs: { teleport: true } },
  });
  await untilIdle();
  await expectNoViolationsIn(wrapper.element);
  wrapper.unmount();
}

describe('axe: tabbladen van de sitedetailpagina', () => {
  it('Overzicht is zonder violations', () => expectNoViolations(TabOverview));
  it('Previews is zonder violations', () => expectNoViolations(TabPreviews));
  it('Versies is zonder violations', () => expectNoViolations(TabVersions));
  it('Toegang is zonder violations', () => expectNoViolations(TabAccess));

  it('Toegang met beide uitzonderingen aan is zonder violations', async () => {
    // The two tables and their row menus only exist when the extras are on, so
    // the default fixture would leave the busiest half of this tab unchecked.
    const backend = makeMockBackend();
    backend.data.sites[0]!.access = { base: 'sso', keys: true, invitees: true };
    vi.stubGlobal('fetch', backend.fetch);
    await expectNoViolations(TabAccess);
  });
  it('Versies met een eigen aantal bewaarde versies is zonder violations', async () => {
    // The number field only exists under the custom option; the default fixture
    // follows the platform and would leave it unchecked.
    _resetCurrentMemberCache();
    const backend = makeMockBackend();
    backend.data.sites[0]!.liveVersionsKept = 3;
    vi.stubGlobal('fetch', backend.fetch);
    const wrapper = mount(TabVersions, {
      props: { group: 'team-aurora', site: 'website', contentBase: MOCK_CONTENT_BASE },
      attachTo: document.body,
    });
    await untilIdle();
    expect(wrapper.find('[data-testid="retention-count"]').exists()).toBe(true);
    await expectNoViolationsIn(wrapper.element);
    wrapper.unmount();
  });

  it('Versies met een geweigerd aantal bij het veld is zonder violations', async () => {
    _resetCurrentMemberCache();
    const backend = makeMockBackend();
    backend.data.sites[0]!.liveVersionsKept = 3;
    vi.stubGlobal('fetch', backend.fetch);
    const wrapper = mount(TabVersions, {
      props: { group: 'team-aurora', site: 'website', contentBase: MOCK_CONTENT_BASE },
      attachTo: document.body,
    });
    await untilIdle();
    const field = wrapper.find('[data-testid="retention-count"]').element;
    field.dispatchEvent(new CustomEvent('change', { detail: { value: '5000000001' } }));
    await untilIdle();
    expect(wrapper.find('[data-testid="retention-count"]').attributes('invalid')).toBeDefined();
    await expectNoViolationsIn(wrapper.element);
    wrapper.unmount();
  });
  it('Versies met een niet opgeslagen keuze onder de opties is zonder violations', async () => {
    _resetCurrentMemberCache();
    const backend = makeMockBackend();
    backend.data.sites[0]!.liveVersionsKept = 3;
    vi.stubGlobal('fetch', (request: RequestInfo | URL, init?: RequestInit) =>
      String(request).endsWith('/live-versions-kept')
        ? Promise.reject(new TypeError('netwerk weg'))
        : backend.fetch(request, init),
    );
    const wrapper = mount(TabVersions, {
      props: { group: 'team-aurora', site: 'website', contentBase: MOCK_CONTENT_BASE },
      attachTo: document.body,
    });
    await untilIdle();
    wrapper.find('[data-testid="retention-default"]').element.dispatchEvent(new CustomEvent('change'));
    await untilIdle();
    expect(wrapper.find('[data-testid="retention-choice-error"]').exists()).toBe(true);
    await expectNoViolationsIn(wrapper.element);
    wrapper.unmount();
  });
  it('Leden is zonder violations', () => expectNoViolations(TabMembers));
  it('Deploy is zonder violations', () => expectNoViolations(TabDeploy));
  it('Instellingen is zonder violations', () => expectNoViolations(TabSettings));

  it('Instellingen voor een redacteur (de titel als tekst) is zonder violations', async () => {
    // lid-3 (Ada Vermeer) is editor in the group: no form, no danger zone.
    const backend = makeMockBackend();
    backend.data.loggedInMemberId = 'lid-3';
    vi.stubGlobal('fetch', backend.fetch);
    const wrapper = mount(TabSettings, {
      props: { group: 'team-aurora', site: 'website' },
      attachTo: document.body,
    });
    await untilIdle();
    expect(wrapper.find('[data-testid="site-title-text"]').exists()).toBe(true);
    await expectNoViolationsIn(wrapper.element);
    wrapper.unmount();
  });

  it('Instellingen met een geweigerde titel bij het veld is zonder violations', async () => {
    const wrapper = mount(TabSettings, {
      props: { group: 'team-aurora', site: 'website' },
      attachTo: document.body,
    });
    await untilIdle();
    fireDetailEvent(wrapper.find('[data-testid="site-title"]').element, 'input', {
      value: 'a'.repeat(201),
    });
    await wrapper.find('[data-testid="site-title-form"]').trigger('submit');
    await untilIdle();
    expect(wrapper.find('[data-testid="site-title"]').attributes('invalid')).toBeDefined();
    expect(wrapper.find('nldd-validation-item#site-title-server').text()).toBe(
      'Een titel is hoogstens 200 tekens lang',
    );
    await expectNoViolationsIn(wrapper.element);
    wrapper.unmount();
  });

  it('de volledige sitepagina (kop, tabs en tabblad) is zonder violations', async () => {
    const Empty = defineComponent({ render: () => h('div') });
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', name: 'overview', component: Empty },
        { path: '/:group', name: 'group', component: Empty },
        {
          path: '/:group/:site',
          component: Site,
          children: [{ path: '', name: 'site-overview', component: TabOverview }],
        },
      ],
    });
    await router.push('/team-aurora/website');
    await router.isReady();

    const Host = defineComponent({ render: () => h(RouterView) });
    const wrapper = mount(Host, {
      global: { plugins: [router] },
      attachTo: document.body,
    });
    await untilIdle();

    await expectNoViolationsIn(wrapper.element);
    wrapper.unmount();
  });

  it('de sitepagina met het tabblad Instellingen (zeven tabs) is zonder violations', async () => {
    const Empty = defineComponent({ render: () => h('div') });
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', name: 'overview', component: Empty },
        { path: '/:group', name: 'group', component: Empty },
        {
          path: '/:group/:site',
          component: Site,
          children: [
            { path: '', name: 'site-overview', component: TabOverview },
            { path: 'settings', name: 'site-settings', component: TabSettings },
          ],
        },
      ],
    });
    await router.push('/team-aurora/website/settings');
    await router.isReady();

    const Host = defineComponent({ render: () => h(RouterView) });
    const wrapper = mount(Host, {
      global: { plugins: [router] },
      attachTo: document.body,
    });
    await untilIdle();

    expect(wrapper.find('[data-testid="tab-settings"]').attributes('current')).toBeDefined();
    expect(wrapper.find('[data-testid="site-title"]').exists()).toBe(true);
    await expectNoViolationsIn(wrapper.element);
    wrapper.unmount();
  });
});
