/**
 * The privacy and accessibility statements are legal texts, and Plak is
 * installed by more than one organisation. What matters: every authority is
 * there as a working link, in both languages; the operator's address comes
 * from `GET /operator`, never from the code; and without an operator, or
 * without an answer, the statements still say who to turn to.
 */
import { flushPromises, mount } from '@vue/test-utils';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { createMemoryHistory, createRouter } from 'vue-router';

import type { Operator } from '../src/api/types';
import { _setLocaleForTest } from '../src/i18n';
import Accessibility from '../src/pages/Accessibility.vue';
import Privacy from '../src/pages/Privacy.vue';
import { makeMockBackend } from './mock-api';

afterEach(() => {
  _setLocaleForTest('nl');
  vi.unstubAllGlobals();
});

function serve(operator: Operator): void {
  const backend = makeMockBackend();
  backend.data.operator = operator;
  vi.stubGlobal('fetch', backend.fetch);
}

async function render(component: typeof Privacy, locale: 'nl' | 'en') {
  _setLocaleForTest(locale);
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/', component }],
  });
  await router.push('/');
  await router.isReady();
  const wrapper = mount(component, { global: { plugins: [router] } });
  await flushPromises();
  return wrapper;
}

function hrefs(wrapper: Awaited<ReturnType<typeof render>>): string[] {
  return wrapper.findAll('a').map((a) => a.attributes('href') ?? '');
}

const OPERATOR = { name: 'Gemeente Voorbeeld', email: 'privacy@voorbeeld.nl' };
const NOBODY = { name: null, email: null };

describe('privacy statement', () => {
  it.each(['nl', 'en'] as const)('names the operator and its address in %s', async (locale) => {
    serve(OPERATOR);
    const wrapper = await render(Privacy, locale);

    expect(hrefs(wrapper)).toEqual(['mailto:privacy@voorbeeld.nl', 'https://www.autoriteitpersoonsgegevens.nl']);
    expect(wrapper.text()).toContain('Gemeente Voorbeeld');
    wrapper.unmount();
  });

  it.each(['nl', 'en'] as const)('points to the organisation that runs it when none is named, in %s', async (locale) => {
    serve(NOBODY);
    const wrapper = await render(Privacy, locale);

    expect(hrefs(wrapper)).toEqual(['https://www.autoriteitpersoonsgegevens.nl']);
    const unnamed =
      locale === 'nl'
        ? 'De organisatie die deze installatie van Plak beheert'
        : 'The organisation that runs this installation of Plak';
    expect(wrapper.text()).toContain(unnamed);
    wrapper.unmount();
  });

  it('names an address without a name, and a name without an address', async () => {
    serve({ name: null, email: 'privacy@voorbeeld.nl' });
    const address = await render(Privacy, 'en');
    expect(hrefs(address)).toContain('mailto:privacy@voorbeeld.nl');
    expect(address.text()).toContain('The organisation that runs this installation of Plak');
    address.unmount();

    serve({ name: 'Gemeente Voorbeeld', email: null });
    const name = await render(Privacy, 'en');
    expect(hrefs(name)).not.toContain('mailto:privacy@voorbeeld.nl');
    expect(name.text()).toContain('This installation of Plak is run by Gemeente Voorbeeld.');
    expect(name.text()).toContain('to the organisation that runs this installation.');
    name.unmount();
  });

  it('still reads as a statement when the operator cannot be fetched', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.reject(new Error('netwerkfout'))));
    const wrapper = await render(Privacy, 'nl');

    expect(wrapper.text()).toContain('stuur je naar de organisatie die deze installatie beheert.');
    wrapper.unmount();
  });

  it('names no organisation of its own and does not claim to store one', async () => {
    serve(NOBODY);
    for (const locale of ['nl', 'en'] as const) {
      const wrapper = await render(Privacy, locale);
      expect(wrapper.text()).not.toMatch(/Binnenlandse|Interior|Digi Gilde|ZAD|minbzk|rijksoverheid\.nl/);
      expect(wrapper.text()).not.toMatch(/organisatie,? via inloggen|and organisation/);
      wrapper.unmount();
    }
  });
});

describe('accessibility statement', () => {
  it.each(['nl', 'en'] as const)('links register, report address and enforcement in %s', async (locale) => {
    serve(OPERATOR);
    const wrapper = await render(Accessibility, locale);

    expect(hrefs(wrapper)).toEqual([
      'https://www.toegankelijkheidsverklaring.nl',
      'mailto:privacy@voorbeeld.nl',
      'https://www.mensenrechten.nl',
    ]);
    expect(wrapper.text()).toContain('WCAG 2.1');
    expect(wrapper.text()).toContain('2026');
    wrapper.unmount();
  });

  it.each(['nl', 'en'] as const)('sends a report to the organisation that runs it when none is named, in %s', async (locale) => {
    serve(NOBODY);
    const wrapper = await render(Accessibility, locale);

    expect(hrefs(wrapper)).toEqual(['https://www.toegankelijkheidsverklaring.nl', 'https://www.mensenrechten.nl']);
    const unnamed =
      locale === 'nl'
        ? 'Laat het de organisatie weten die deze installatie beheert.'
        : 'Let the organisation that runs this installation know.';
    expect(wrapper.text()).toContain(unnamed);
    wrapper.unmount();
  });
});
