/**
 * No Dutch in a component. Every word a person reads lives in the catalogue
 * under `src/i18n/`, in both languages; a Dutch sentence left in a `.vue` file
 * or a composable is a sentence an English reader will meet in Dutch.
 *
 * The check is a heuristic, and it says so: it looks for unmistakably Dutch
 * function words (het, niet, een, wordt, ...) inside the parts of a file a
 * person can see, which are the text between tags and the string literals.
 * A heuristic can be wrong in two directions, and both are handled on purpose:
 *
 *   - It can miss a Dutch string with no such word in it ("Opslaan"). Nothing
 *     here can catch that, so the defence is the second one below: every
 *     screen is rendered in English by its own tests and by
 *     tests/language-axe.test.ts, where a leftover Dutch word stands out in an
 *     English sentence, and the catalogue parity test keeps the two languages
 *     equally complete.
 *   - It can fire on something that is not visible text, a CSS value or a
 *     comment. Comments and <style> blocks are stripped; anything else that
 *     trips it belongs in ALLOWED below, with a reason.
 *
 * The catalogue itself is exempt: Dutch is what it is for.
 */
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

const SRC = resolve(dirname(fileURLToPath(import.meta.url)), '../src');

/**
 * Words that cannot be anything but Dutch in a codebase whose identifiers,
 * comments and tests are English. Short ones ("in", "is", "met") are left out:
 * they collide with English and with code.
 */
const DUTCH_WORDS = [
  'aan',
  'alleen',
  'als',
  'bij',
  'dat',
  'deze',
  'die',
  'dit',
  'door',
  'een',
  'geen',
  'het',
  'hier',
  'hoe',
  'iemand',
  'iets',
  'kan',
  'kun',
  'kunt',
  'maar',
  'mag',
  'moet',
  'naar',
  'niet',
  'niets',
  'nog',
  'ook',
  'onder',
  'staat',
  'toch',
  'uit',
  'van',
  'voor',
  'waar',
  'wat',
  'wie',
  'wordt',
  'worden',
  'zijn',
  'zonder',
];

const DUTCH = new RegExp(`(^|[^a-zA-Z])(${DUTCH_WORDS.join('|')})([^a-zA-Z]|$)`, 'i');

/**
 * Files whose Dutch is not interface text. Each line says why; a new entry is
 * a claim a reviewer may contradict.
 */
const EXEMPT_FILES = [
  // The catalogues themselves.
  /^i18n\//,
  // Test doubles for the API: these are the backend's own messages, not the
  // interface's, and the backend renders them per Accept-Language.
  /^api\/mock\.ts$/,
];

/** Literal strings that are not visible text, with their reason. */
const ALLOWED = [
  // Route paths and slugs are addresses, not text: /cli-link, /-/sessions,
  // /-/profile.
  /^\/[a-z0-9/-]*$/,
  // data-testid values, the ids an aria-labelledby or an nldd-validation-item
  // points at, and class names: one lowercase word chain, no spaces. Those
  // keep their Dutch spelling on purpose, because a test and a stylesheet
  // address them.
  /^[a-z0-9-]+$/,
  // The same, built in a template literal: `${prefix}-link-zonder-code`.
  /^[$a-z0-9{}-]+$/,
];

function sourceFiles(directory: string): string[] {
  const found: string[] = [];
  for (const entry of readdirSync(directory)) {
    const full = join(directory, entry);
    if (statSync(full).isDirectory()) {
      found.push(...sourceFiles(full));
      continue;
    }
    if (!/\.(vue|ts)$/.test(entry) || entry.endsWith('.test.ts') || entry.endsWith('.d.ts')) {
      continue;
    }
    found.push(full);
  }
  return found;
}

/** The file without its comments and its <style> block. */
function strippable(source: string): string {
  return source
    .replace(/<style[\s\S]*?<\/style>/g, '')
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/(^|[^:])\/\/[^\n]*/g, '$1');
}

/** Everything a person could read: text between tags, and string literals. */
function visibleParts(source: string): string[] {
  const parts: string[] = [];
  const stripped = strippable(source);

  // The value of a bound attribute (`:text="..."`, `@click="..."`, `v-if`) is
  // an expression, not a string. Its own quotes are dropped and the
  // expression kept, so a literal inside it (`:text="'Onbekende fout'"`) is
  // still scanned while the expression around it no longer counts as text.
  const withoutBindings = stripped.replace(
    /(?:[:@]|v-)[\w.:[\]-]+="([^"]*)"/g,
    (_whole, expression: string) => ` ${expression} `,
  );

  for (const match of withoutBindings.matchAll(/'([^'\\\n]*)'|"([^"\\\n]*)"|`([^`\\]*)`/g)) {
    parts.push(match[1] ?? match[2] ?? match[3] ?? '');
  }

  const template = stripped.match(/<template>([\s\S]*)<\/template>/);
  if (template) {
    for (const match of template[1]!.matchAll(/>([^<>{}]+)</g)) {
      parts.push(match[1]!);
    }
  }
  return parts;
}

describe('No Dutch outside the catalogue', () => {
  const files = sourceFiles(SRC).filter((file) => {
    const name = relative(SRC, file);
    return !EXEMPT_FILES.some((pattern) => pattern.test(name));
  });

  it('finds files to check at all', () => {
    // A check that silently scans nothing is worse than no check.
    expect(files.length).toBeGreaterThan(30);
  });

  it.each(files.map((file) => [relative(SRC, file), file]))('%s', (_name, file) => {
    const offending = visibleParts(readFileSync(file, 'utf8'))
      .map((part) => part.trim())
      .filter((part) => part !== '' && !ALLOWED.some((pattern) => pattern.test(part)))
      .filter((part) => DUTCH.test(part));

    expect(offending, offending.join('\n')).toEqual([]);
  });
});
