import '@nldd/design-system';

import { mount } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { createMemoryHistory, createRouter, type Router } from 'vue-router';

import { expectNoAxeViolations } from '../../tests/a11y';
import { _resetBreadcrumbs, breadcrumbsFor } from '../composables/breadcrumbs';
import { _setLocaleForTest } from '../i18n';
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

describe('Privacy: the previous name or title in the audit log', () => {
  /** The list items and the paragraphs of the page, as a reader gets them. */
  function privacyTexts(): { items: string[]; paragraphs: string[] } {
    wrapper = mount(Privacy, { attachTo: document.body, global: { plugins: [router] } });
    return {
      items: wrapper.findAll('li').map((item) => item.text()),
      paragraphs: wrapper.findAll('p').map((paragraph) => paragraph.text()),
    };
  }

  it('tells a Dutch reader that a rename puts the previous name or title in the entry', () => {
    const { items } = privacyTexts();

    expect(items).toContain(
      'Auditgegevens: wie wat wanneer publiceerde of wijzigde, en wie wanneer inlogde. ' +
        'Hernoem je een groep of site, dan staat ook de vorige naam of titel in die regel.',
    );
  });

  it('names that text as the exception to the pseudonym, in Dutch', () => {
    const { paragraphs } = privacyTexts();

    const visibility = paragraphs.find((text) => text.startsWith('Het auditlog is alleen in te zien'));
    expect(visibility).toContain(
      'In de regels staan jouw naam en e-mailadres niet, maar een pseudoniem: een versleutelde weergave van je SSO-id. ' +
        'Een uitzondering is de vorige naam van een groep of de vorige titel van een site: die tekst staat er leesbaar in.',
    );
    expect(visibility).not.toContain('In de regels staat geen naam');
  });

  it('tells an English reader the same', () => {
    _setLocaleForTest('en');
    const { items, paragraphs } = privacyTexts();

    expect(items).toContain(
      'Audit data: who published or changed what and when, and who signed in when. ' +
        'If you rename a group or site, the previous name or title is in that entry.',
    );
    const visibility = paragraphs.find((text) => text.startsWith('The audit log can only be viewed'));
    expect(visibility).toContain(
      'The entries hold neither your name nor your e-mail address, but a pseudonym: an encrypted representation of your SSO ID. ' +
        'The exception is the previous name of a group or the previous title of a site: that text is readable in the entry.',
    );
    expect(visibility).not.toContain('The entries hold no name');
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
