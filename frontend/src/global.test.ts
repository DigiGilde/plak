import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

// Vitest stubs CSS imports, so read from disk. The path is anchored to
// the project root, where vitest runs from.
const css = readFileSync(resolve(process.cwd(), 'src/global.css'), 'utf8');

describe('Global style', () => {
  it('lets a long word in a heading break', () => {
    // An unbreakable word pushes the page at 320px into a horizontal scroll.
    expect(css).toContain('hyphens: auto');
    expect(css).toContain('overflow-wrap: break-word');
  });
});
