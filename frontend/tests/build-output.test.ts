import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

const hereDir = dirname(fileURLToPath(import.meta.url));
const distIndexPath = resolve(hereDir, '../dist/index.html');

/**
 * Guards the CSP precondition from spec 9: script-src 'self' without
 * unsafe-inline. Run it after `npm run build`; without dist/index.html the test
 * fails with a clear message rather than passing silently.
 */
describe('gebouwde index.html', () => {
  it('bevat geen inline <script> zonder src', () => {
    let html: string;
    try {
      html = readFileSync(distIndexPath, 'utf-8');
    } catch {
      throw new Error(
        `dist/index.html niet gevonden op ${distIndexPath}. Draai eerst 'npm run build'.`,
      );
    }

    const scriptTags = [...html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi)];
    expect(scriptTags.length).toBeGreaterThan(0);

    for (const [tag, content] of scriptTags) {
      const hasSrc = /\bsrc\s*=/.test(tag);
      const isEmpty = content.trim().length === 0;
      expect(hasSrc || isEmpty, `inline script zonder src gevonden: ${tag}`).toBe(true);
    }
  });
});
