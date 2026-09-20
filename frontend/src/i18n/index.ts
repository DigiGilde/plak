/**
 * The interface language: one small module rather than a library.
 *
 * What a library would add here is message syntax, lazy loading and
 * pluralisation. This app hand-rolls its few plurals already ("1 site" against
 * "3 sites"), formats dates and numbers through `Intl`, and ships four runtime
 * dependencies on purpose. What it would cost is a key lookup that fails at
 * runtime instead of at build time. The catalogues are typed, so `vue-tsc`
 * refuses a missing key and a language that is not complete.
 *
 * Where the choice comes from, narrowest first:
 *   1. what this member set in their profile, which lives on their account
 *      (`GET /me` -> `language`) and so travels to every device they use
 *   2. what the browser asks for, `nl` or `en`
 *   3. English, the fallback for a browser that asks for neither
 *
 * Step 3 is English rather than Dutch on purpose. A browser that asks for
 * French has said it does not read Dutch, and a browser that asks for nothing
 * has said nothing at all; English is the wider of the two languages this
 * interface has. Whoever wants Dutch either asks for it or sets it once.
 *
 * The member's choice is also cached in this browser, purely so a reload
 * paints in the right language instead of flashing the browser's language
 * until `/me` comes back. The account stays the authority: every `/me`
 * overwrites the cache, in both directions.
 */
import { computed, ref, watchEffect } from 'vue';

import { type Catalogue, type MessageKey, nl } from './nl';
import { en } from './en';

export type Locale = 'nl' | 'en';

/** A member's own choice, or null when they leave it to their browser. */
export type LanguageChoice = Locale | null;

export const LOCALES: Locale[] = ['nl', 'en'];

/** What a browser that asks for neither of our languages gets. */
export const FALLBACK_LOCALE: Locale = 'en';

const CATALOGUES: Record<Locale, Catalogue> = { nl, en };

const STORAGE_KEY = 'plak-taal';

/**
 * The BCP 47 tag `Intl` gets per language. `en-GB` rather than plain `en`:
 * bare `en` resolves to the American conventions, which would print 9/12/2026
 * and 2:30 PM beside a Dutch original that says 12-9-2026 and 14:30. The
 * service is European, so the European English is the one that matches.
 */
const INTL_LOCALES: Record<Locale, string> = { nl: 'nl-NL', en: 'en-GB' };

export function isLocale(value: string | null | undefined): value is Locale {
  return value === 'nl' || value === 'en';
}

function cached(): LanguageChoice {
  try {
    const value = window.localStorage.getItem(STORAGE_KEY);
    return isLocale(value) ? value : null;
  } catch {
    // A private window, or storage the browser blocks. Not knowing the
    // preference is survivable; failing to render is not.
    return null;
  }
}

function remember(next: LanguageChoice): void {
  try {
    if (next === null) window.localStorage.removeItem(STORAGE_KEY);
    else window.localStorage.setItem(STORAGE_KEY, next);
  } catch {
    // See cached(): the choice then holds for this page only, and the account
    // still carries it to the next load.
  }
}

/** The language the browser asks for, or null when it asks for neither. */
function fromBrowser(): Locale | null {
  const tags = navigator.languages?.length ? navigator.languages : [navigator.language];
  for (const tag of tags) {
    const base = tag?.split('-')[0];
    if (isLocale(base)) return base;
  }
  return null;
}

/**
 * A ref rather than a constant, although a browser never changes its mind
 * mid-session: it is what lets a test stand in for a browser that asks for
 * another language without reloading the module.
 */
const browserLocale = ref<Locale>(fromBrowser() ?? FALLBACK_LOCALE);

const choice = ref<LanguageChoice>(cached());

/** The language the interface is in right now. */
export const currentLocale = computed<Locale>(() => choice.value ?? browserLocale.value);

/** What this member chose, or null while they follow their browser. */
export const languageChoice = computed<LanguageChoice>(() => choice.value);

/** The language the browser asks for; what "follow my browser" resolves to. */
export function browserPreference(): Locale {
  return browserLocale.value;
}

/** The tag `Intl` formatters take for the language on screen. */
export const intlLocale = computed<string>(() => INTL_LOCALES[currentLocale.value]);

// A screen reader picks its phonetics from `lang`; English text announced with
// Dutch sounds is unintelligible (WCAG 3.1.1). Keeping this in the module that
// owns the language means it can never fall behind a change of choice.
// index.html ships lang="en", the same fallback as above, so the shell before
// the first paint says what an unasked question resolves to.
// Synchronous: the attribute is not a render, and a screen reader that is
// already reading should not get one tick of the wrong phonetics.
watchEffect(
  () => {
    document.documentElement.lang = currentLocale.value;
  },
  { flush: 'sync' },
);

/**
 * Record the language choice. `null` hands the choice back to the browser.
 * Sending it to the account is the caller's job (`setMyLanguage`), so that a
 * failing request does not leave the screen in a language the account denies.
 */
export function setLocale(next: LanguageChoice): void {
  choice.value = next;
  remember(next);
}

/**
 * The choice as `/me` reports it. An unknown value from the wire counts as no
 * choice: a language this build has no catalogue for cannot be rendered, and
 * falling back beats a screen full of raw keys.
 */
export function applyMemberLanguage(value: string | null | undefined): void {
  setLocale(isLocale(value) ? value : null);
}

/**
 * One message. `params` fills `{name}` placeholders, so a sentence keeps its
 * word order per language instead of being glued together from fragments.
 */
export function t(key: MessageKey, params?: Record<string, string | number>): string {
  const message = CATALOGUES[currentLocale.value][key];
  if (params === undefined) return message;
  return message.replace(/\{(\w+)\}/g, (whole, name: string) =>
    name in params ? String(params[name]) : whole,
  );
}

/**
 * The sentence for a machine-readable error code, or null when this catalogue
 * does not know it. The backend's own `detail` is the fallback: the API
 * answers in the language this SPA asks for (`Accept-Language`), so an
 * untranslated code still yields a sentence in the right language.
 */
export function messageForCode(code: string | undefined): string | null {
  if (code === undefined) return null;
  const key = `error.code.${code}` as MessageKey;
  return key in nl ? t(key) : null;
}

/**
 * For tests: render in this language, whatever the resolution would say.
 * It stands in for the browser as well as clearing any member choice, so a
 * `/me` without a language of its own lands here rather than on the fallback.
 */
export function _setLocaleForTest(next: Locale): void {
  choice.value = null;
  browserLocale.value = next;
}

export type { MessageKey };
