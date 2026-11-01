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

  it('lets a long address wrap inside a dialog, a help text and a validation item', () => {
    // A 63 character slug has no break opportunity, so at 320px it would
    // scroll the page sideways (WCAG 1.4.10).
    expect(css).toMatch(
      /nldd-modal-dialog,\s*nldd-form-field-help-text,\s*nldd-validation-item\s*{[^}]*overflow-wrap:\s*anywhere/,
    );
  });
});
