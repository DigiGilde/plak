import '@nldd/design-system';

import { readdirSync, readFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { flushPromises, mount } from '@vue/test-utils';
import axe from 'axe-core';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { defineComponent, h } from 'vue';
import { createMemoryHistory, createRouter, type Router } from 'vue-router';

import { makeMockBackend, type MockBackend } from './api/mock';
import App from './App.vue';
import { _resetCurrentMemberCache } from './composables/currentMember';
import { _resetBreadcrumbs, setBreadcrumbs } from './composables/breadcrumbs';
import { appVersion } from './version';

vi.mock('./version', () => ({ appVersion: vi.fn(() => 'dev') }));

let wrapper: ReturnType<typeof mount> | null = null;
let backend: MockBackend;
let router: Router;

beforeEach(() => {
  backend = makeMockBackend();
  vi.stubGlobal('fetch', backend.fetch);
  _resetCurrentMemberCache();
  _resetBreadcrumbs();
  router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: { template: '<div />' } },
      { path: '/-/groups', component: { template: '<div />' } },
    ],
  });
});

afterEach(() => {
  wrapper?.unmount();
  wrapper = null;
  vi.unstubAllGlobals();
  // The axe tests hang the shell in the document for real; whatever is left of
  // that would give the next test a second toolbar and a second
  // id="page-footer".
  document.body.innerHTML = '';
});

// Vue sets href/text on Lit elements as a DOM property (the accessor exists),
// not as an attribute; so assert on properties, after a tick so Lit has run its
// update.
async function mountApp(): Promise<ReturnType<typeof mount>> {
  await router.push('/');
  await router.isReady();
  wrapper = mount(App, {
    global: { plugins: [router], stubs: { RouterView: true, RouterLink: true } },
  });
  await flushPromises();
  await new Promise((resolve) => setTimeout(resolve, 0));
  return wrapper;
}

/**
 * The shell in the document itself, with a page that supplies only its h1: axe
 * judges the rendered DOM, and a stubbed router-view would rob the page of its
 * heading while the shell has none of its own.
 */
async function mountAppForAxe(): Promise<ReturnType<typeof mount>> {
  router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/', component: defineComponent({ render: () => h('h1', 'Overzicht') }) }],
  });
  return mountAppInDocument();
}

/**
 * The same shell, but really in the document: only then does Lit run its first
 * render, so only then is there anything to read out of a shadow root.
 */
async function mountAppInDocument(): Promise<ReturnType<typeof mount>> {
  await router.push('/');
  await router.isReady();
  wrapper = mount(App, { global: { plugins: [router] }, attachTo: document.body });
  await flushPromises();
  await new Promise((resolve) => setTimeout(resolve, 0));
  return wrapper;
}

const AXE_OPTIONS: axe.RunOptions = {
  rules: {
    // jsdom has no renderer or canvas; contrast is covered by the design
    // system's own tokens, not by this composition.
    'color-contrast': { enabled: false },
    // nldd-page wraps the footer slot in a <footer> inside its shadow root and
    // nldd-page-footer puts role="contentinfo" on its own host in there
    // (deliberately: assistive tech does not reliably pick up a <footer> from a
    // shadow root as a landmark). That nested contentinfo therefore comes from
    // the design system and cannot be fixed from here; everything around main
    // stays enabled.
    'landmark-no-duplicate-contentinfo': { enabled: false },
    'landmark-contentinfo-is-top-level': { enabled: false },
    'landmark-unique': { enabled: false },
  },
};

/**
 * nldd-skip-link belongs before the shell and therefore falls outside every
 * landmark by definition. Excluding only that one component keeps the region
 * rule in force for the rest of the shell.
 */
function axeContext(): axe.ElementContext {
  return { include: [document.body], exclude: ['nldd-skip-link'] };
}

async function expectNoAxeViolations(): Promise<void> {
  const result = await axe.run(axeContext(), AXE_OPTIONS);
  expect(result.violations, JSON.stringify(result.violations, null, 2)).toEqual([]);
}

function property<T>(el: Element, name: string): T {
  return (el as unknown as Record<string, T>)[name];
}

/** Custom elements get their props from Vue as a property, not as an attribute. */
function withText(app: ReturnType<typeof mount>, selector: string, text: string) {
  return app.findAll(selector).find((el) => property<string>(el.element, 'text') === text);
}

function footerItem(app: ReturnType<typeof mount>, href: string): Element {
  return app
    .findAll('nldd-page-footer-legal-bar-item')
    .map((item) => item.element)
    .find((element) => property<string>(element, 'href') === href)!;
}

function texts(
  root: { findAll: (selector: string) => { element: Element }[] },
  selector: string,
) {
  return root.findAll(selector).map((el) => property<string>(el.element, 'text'));
}

describe('App', () => {
  it('warns in a status bar that Plak is still in development', async () => {
    const wrapper = await mountApp();

    const bar = wrapper.find('[data-testid="beta-banner"]');
    expect(bar.exists(), wrapper.html().slice(0, 400)).toBe(true);
    expect(bar.element.tagName.toLowerCase()).toBe('nldd-status-bar');
    // Detached, Lit does not reflect; read the property Vue set.
    expect(property<string>(bar.element, 'text')).toBe(
      'Bètaversie - Plak is in ontwikkeling en kan fouten bevatten',
    );
    expect(property<string>(bar.element, 'variant')).toBe('warning');
    // The bar shows one line and truncates the rest, so the first word has
    // to carry the message; otherwise nothing is left on a phone.
    expect(property<string>(bar.element, 'text').startsWith('Bètaversie')).toBe(true);

    // Above the skip link's target: whoever jumps to the content wants the
    // content, not this notice.
    const content = wrapper.find('#hoofdinhoud').element;
    expect(
      bar.element.compareDocumentPosition(content) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
  });

  it('offers the platform pages and the API documentation in the global footer', async () => {
    const app = await mountApp();

    expect(app.find('nldd-page-footer').exists()).toBe(true);
    const items = app.findAll('nldd-page-footer-legal-bar-item');
    expect(items.map((item) => property<string>(item.element, 'href'))).toEqual([
      '/-/whats-new',
      '/-/about',
      '/-/accessibility',
      '/-/privacy',
      '/-/api/docs',
    ]);
    // One group: per NLDD the end slot is the place for links like these
    // (start is for a copyright line or version number). That keeps them
    // together on the right and stops them wrapping into two rows on a narrow
    // screen.
    expect(items.map((item) => item.attributes('slot'))).toEqual([
      'start',
      'end',
      'end',
      'end',
      'end',
    ]);
  });

  it('links to the release notes page in the footer on a dev build', async () => {
    const app = await mountApp();

    const link = footerItem(app, '/-/whats-new');
    expect(property<string>(link, 'text')).toBe('Wat is er nieuw');
  });

  it('names a version that is not a CalVer but links to the page itself', async () => {
    vi.mocked(appVersion).mockReturnValue('2026.9.30-5-g1a2b3c4');
    try {
      const app = await mountApp();

      const link = footerItem(app, '/-/whats-new');
      expect(property<string>(link, 'text')).toBe('Versie 2026.9.30-5-g1a2b3c4');
    } finally {
      vi.mocked(appVersion).mockReturnValue('dev');
    }
  });

  it('links to the day of the version when the notes have one for it', async () => {
    vi.mocked(appVersion).mockReturnValue('2026.9.30.2');
    try {
      const app = await mountApp();

      const link = footerItem(app, '/-/whats-new#d2026-09-30');
      expect(property<string>(link, 'text')).toBe('Versie 2026.9.30.2');
    } finally {
      vi.mocked(appVersion).mockReturnValue('dev');
    }
  });

  it('links to the plain page when the notes have no day for the version', async () => {
    vi.mocked(appVersion).mockReturnValue('2026.10.1.2');
    try {
      const app = await mountApp();

      const link = footerItem(app, '/-/whats-new');
      expect(property<string>(link, 'text')).toBe('Versie 2026.10.1.2');
    } finally {
      vi.mocked(appVersion).mockReturnValue('dev');
    }
  });

  it('has a skip link to the main content', async () => {
    const app = await mountApp();

    const skipLink = app.find('nldd-skip-link');
    expect(property<string>(skipLink.element, 'href')).toBe('#hoofdinhoud');
    expect(property<string>(skipLink.element, 'text')).toBe('Direct naar de inhoud');
    const target = app.find('#hoofdinhoud');
    expect(target.exists()).toBe(true);
    // A div is not focusable, so without tabindex the skip link only moves the
    // scroll position and focus stays at the top.
    expect(target.attributes('tabindex')).toBe('-1');
  });

  it('renders the whole [P]lak brand as media of the toolbar title, without separate title text', async () => {
    const app = await mountApp();

    const title = app.find('nldd-toolbar-title');
    // With both `text` and a P block, the P would be read out twice.
    expect(property<string | undefined>(title.element, 'text')).toBeFalsy();
    expect(property<string>(title.element, 'href')).toBe('/');

    const brand = title.find('[slot="media"]');
    expect(brand.exists()).toBe(true);
    // The wordmark carries the window's name, since there is no title text left.
    expect(brand.attributes('role')).toBe('img');
    expect(brand.attributes('aria-label')).toContain('Plak');
    expect(brand.text().replace(/\s+/g, '')).toBe('Plak');
    expect(brand.findAll('[aria-hidden="true"]')).toHaveLength(2);
  });

  it('hangs the toolbar in a wrapper, since the bar does not draw its own gutter', async () => {
    const app = await mountApp();

    // The header slot stacks two things: the status bar at full width, and
    // below it the toolbar in its own gutter.
    const header = app.find('[slot="header"]');
    expect(header.exists()).toBe(true);
    expect([...header.element.children].map((c) => c.tagName.toLowerCase())).toEqual([
      'nldd-status-bar',
      'div',
    ]);

    const wrap = header.find('.toolbar');
    expect(wrap.exists()).toBe(true);
    // With the bar itself in the header slot, the wordmark starts at x=0 and
    // the left half of its focus ring falls off screen.
    const bar = wrap.find('nldd-toolbar');
    expect(bar.exists()).toBe(true);
    expect(bar.attributes('slot')).toBeUndefined();
  });

  // jsdom computes no layout, so whether the wordmark really sits at the same x
  // as the h1 cannot be measured here; what can, is that the gutter comes from
  // the same tokens as the page sections and the footer, not from a loose
  // number.
  it('derives the toolbar gutter from the page section tokens', () => {
    const source = readFileSync(resolve(dirname(fileURLToPath(import.meta.url)), 'App.vue'), 'utf-8');
    const style = source.slice(source.indexOf('<style'));

    expect(style).toContain('--semantics-page-sections-body-max-width');
    for (const step of ['sm', 'md', 'lg']) {
      expect(style).toContain(`--semantics-page-sections-${step}-margin-inline`);
    }
  });

  // Waggle fixes its menu button in place with a shadow under it; here the
  // header is part of the page and nothing lifts itself above the text. A
  // variant covers the fill, but a shadow could only come from this CSS.
  it('does not lift the menu button with its own shadow or border', () => {
    const source = readFileSync(resolve(dirname(fileURLToPath(import.meta.url)), 'App.vue'), 'utf-8');
    const style = source.slice(source.indexOf('<style'));

    expect(style).not.toContain('box-shadow');
    expect(style).not.toContain('position: fixed');
  });

  it('carries one menu button on the right, without fill and without shadow', async () => {
    const app = await mountApp();

    const endItems = app.findAll('nldd-toolbar-item[slot="end"]');
    expect(endItems).toHaveLength(1);
    const button = endItems[0].find('nldd-button');
    expect(property<string>(button.element, 'text')).toBe('Menu');
    expect(property<string>(button.element, 'startIcon')).toBe('menu');
    expect(property<string>(button.element, 'popupType')).toBe('menu');
    // This app's quiet variant: no filled button lying over the page like a
    // bar.
    expect(property<string>(button.element, 'variant')).toBe('neutral-transparent');
    expect(button.find('nldd-menu[slot="popup"]').exists()).toBe(true);
  });

  // `expandable` draws the chevron and forces aria-expanded; popup-type does
  // that second part too. The button therefore carries popup-type only, and this
  // pins down that the chevron is gone while the open/closed state remains for
  // assistive tech.
  it('carries no chevron, but keeps aria-expanded on the menu button', async () => {
    // Mounted in the document: a custom element only draws its shadow root once
    // it is connected, and this is precisely about what is in there.
    const app = await mountAppInDocument();

    const button = app.find('nldd-toolbar-item[slot="end"] nldd-button');
    expect(property<boolean>(button.element, 'expandable')).toBe(false);
    expect(button.element.shadowRoot?.querySelector('.button__disclosure-icon')).toBeNull();

    // popup-type carries the open/closed state `expandable` otherwise forced.
    const inner = button.element.shadowRoot?.querySelector('button');
    expect(inner?.getAttribute('aria-haspopup')).toBe('menu');
    expect(inner?.getAttribute('aria-expanded')).toBe('false');
  });

  it('carries the navigation in that one menu and the account group below it', async () => {
    const app = await mountApp();

    const menu = app.find('nldd-menu[slot="popup"]');
    const items = menu.findAll('nldd-menu-item');
    expect(items.map((item) => property<string>(item.element, 'text'))).toEqual([
      'Overzicht',
      'Groepen',
      'Platformbeheer',
      'Profiel',
      'Gekoppelde CLI-sessies',
      'Uitloggen',
    ]);
    // Navigation items are real links, so middle click and "copy link" work;
    // the action item has nothing to link to.
    // The action item has nothing to link to; NLDD reflects that as an empty
    // href rather than undefined.
    expect(items.map((item) => property<string>(item.element, 'href'))).toEqual([
      '/',
      '/-/groups',
      '/-/members',
      '/-/profile',
      '/-/sessions',
      '',
    ]);

    // Platformbeheer is a page like any other: it sits with the navigation, not
    // with the account actions.
    const group = menu.find('nldd-menu-group');
    expect(property<string>(group.element, 'text')).toBe('Account');
    expect(texts(group, 'nldd-menu-item')).toEqual(['Profiel', 'Gekoppelde CLI-sessies', 'Uitloggen']);

    // Who you are sits inside that group, no longer above the list or on the
    // button itself.
    const identity = group.find('nldd-identity');
    expect(property<string>(identity.element, 'text')).toBe('Bea Heerder');
    expect(property<string>(identity.element, 'supportingText')).toBe(
      'beheerder@voorbeeld.nl',
    );
  });

  // The children of a menu are menu items and an identity is not one, so the
  // block around it has to stay out of the accessibility tree; the same name
  // and address are already on the page behind the menu.
  it('hides the identity block in the account group from assistive tech', async () => {
    const app = await mountApp();

    // Both in the menu itself and in the overflow twin the toolbar clones.
    const blocks = app.findAll('nldd-menu-group nldd-container');
    expect(blocks).toHaveLength(2);
    for (const block of blocks) {
      expect(block.attributes('aria-hidden')).toBe('true');
      expect(block.find('nldd-identity').exists()).toBe(true);
    }
  });

  it('keeps "Platformbeheer" away from whoever is not a platform admin', async () => {
    backend.data.loggedInMemberId = 'lid-3';
    const app = await mountApp();

    expect(texts(app.find('nldd-menu[slot="popup"]'), 'nldd-menu-item')).toEqual([
      'Overzicht',
      'Groepen',
      'Profiel',
      'Gekoppelde CLI-sessies',
      'Uitloggen',
    ]);
  });

  it('keeps navigation inside the SPA on a plain click', async () => {
    const app = await mountApp();

    const groups = withText(app, 'nldd-menu[slot="popup"] nldd-menu-item', 'Groepen')!;
    groups.element.dispatchEvent(
      new MouseEvent('click', { bubbles: true, cancelable: true, button: 0 }),
    );
    await flushPromises();

    expect(router.currentRoute.value.fullPath).toBe('/-/groups');
  });

  it('keeps navigation inside the SPA for the account items too', async () => {
    const app = await mountApp();

    const profile = withText(app, 'nldd-menu-group nldd-menu-item', 'Profiel')!;
    profile.element.dispatchEvent(
      new MouseEvent('click', { bubbles: true, cancelable: true, button: 0 }),
    );
    await flushPromises();

    expect(router.currentRoute.value.fullPath).toBe('/-/profile');

    const sessions = withText(app, 'nldd-menu-group nldd-menu-item', 'Gekoppelde CLI-sessies')!;
    sessions.element.dispatchEvent(new KeyboardEvent('keydown', { key: ' ', bubbles: true }));
    await flushPromises();

    expect(router.currentRoute.value.fullPath).toBe('/-/sessions');
  });

  it('lets a click with a modifier key keep the native link behavior', async () => {
    const app = await mountApp();

    const groups = withText(app, 'nldd-menu[slot="popup"] nldd-menu-item', 'Groepen')!;
    const event = new MouseEvent('click', {
      bubbles: true,
      cancelable: true,
      button: 0,
      metaKey: true,
    });
    groups.element.dispatchEvent(event);
    await flushPromises();

    // Not intercepted, so the browser opens the href itself (new tab).
    expect(event.defaultPrevented).toBe(false);
    expect(router.currentRoute.value.fullPath).toBe('/');
  });

  it('navigates with the space bar, which otherwise only scrolls on a link', async () => {
    const app = await mountApp();

    const groups = withText(app, 'nldd-menu[slot="popup"] nldd-menu-item', 'Groepen')!;
    groups.element.dispatchEvent(new KeyboardEvent('keydown', { key: ' ', bubbles: true }));
    await flushPromises();
    expect(router.currentRoute.value.fullPath).toBe('/-/groups');

    // Other keys stay with the component itself.
    const overview = withText(app, 'nldd-menu[slot="popup"] nldd-menu-item', 'Overzicht')!;
    overview.element.dispatchEvent(new KeyboardEvent('keydown', { key: 'a', bubbles: true }));
    await flushPromises();
    expect(router.currentRoute.value.fullPath).toBe('/-/groups');
  });

  it('offers the same list in the overflow slot of the toolbar item', async () => {
    const app = await mountApp();

    const item = app.findAll('nldd-toolbar-item[slot="end"]')[0];
    // On overflow nldd-toolbar hides the button and clones only the overflow
    // slot into the "..." menu; whatever is missing there is unreachable.
    expect(
      texts(item, '[slot="overflow"] nldd-menu-item, nldd-menu-item[slot="overflow"]'),
    ).toEqual(texts(item, 'nldd-menu[slot="popup"] nldd-menu-item'));
    expect(property<string>(app.find('nldd-menu-group[slot="overflow"]').element, 'text')).toBe(
      'Account',
    );
  });

  it('also keeps the admin action out of the overflow slot for whoever is not an admin', async () => {
    backend.data.loggedInMemberId = 'lid-3';
    const app = await mountApp();

    const item = app.findAll('nldd-toolbar-item[slot="end"]')[0];
    expect(
      texts(item, '[slot="overflow"] nldd-menu-item, nldd-menu-item[slot="overflow"]'),
    ).toEqual(['Overzicht', 'Groepen', 'Profiel', 'Gekoppelde CLI-sessies', 'Uitloggen']);
  });

  it('logs out via the item that the toolbar clones into its overflow menu', async () => {
    const app = await mountApp();

    const form = app.find('form').element as HTMLFormElement;
    const submit = vi.fn();
    form.submit = submit;

    // The clone in the overflow menu forwards its select to this original; the
    // overflow itself cannot be reproduced, because jsdom measures no widths.
    const logout = app
      .findAll('[slot="overflow"] nldd-menu-item')
      .find((item) => property<string>(item.element, 'text') === 'Uitloggen');
    expect(logout).toBeDefined();
    logout!.element.dispatchEvent(new CustomEvent('select'));

    expect(submit).toHaveBeenCalledTimes(1);
  });

  it('leaves a select on a navigation item alone: that is a link', async () => {
    const app = await mountApp();

    const form = app.find('form').element as HTMLFormElement;
    const submit = vi.fn();
    form.submit = submit;

    // Both copies of the navigation: neither the menu itself nor the overflow
    // twin may hang an action on what is a link.
    for (const selector of [
      'nldd-menu[slot="popup"] nldd-menu-item',
      'nldd-menu-item[slot="overflow"]',
    ]) {
      const item = withText(app, selector, 'Platformbeheer');
      expect(item).toBeDefined();
      item!.element.dispatchEvent(new CustomEvent('select'));
    }
    await flushPromises();

    expect(submit).not.toHaveBeenCalled();
    expect(router.currentRoute.value.fullPath).toBe('/');
  });

  it('falls back to the e-mail address when the IdP supplies no name', async () => {
    backend.data.members.find((l) => l.id === 'lid-1')!.name = '';
    const app = await mountApp();

    const identity = app.find('nldd-menu-group nldd-identity');
    expect(property<string>(identity.element, 'text')).toBe('beheerder@voorbeeld.nl');
    // Name and supporting text would otherwise say the e-mail address twice.
    expect(property<string | undefined>(identity.element, 'supportingText')).toBeFalsy();
  });

  it('shows an empty name rather than crash when the IdP supplies neither', async () => {
    const lid = backend.data.members.find((l) => l.id === 'lid-1')!;
    lid.name = '';
    lid.email = '';
    const app = await mountApp();

    const identity = app.find('nldd-menu-group nldd-identity');
    expect(property<string>(identity.element, 'text')).toBe('');
  });

  it('does not navigate on a click of the logout item, which only acts on select', async () => {
    const app = await mountApp();

    const logout = withText(app, 'nldd-menu-group nldd-menu-item', 'Uitloggen')!;
    logout.element.dispatchEvent(
      new MouseEvent('click', { bubbles: true, cancelable: true, button: 0 }),
    );
    await flushPromises();

    expect(router.currentRoute.value.fullPath).toBe('/');
  });

  it('leaves a select on an account link alone, since that only acts for logout', async () => {
    const app = await mountApp();

    const form = app.find('form').element as HTMLFormElement;
    const submit = vi.fn();
    form.submit = submit;

    const profile = withText(app, 'nldd-menu-group nldd-menu-item', 'Profiel')!;
    profile.element.dispatchEvent(new CustomEvent('select'));

    expect(submit).not.toHaveBeenCalled();
  });

  it('does not crash the shell when the session cannot be fetched at all', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('netwerkfout')));

    const app = await mountApp();

    expect(app.find('nldd-toolbar-title').exists()).toBe(true);
  });

  it('does not show the menu button without a session', async () => {
    backend.data.loggedInMemberId = null;
    const app = await mountApp();

    expect(app.findAll('nldd-toolbar-item[slot="end"]')).toHaveLength(0);
    expect(app.find('nldd-toolbar-title').exists()).toBe(true);
  });

  it('logs out with a POST to /-/logout', async () => {
    const app = await mountApp();

    const form = app.find('form').element as HTMLFormElement;
    expect(form.method).toBe('post');
    expect(form.getAttribute('action')).toBe('/-/logout');
    const submit = vi.fn();
    form.submit = submit;

    const logout = app
      .findAll('nldd-menu-item')
      .find((item) => property<string>(item.element, 'text') === 'Uitloggen');
    expect(logout).toBeDefined();
    logout!.element.dispatchEvent(new CustomEvent('select'));

    expect(submit).toHaveBeenCalledTimes(1);
  });

  it('shows the page breadcrumbs in the footer, not at the top', async () => {
    const app = await mountApp();

    expect(app.find('nldd-breadcrumbs').exists()).toBe(false);

    setBreadcrumbs('/', [{ text: 'Overzicht', href: '/' }, { text: 'Team Aurora' }]);
    await flushPromises();

    const breadcrumbs = app.find('nldd-page-footer nldd-breadcrumbs[slot="breadcrumbs"]');
    expect(breadcrumbs.exists()).toBe(true);
    const items = breadcrumbs.findAll('nldd-breadcrumbs-item');
    expect(items.map((item) => property<string>(item.element, 'text'))).toEqual([
      'Overzicht',
      'Team Aurora',
    ]);
    expect(property<boolean>(items[1].element, 'current')).toBe(true);
  });

  it('does not show the breadcrumbs of another route', async () => {
    setBreadcrumbs('/-/members', [{ text: 'Plak', href: '/' }, { text: 'Leden' }]);
    const app = await mountApp();

    expect(app.find('nldd-breadcrumbs').exists()).toBe(false);
  });

  // The axe suites only check pages (group, site tabs, overview), while
  // wordmark, Nieuw button, user menu and footer come together here. Without
  // this run, the duplicate main the shell wrapped around nldd-page's own one
  // stayed invisible.
  describe('axe', () => {
    it('leaves the main landmark to nldd-page and does not wrap a second one around it', async () => {
      const app = await mountAppForAxe();

      const page = app.find('nldd-page').element;
      expect(page.querySelector('main')).toBeNull();
      expect(page.shadowRoot!.querySelectorAll('main')).toHaveLength(1);
    });

    it('is without violations in the logged-in shell, breadcrumbs included', async () => {
      setBreadcrumbs('/', [{ text: 'Overzicht', href: '/' }, { text: 'Team Aurora' }]);
      await mountAppForAxe();

      await expectNoAxeViolations();
    });

    it('is without violations without a session, when the toolbar carries only the brand', async () => {
      backend.data.loggedInMemberId = null;
      await mountAppForAxe();

      await expectNoAxeViolations();
    });
  });

  // A var() with a non-existent token name falls back silently and, in the
  // source, looks as if the design system is being followed; only a comparison
  // against the shipped CSS catches it.
  it('refers in its own CSS only to tokens the design system provides', () => {
    const hereDir = dirname(fileURLToPath(import.meta.url));
    const cssPath = resolve(hereDir, '../node_modules/@nldd/design-system/dist/css');
    const systemCss = readdirSync(cssPath)
      .filter((name) => name.endsWith('.css'))
      .map((name) => readFileSync(join(cssPath, name), 'utf-8'))
      .join('\n');

    const source = readFileSync(resolve(hereDir, 'App.vue'), 'utf-8');
    const used = [...source.matchAll(/var\(\s*(--[a-z0-9-]+)/gi)].map(([, name]) => name);

    expect(used.length).toBeGreaterThan(0);
    for (const token of used) {
      expect(systemCss.includes(`${token}:`), `unknown token ${token}`).toBe(true);
    }
  });
});
