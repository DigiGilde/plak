/**
 * The privacy and accessibility statements are legal texts: what matters is
 * that every contact route and authority is there as a working link, in both
 * languages, and that the statement says what Plak does not do (no
 * organisation is stored, the statements are drafts).
 */
import { flushPromises, mount } from '@vue/test-utils';
import { afterEach, describe, expect, it } from 'vitest';
import { createMemoryHistory, createRouter } from 'vue-router';

import { _setLocaleForTest } from '../src/i18n';
import Accessibility from '../src/pages/Accessibility.vue';
import Privacy from '../src/pages/Privacy.vue';

afterEach(() => {
  _setLocaleForTest('nl');
});

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

describe('privacy statement', () => {
  it.each(['nl', 'en'] as const)('names every route and authority in %s', async (locale) => {
    const wrapper = await render(Privacy, locale);

    expect(hrefs(wrapper)).toEqual([
      'https://www.rijksoverheid.nl/contact/contactformulier',
      'https://www.avgregisterrijksoverheid.nl',
      'mailto:postbusfg@minbzk.nl',
      'https://www.autoriteitpersoonsgegevens.nl',
      'mailto:digigilde@rijksoverheid.nl',
    ]);
    expect(wrapper.text()).toContain('Postbus 20011, 2500 EA Den Haag');
    wrapper.unmount();
  });

  it('does not claim to store an organisation', async () => {
    const nl = await render(Privacy, 'nl');
    expect(nl.text()).not.toMatch(/organisatie,? via inloggen/);
    nl.unmount();
    const en = await render(Privacy, 'en');
    expect(en.text()).not.toMatch(/and organisation/);
    en.unmount();
  });
});

describe('accessibility statement', () => {
  it.each(['nl', 'en'] as const)('links register, report address and enforcement in %s', async (locale) => {
    const wrapper = await render(Accessibility, locale);

    expect(hrefs(wrapper)).toEqual([
      'https://www.toegankelijkheidsverklaring.nl',
      'mailto:digigilde@rijksoverheid.nl',
      'https://www.mensenrechten.nl',
    ]);
    expect(wrapper.text()).toContain('WCAG 2.1');
    expect(wrapper.text()).toContain('2026');
    wrapper.unmount();
  });
});
