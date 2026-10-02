import '@nldd/design-system';

import { flushPromises, mount } from '@vue/test-utils';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { createMemoryHistory, createRouter } from 'vue-router';

import { expectNoAxeViolations } from '../../tests/a11y';
import { _setLocaleForTest } from '@/i18n';
import { appVersion } from '../version';
import WhatsNew from './WhatsNew.vue';

vi.mock('../version', () => ({ appVersion: vi.fn(() => 'dev') }));

const SOURCE = {
  './content/releases/2026.9.30.nl.md': '## Nieuw\n\nEen **zin**.',
  './content/releases/2026.9.30.en.md': '## New\n\nA **sentence**.',
  './content/releases/2026.9.30.1.nl.md': 'Later op de dag',
  './content/releases/2026.9.30.1.en.md': 'Later that day',
  './content/releases/2026.10.1.nl.md': '- Eerste\n- Tweede',
  './content/releases/2026.10.1.en.md': '- First\n- Second',
  './content/releases/unreleased.en.md': 'Not shown',
};

let wrapper: ReturnType<typeof mount> | null = null;

afterEach(() => {
  wrapper?.unmount();
  wrapper = null;
  vi.mocked(appVersion).mockReturnValue('dev');
  _setLocaleForTest('nl');
  document.body.innerHTML = '';
});

async function mountPage(source?: Record<string, string>) {
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: {} }] });
  await router.push('/');
  // The shell draws the main landmark around a page; axe wants one here too.
  const main = document.body.appendChild(document.createElement('main'));
  wrapper = mount(WhatsNew, {
    props: { source },
    global: { plugins: [router] },
    attachTo: main,
  });
  await flushPromises();
  return wrapper;
}

describe('WhatsNew', () => {
  it('says nothing about the version on a dev build', async () => {
    _setLocaleForTest('en');
    const page = await mountPage(SOURCE);

    expect(page.text()).not.toContain('You are using');
  });

  it('names the version above the first date, and nothing more, when its day is the newest', async () => {
    _setLocaleForTest('en');
    vi.mocked(appVersion).mockReturnValue('2026.10.1.3');
    const page = await mountPage(SOURCE);

    const rich = page.find('nldd-rich-text').element;
    expect(rich.children[0]!.tagName.toLowerCase()).toBe('p');
    expect(rich.children[0]!.textContent).toBe('You are using version 2026.10.1.3.');
    expect(rich.children[1]!.tagName.toLowerCase()).toBe('h2');
  });

  it('says nothing you would notice changed since the newest note when the version is later', async () => {
    _setLocaleForTest('en');
    vi.mocked(appVersion).mockReturnValue('2026.10.5');
    const page = await mountPage(SOURCE);

    expect(page.find('p').text()).toBe(
      'You are using version 2026.10.5. Nothing you would notice has changed since 1 October 2026.',
    );
  });

  it('says it in Dutch too', async () => {
    vi.mocked(appVersion).mockReturnValue('2026.10.5');
    const page = await mountPage(SOURCE);

    expect(page.find('p').text()).toBe(
      'Je gebruikt versie 2026.10.5. Sinds 1 oktober 2026 is er niets veranderd dat je merkt.',
    );
  });

  it('treats a version that is not a CalVer as having no notes for its day', async () => {
    _setLocaleForTest('en');
    vi.mocked(appVersion).mockReturnValue('2026.10.1-5-g1a2b3c4');
    const page = await mountPage(SOURCE);

    expect(page.find('p').text()).toContain('Nothing you would notice has changed since 1 October 2026.');
  });

  it('shows only the first sentence next to the empty state when there are no notes', async () => {
    _setLocaleForTest('en');
    vi.mocked(appVersion).mockReturnValue('2026.10.5');
    const page = await mountPage({});

    expect(page.findAll('p').map((p) => p.text())).toEqual([
      'You are using version 2026.10.5.',
      'No releases yet.',
    ]);
  });

  it('groups releases under a date heading, newest first, in the language on screen', async () => {
    _setLocaleForTest('en');
    const page = await mountPage(SOURCE);

    expect(page.find('h1').text()).toBe("What's new in Plak");
    const headings = page.findAll('h2');
    expect(headings.map((h) => h.text())).toEqual(['1 October 2026', '30 September 2026']);
    expect(headings[0]!.find('time').attributes('datetime')).toBe('2026-10-01');
    expect(headings.map((h) => h.attributes('id'))).toEqual(['d2026-10-01', 'd2026-09-30']);
    // The releases of one day sit under one date, newest first, and the
    // versions themselves are not shown.
    expect(page.text()).not.toContain('2026.9.30.1');
    const later = page.findAll('p').map((p) => p.text());
    expect(later.indexOf('Later that day')).toBeLessThan(later.indexOf('A sentence.'));
    expect(page.findAll('li').map((li) => li.text())).toEqual(['First', 'Second']);
    // The note's own ## sits one level under the date heading.
    expect(page.find('h3').text()).toBe('New');
    expect(page.find('strong').text()).toBe('sentence');
    expect(page.text()).not.toContain('Not shown');
  });

  it('puts no wrapper between nldd-rich-text and its content, which only spaces direct children', async () => {
    const page = await mountPage(SOURCE);

    const rich = page.find('nldd-rich-text').element;
    const tags = [...rich.children].map((child) => child.tagName.toLowerCase());
    expect(tags.length).toBeGreaterThan(0);
    expect(tags.every((tag) => ['h2', 'h3', 'p', 'ul'].includes(tag))).toBe(true);
  });

  it('follows the Dutch interface', async () => {
    const page = await mountPage(SOURCE);

    expect(page.find('h1').text()).toBe('Wat is er nieuw in Plak');
    expect(page.find('h2').text()).toBe('1 oktober 2026');
    expect(page.find('h2').attributes('id')).toBe('d2026-10-01');
    expect(page.text()).toContain('Tweede');
  });

  it('says so when there are no released notes', async () => {
    const page = await mountPage({ './content/releases/unreleased.nl.md': 'x' });

    expect(page.text()).toContain('Er zijn nog geen releases.');
    expect(page.find('h2').exists()).toBe(false);
  });

  it('says so in English too', async () => {
    _setLocaleForTest('en');
    const page = await mountPage({});

    expect(page.text()).toContain('No releases yet.');
  });

  it('reads the bundled notes without a source', async () => {
    const page = await mountPage();

    expect(page.find('h1').exists()).toBe(true);
  });

  it('has no accessibility violations', async () => {
    await mountPage(SOURCE);
    await expectNoAxeViolations(document.querySelector('main')!);
  });
});
