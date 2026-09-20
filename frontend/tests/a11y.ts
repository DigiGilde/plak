/**
 * axe-core helper for component tests. `color-contrast` is off: jsdom has no
 * renderer or canvas, so that check cannot give a reliable verdict (and throws
 * a not-implemented-canvas error). Contrast is covered by the design system's
 * own tokens, not per app page.
 */
import axe, { type RunOptions } from 'axe-core';
import { expect } from 'vitest';

const OPTIONS: RunOptions = {
  rules: {
    'color-contrast': { enabled: false },
  },
};

export async function expectNoAxeViolations(element: Element): Promise<void> {
  const result = await axe.run(element, OPTIONS);
  expect(result.violations, JSON.stringify(result.violations, null, 2)).toEqual([]);
}
