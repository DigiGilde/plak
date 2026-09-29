import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  _setLocaleForTest,
  applyMemberLanguage,
  currentLocale,
  setLocale,
  t,
} from './index';
import { en } from './en';
import { nl } from './nl';

/**
 * The browser is read once, when the module loads, so a fresh module with a
 * stubbed navigator is the only way to see that step happen.
 */
async function localeForBrowser(languages: string[]): Promise<string> {
  vi.resetModules();
  vi.stubGlobal('navigator', { languages, language: languages[0] ?? '' });
  window.localStorage.removeItem('plak-taal');
  const reloaded = await import('./index');
  return reloaded.currentLocale.value;
}

afterEach(() => {
  vi.unstubAllGlobals();
  window.localStorage.removeItem('plak-taal');
  _setLocaleForTest('nl');
});

describe('Interface language', () => {
  it('sets the language on the html element, and follows every change', () => {
    // Without this a screen reader pronounces English text with Dutch sounds
    // (WCAG 3.1.1). index.html ships lang="en", the fallback; the module
    // overwrites it with the resolved language before anything is painted.
    expect(document.documentElement.lang).toBe(currentLocale.value);

    setLocale('en');
    expect(document.documentElement.lang).toBe('en');

    setLocale('nl');
    expect(document.documentElement.lang).toBe('nl');
  });

  it('keeps both catalogs equally complete', () => {
    expect(Object.keys(en).sort()).toEqual(Object.keys(nl).sort());
  });

  it('fills placeholders with the given values', () => {
    setLocale('nl');
    expect(t('access.label.only', { extras: 'geheime links' })).toBe('Alleen geheime links');
    expect(t('error.expired.action')).toBe(nl['error.expired.action']);
  });

  it('leaves a placeholder alone when nothing was given for it', () => {
    setLocale('nl');
    expect(t('access.label.only', {})).toBe('Alleen {extras}');
  });

  it('gives English to whoever asks for nothing', async () => {
    // No signal is not a request for Dutch. English is the wider of the two
    // languages this interface has, so it is the safer guess.
    expect(await localeForBrowser([])).toBe('en');
  });

  it('gives English to whoever asks for a language we do not have', async () => {
    expect(await localeForBrowser(['fr-FR', 'fr'])).toBe('en');
    expect(await localeForBrowser(['de-DE', 'de', 'pl'])).toBe('en');
  });

  it('picks the language we do have, wherever it sits in the list', async () => {
    expect(await localeForBrowser(['fr-FR', 'fr', 'nl'])).toBe('nl');
    expect(await localeForBrowser(['nl-NL', 'nl', 'en'])).toBe('nl');
    expect(await localeForBrowser(['en-GB', 'en'])).toBe('en');
  });

  it('lets the account overrule the browser, in both directions', () => {
    _setLocaleForTest('nl');

    applyMemberLanguage('en');
    expect(currentLocale.value).toBe('en');

    applyMemberLanguage('nl');
    expect(currentLocale.value).toBe('nl');
  });

  it('falls back to the browser when the account holds no choice', () => {
    _setLocaleForTest('nl');
    applyMemberLanguage('en');

    applyMemberLanguage(null);
    expect(currentLocale.value).toBe('nl');
  });

  it('ignores a language from the wire that this build cannot render', () => {
    // A newer backend could know a language this bundle has no catalogue for;
    // falling back beats a screen full of raw keys.
    _setLocaleForTest('nl');
    applyMemberLanguage('fr');
    expect(currentLocale.value).toBe('nl');
  });

  it('remembers the account choice in this browser, so a reload does not flash', async () => {
    vi.resetModules();
    vi.stubGlobal('navigator', { languages: ['nl-NL', 'nl'], language: 'nl-NL' });
    window.localStorage.setItem('plak-taal', 'en');
    const reloaded = await import('./index');
    expect(reloaded.currentLocale.value).toBe('en');
  });

  it('forgets the cache again once the choice goes back to the browser', () => {
    setLocale('en');
    expect(window.localStorage.getItem('plak-taal')).toBe('en');

    setLocale(null);
    expect(window.localStorage.getItem('plak-taal')).toBeNull();
  });

  it('renders without storage at all', async () => {
    // A private window, or site data the browser blocks.
    vi.resetModules();
    vi.stubGlobal('navigator', { languages: ['nl'], language: 'nl' });
    vi.stubGlobal('localStorage', {
      getItem: () => {
        throw new Error('blocked');
      },
      setItem: () => {
        throw new Error('blocked');
      },
      removeItem: () => {
        throw new Error('blocked');
      },
    });
    const reloaded = await import('./index');
    expect(reloaded.currentLocale.value).toBe('nl');
    expect(() => reloaded.setLocale('en')).not.toThrow();
  });
});
