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
 * The catalogue itself is exempt: Dutch is what it is for. The comments in a
 * code snippet are not, see the last describe.
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
  // points at, and class names: one lowercase word chain, no spaces. A test
  // and a stylesheet address those, so they are not text.
  /^[a-z0-9-]+$/,
  // The same, built in a template literal: `${prefix}-link-without-code`.
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
    // `</style >` and `</style\n>` are valid end tags; a naive `<\/style>`
    // leaves the block in and the CSS then gets scanned for Dutch words.
    // Same shape as build-output.test.ts, where it guards the CSP instead.
    .replace(/<style\b[^>]*>[\s\S]*?<\/style(?:\s[^>]*)?>/g, '')
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

  // A literal may hold escapes (`\${{` in a workflow snippet, `\n` in a joined
  // one), which must not end the match: the backticks after it would be
  // paired the wrong way round and the snippets in between would go unread.
  for (const match of withoutBindings.matchAll(
    /'((?:[^'\\\n]|\\.)*)'|"((?:[^"\\\n]|\\.)*)"|`((?:[^`\\]|\\[\s\S])*)`/g,
  )) {
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

/**
 * Words that give a Dutch comment away. A comment is a few words of free
 * text, so the list takes Dutch words that DUTCH_WORDS leaves out for visible
 * text, among them the verbs a snippet comment is likely to use. "is", "in",
 * "of", "en" and "met" stay out: they are English, or a language code, as well.
 * A hyphen belongs to the word, so "de-duplicate" is not "de".
 */
const DUTCH_COMMENT_WORDS = [
  ...DUTCH_WORDS,
  'bouwen',
  'daarna',
  'de',
  'eerst',
  'eigen',
  'inloggen',
  'laat',
  'op',
  'opruimen',
  'publiceer',
  'publiceren',
  'te',
  'tijdelijk',
  'tijdelijke',
  'uitloggen',
  'verwijder',
  'verwijderen',
  'zien',
];
const DUTCH_COMMENT = new RegExp(`(^|[^a-zA-Z-])(${DUTCH_COMMENT_WORDS.join('|')})([^a-zA-Z-]|$)`, 'i');

/**
 * The comments in a piece of code: a `#` followed by a space, at the start of
 * a line or after a space, up to the end of the line. A `#` inside a word, as
 * in `@beta#subdirectory=cli`, or one without a space, as in `#fff`, is not.
 * An escaped `\n` in a string ends a line too.
 */
function snippetComments(text: string): string[] {
  return text.replace(/\\n/g, '\n').split('\n').flatMap((line) => {
    const comment = /(?:^|\s)#\s+(\S.*)$/.exec(line)?.[1];
    return comment === undefined ? [] : [comment.trim()];
  });
}

/**
 * A comment in a snippet the interface shows (a workflow file, a command
 * line) is code, so it is English in both interface languages, whatever the
 * language around it. The check above looks for Dutch in visible text and
 * passes a comment by on a single missed word. This one reads the comments of
 * a snippet on their own and holds them to the longer list. The catalogues are
 * not exempt here, because Dutch is what they are for but not what a snippet
 * in them is written in.
 */
describe('No Dutch comment in a code snippet', () => {
  const files = sourceFiles(SRC);

  function dutchComments(source: string): string[] {
    return visibleParts(source)
      .flatMap(snippetComments)
      .filter((comment) => DUTCH_COMMENT.test(comment));
  }

  it('reaches the workflow and command snippets of the deploy tab', () => {
    // A check that silently reads nothing is worse than no check.
    const parts = visibleParts(readFileSync(join(SRC, 'components/site/TabDeploy.vue'), 'utf8'));
    for (const marker of ['runs-on: ubuntu-latest', 'runs-on: docker', 'toon publish ./dist']) {
      expect(parts.filter((part) => part.includes(marker)).length, marker).toBeGreaterThan(0);
    }
    expect(parts.flatMap(snippetComments).length).toBeGreaterThan(0);
  });

  it('reads the catalogues too', () => {
    expect(files.map((file) => relative(SRC, file))).toContain('i18n/nl.ts');
  });

  it('catches a Dutch comment in a template literal with escapes', () => {
    const source = [
      'const forgejo = `jobs:',
      '    runs-on: docker # de runner-label van deze Forgejo-instantie',
      '    preview-ref: pr-\\${{ github.event.pull_request.number }}`;',
      'const cli = `toon login',
      '',
      '# Preview: eigen pad, laat de live site ongemoeid',
      'toon publish ./dist`;',
    ].join('\n');

    expect(dutchComments(source)).toEqual([
      'de runner-label van deze Forgejo-instantie',
      'Preview: eigen pad, laat de live site ongemoeid',
    ]);
  });

  it('catches one in a string with an escaped newline, and one with a verb a snippet would use', () => {
    const source = [
      "const lines = ['# Preview: eigen pad, laat de live site ongemoeid\\ntoon publish ./dist'];",
      "const more = '# Eerst bouwen en daarna publiceren';",
    ].join('\n');

    expect(dutchComments(source)).toEqual([
      'Preview: eigen pad, laat de live site ongemoeid',
      'Eerst bouwen en daarna publiceren',
    ]);
  });

  it('leaves English comments, a # inside a word and a colour alone', () => {
    const source = [
      'const a = `runs-on: docker # the runner label of this Forgejo instance`;',
      "const b = 'git+https://github.com/DigiGilde/toon@beta#subdirectory=cli';",
      "const c = 'color: #fff';",
      'const d = `# de-duplicate the entries before they go out`;',
    ].join('\n');

    expect(dutchComments(source)).toEqual([]);
  });

  it('finds none in the source', () => {
    const offending = files.flatMap((file) =>
      dutchComments(readFileSync(file, 'utf8')).map((comment) => `${relative(SRC, file)}: # ${comment}`),
    );

    expect(offending, offending.join('\n')).toEqual([]);
  });
});
