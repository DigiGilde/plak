/**
 * The id of an nldd-validation-item is what `unmet` on a control points at,
 * and an id is one per document. Two components that both call an item
 * `group-name-required` work apart and break the moment one page mounts both,
 * so no id is used twice anywhere in the components.
 *
 * Only a literal `id="..."` counts; an id built at runtime (`:id`) cannot be
 * read from the source.
 */
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

const SRC = resolve(dirname(fileURLToPath(import.meta.url)), '../src');

const ITEM = /<nldd-validation-item\b[^>]*?\sid="([^"]+)"/g;

/** The ids that more than one place declares, with the places. */
function reusedIds(sources: Record<string, string>): Record<string, string[]> {
  const places = new Map<string, string[]>();
  for (const [file, source] of Object.entries(sources)) {
    for (const match of source.matchAll(ITEM)) {
      places.set(match[1]!, [...(places.get(match[1]!) ?? []), file]);
    }
  }
  return Object.fromEntries([...places].filter(([, files]) => files.length > 1));
}

function vueFiles(directory: string): string[] {
  return readdirSync(directory).flatMap((entry) => {
    const full = join(directory, entry);
    if (statSync(full).isDirectory()) return vueFiles(full);
    return entry.endsWith('.vue') ? [full] : [];
  });
}

describe('ids of validation items', () => {
  it('notices an id that two components both declare', () => {
    expect(
      reusedIds({
        'A.vue': '<nldd-validation-item id="name-required" required>x</nldd-validation-item>',
        'B.vue': '<nldd-validation-item\n  hint\n  id="name-required"\n>y</nldd-validation-item>',
        'C.vue': '<nldd-validation-item id="other">z</nldd-validation-item>',
      }),
    ).toEqual({ 'name-required': ['A.vue', 'B.vue'] });
  });

  it('are different in every component', () => {
    const sources = Object.fromEntries(
      vueFiles(SRC).map((file) => [relative(SRC, file), readFileSync(file, 'utf8')]),
    );

    // Several items were found at all: a scan that reads nothing proves nothing.
    expect([...Object.values(sources).join('').matchAll(ITEM)].length).toBeGreaterThan(20);
    expect(reusedIds(sources)).toEqual({});
  });
});
