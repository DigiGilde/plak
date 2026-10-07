import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

// Vitest stubs CSS imports, so read from disk. The path is anchored to
// the project root, where vitest runs from.
const css = readFileSync(resolve(process.cwd(), 'src/global.css'), 'utf8');

describe('Global style', () => {
  it('lets the status bar grow along with the text size', () => {
    // nldd-status-bar pins 24px against 0.889rem text, so at 200% it
    // clips its own letters (WCAG 1.4.4). Via its own token, not a grab
    // into the shadow root.
    expect(css).toContain('--components-status-bar-height: max(24px, 1.5em)');
  });

  it('lets a long word in a heading break', () => {
    // An unbreakable word pushes the page at 320px into a horizontal scroll.
    expect(css).toContain('hyphens: auto');
    expect(css).toContain('overflow-wrap: break-word');
  });

  it('lets a tab bar scroll sideways in its wrapper instead of clipping its labels', () => {
    // nldd-tab-bar has no overflow strategy (0.8.92): with seven tabs the
    // labels collapse to one letter on a phone (WCAG 1.4.4, 1.4.10).
    expect(css).toMatch(/\.tabs-scroll\s*{[^}]*overflow-x:\s*auto/);
    expect(css).toMatch(/\.tabs-scroll nldd-tab-bar\s*{[^}]*max-width:\s*none/);
    expect(css).toMatch(/\.tabs-scroll nldd-tab-bar-item\s*{[^}]*min-width:\s*max-content/);
  });

  it('keeps the focus ring of a tab inside the scroll box, and the bar in line with the page', () => {
    // A scroll box clips what sticks out of it, so the ring of the first and
    // the last tab were cut off at the sides: padding all round, pulled back
    // out by the margin, and the same room when a tab is scrolled to the edge.
    expect(css).toMatch(/\.tabs-scroll\s*{[^}]*[^-]padding:\s*var\(--primitives-space-4\)/);
    expect(css).toMatch(
      /\.tabs-scroll\s*{[^}]*margin-inline:\s*calc\(-1 \* var\(--primitives-space-4\)\)/,
    );
    expect(css).toMatch(/\.tabs-scroll\s*{[^}]*scroll-padding-inline:\s*var\(--primitives-space-4\)/);
  });

  it('keeps the padding of the tab bar wrapper on a spacing token that exists', () => {
    // A renamed primitive would leave the padding at nothing, and the focus
    // ring of the first tab clipped by the scroll box without a word.
    const variables = readFileSync(
      resolve(process.cwd(), 'node_modules/@nldd/design-system/dist/css/variables.css'),
      'utf8',
    );
    expect(variables).toContain('--primitives-space-4:');
  });

  it('lets a long address wrap inside a dialog, a help text and a validation item', () => {
    // A 63 character slug has no break opportunity, so at 320px it would
    // scroll the page sideways (WCAG 1.4.10).
    expect(css).toMatch(
      /nldd-modal-dialog,\s*nldd-form-field-help-text,\s*nldd-validation-item\s*{[^}]*overflow-wrap:\s*anywhere/,
    );
  });
});
