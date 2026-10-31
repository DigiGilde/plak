/**
 * The notes on disk: only released notes, each in both languages. A note on
 * its way to a release waits in `changelog.d/` as a fragment, not here.
 */
import { readdirSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

const DIRECTORY = resolve(dirname(fileURLToPath(import.meta.url)), '../src/content/releases');
const NAME = /^(\d{4}\.\d{1,2}\.\d{1,2}(?:\.\d+)?)\.(nl|en)\.md$/;

const files = readdirSync(DIRECTORY).filter((name) => name.endsWith('.md'));

describe('release notes', () => {
  it('are all named <calver>.<nl|en>.md', () => {
    expect(files.filter((name) => !NAME.test(name))).toEqual([]);
  });

  it('exist in both languages', () => {
    const languages = new Map<string, Set<string>>();
    for (const name of files) {
      const [, version, language] = NAME.exec(name)!;
      languages.set(version!, (languages.get(version!) ?? new Set()).add(language!));
    }

    const incomplete = [...languages].filter(([, set]) => set.size !== 2).map(([version]) => version);
    expect(incomplete).toEqual([]);
  });
});
