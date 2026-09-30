/**
 * The design system's own words, held against the package.
 *
 * `src/i18n/designSystem.ts` answers the translation keys of the components
 * this app uses, so an English interface is English down to the aria-labels.
 * That claim ages badly by itself: a new component gets used, an upgrade adds
 * a key, a default is reworded, an override is hung on the element next to the
 * one that renders the text. So rather than trust the sweep that produced the
 * table, this reads the package on every run and asks:
 *
 *   1. Is every key that can reach a screen here answered? UNREACHED is where
 *      a key is excused, with the reason; an entry there is a claim a reviewer
 *      may contradict.
 *   2. Does every key we answer still exist, on an element of its own
 *      component that this app really uses?
 *   3. Is our Dutch still the package's Dutch, word for word, so the Dutch
 *      interface says what it always said?
 *
 * The English half is covered elsewhere: `src/i18n/index.test.ts` keeps the
 * two catalogues equally complete, so a key without an English string cannot
 * reach a build.
 */
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { beforeAll, describe, expect, it } from 'vitest';

import { _setLocaleForTest } from '../src/i18n';
import { wordsPerElement } from '../src/i18n/designSystem';

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC = resolve(HERE, '../src');
const PACKAGE = resolve(HERE, '../node_modules/@nldd/design-system/dist/components');

/**
 * Keys of a component we do use whose Dutch cannot reach anyone here, with the
 * reason. Everything else has to be answered.
 */
const UNREACHED: Record<string, string> = {
  'components.button.opens-in-new-tab-label': 'no nldd-button here is a link to a new tab',
  'components.icon-button.opens-in-new-tab-label':
    'no nldd-icon-button here is a link to a new tab',
  'components.list-item.opens-in-new-tab-label': 'no nldd-list-item here is a link',
  'components.file-field.to-choose-files-action': 'no file field here takes more than one file',
  'components.file-field.file-count-text': 'no file field here takes more than one file',
  'components.menu.empty-text':
    'every menu here sets empty-text or is never empty, the toolbar overflow menu included',
  'components.menu.back-action': 'no nldd-menu-item here has a submenu',
  'components.menu.submenu-title': 'no nldd-menu-item here has a submenu',
  'components.menu.submenu-back-action': 'no nldd-menu-item here has a submenu',
  'components.list.items-accessible-label': 'every list here sets accessible-label',
  'components.list.navigation-accessible-label': 'no list here is type="navigation"',
  'components.list.search-placeholder-label': 'no list here is type="listbox"',
  'components.list.search-clear-action': 'no list here is type="listbox"',
  'components.list.reorder-moved-text': 'no list here is reorderable',
  'components.list.reorder-dropped-text': 'no list here is reorderable',
  'components.list.reorder-no-change-text': 'no list here is reorderable',
  'components.list.reorder-canceled-text': 'no list here is reorderable',
  'components.table.accessible-label': 'every table here sets accessible-label',
  'components.badge.notification-label': 'every badge here sets text or accessible-label',
  'components.rich-text.table-scroll-label': 'no rich text here holds a table',
  'components.skip-link.action': 'the one skip link sets its own text',
  'components.toolbar.opens-in-new-tab-label':
    'the toolbar title links to the overview in this tab',
};

function filesUnder(directory: string, keep: (name: string) => boolean): string[] {
  const found: string[] = [];
  for (const entry of readdirSync(directory)) {
    const full = join(directory, entry);
    if (statSync(full).isDirectory()) found.push(...filesUnder(full, keep));
    else if (keep(entry)) found.push(full);
  }
  return found;
}

/**
 * Every key the package can say, with its Dutch and the elements that may
 * render it: the ones defined by the modules beside its `.i18n.js`. Read as
 * text, because the package's export map does not reach the i18n modules.
 *
 * The elements matter. A key is named after a component but rendered by
 * whichever element holds the markup, and those are not always the same one:
 * `components.page-footer.legal-bar-accessible-label` is written by
 * `nldd-page-footer-legal-bar`, so handing it to `nldd-page-footer` does
 * nothing at all and nothing on the page says so.
 *
 * A module is as fine as this gets, and it defines more than one element:
 * this catches a key handed to an element of another component, not one
 * handed to the element beside the right one. What settles that is reading the
 * label on a running page.
 */
interface PackageKey {
  dutch: string;
  tags: Set<string>;
}

function packageKeys(): Map<string, PackageKey> {
  const found = new Map<string, PackageKey>();
  for (const file of filesUnder(PACKAGE, (name) => name.endsWith('.i18n.js'))) {
    const directory = dirname(file);
    const tags = new Set<string>();
    for (const sibling of readdirSync(directory)) {
      if (!sibling.endsWith('.js') || sibling.endsWith('.i18n.js')) continue;
      for (const [, tag] of readFileSync(join(directory, sibling), 'utf8').matchAll(
        /customElements?(?:\.define)?\('(nldd-[a-z-]+)'/g,
      )) {
        tags.add(tag);
      }
    }
    for (const [, key, value] of readFileSync(file, 'utf8').matchAll(
      /'(components\.[^']+)':\s*'((?:[^'\\]|\\.)*)'/g,
    )) {
      found.set(key, { dutch: value.replace(/\\'/g, "'"), tags });
    }
  }
  return found;
}

/** Every `nldd-*` tag that appears in a template or a composable. */
function tagsInUse(): Set<string> {
  const tags = new Set<string>();
  const keep = (name: string): boolean => /\.(vue|ts)$/.test(name) && !name.endsWith('.test.ts');
  for (const file of filesUnder(SRC, keep)) {
    for (const [, tag] of readFileSync(file, 'utf8').matchAll(/<(nldd-[a-z-]+)[\s>]/g)) {
      tags.add(tag);
    }
  }
  return tags;
}

describe('the words the design system says by itself', () => {
  const keys = packageKeys();
  const used = tagsInUse();
  // The table renders in the language on screen, so read it in Dutch: that is
  // the half that has to match the package.
  beforeAll(() => _setLocaleForTest('nl'));
  const answered = (): [string, string, string][] =>
    Object.entries(wordsPerElement()).flatMap(([tag, words]) =>
      Object.entries(words).map(([key, dutch]): [string, string, string] => [tag, key, dutch]),
    );

  it('answers every key that can reach a screen here, or says why not', () => {
    const answeredKeys = new Set(answered().map(([, key]) => key));
    const missing: string[] = [];
    for (const [key, { tags }] of keys) {
      if (![...tags].some((tag) => used.has(tag))) continue;
      if (answeredKeys.has(key) || UNREACHED[key] !== undefined) continue;
      missing.push(key);
    }
    expect(missing).toEqual([]);
  });

  it('answers no key the package no longer has', () => {
    const stale = [
      ...answered().map(([, key]) => key),
      ...Object.keys(UNREACHED),
    ].filter((key) => !keys.has(key));
    expect(stale).toEqual([]);
  });

  it('hands every key to an element of its own component, and one this app uses', () => {
    const misplaced = answered()
      .filter(([tag, key]) => keys.get(key)?.tags.has(tag) !== true || !used.has(tag))
      .map(([tag, key]) => `${key} on ${tag}`);
    expect(misplaced).toEqual([]);
  });

  it('excuses no key it also answers', () => {
    const both = answered()
      .map(([, key]) => key)
      .filter((key) => UNREACHED[key] !== undefined);
    expect(both).toEqual([]);
  });

  it('says the same Dutch the package says', () => {
    const changed = answered()
      .filter(([, key, dutch]) => dutch !== keys.get(key)?.dutch)
      .map(([, key, dutch]) => `${key}: "${dutch}" against "${keys.get(key)?.dutch}"`);
    expect(changed).toEqual([]);
  });
});
