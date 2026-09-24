import { readdirSync, readFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

const hereDir = dirname(fileURLToPath(import.meta.url));
const distDir = resolve(hereDir, '../dist');
const distIndexPath = join(distDir, 'index.html');

function builtIndex(): string {
  try {
    return readFileSync(distIndexPath, 'utf-8');
  } catch {
    throw new Error(
      `dist/index.html niet gevonden op ${distIndexPath}. Draai eerst 'npm run build'.`,
    );
  }
}

function distFiles(directory = distDir): string[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const full = join(directory, entry.name);
    return entry.isDirectory() ? distFiles(full) : [full];
  });
}

/**
 * Guards the CSP precondition from spec 9: script-src 'self'; style-src 'self',
 * both without unsafe-inline. Run it after `npm run build`; without dist/
 * index.html the test fails with a clear message rather than passing silently.
 */
describe('gebouwde index.html', () => {
  it('bevat geen inline <script> zonder src', () => {
    const html = builtIndex();

    const scriptTags = [...html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi)];
    expect(scriptTags.length).toBeGreaterThan(0);

    for (const [tag, content] of scriptTags) {
      const hasSrc = /\bsrc\s*=/.test(tag);
      const isEmpty = content.trim().length === 0;
      expect(hasSrc || isEmpty, `inline script zonder src gevonden: ${tag}`).toBe(true);
    }
  });

  it('bevat geen inline <style> of style-attribuut', () => {
    const html = builtIndex();

    const styleTags = [...html.matchAll(/<style\b[^>]*>([\s\S]*?)<\/style>/gi)];
    for (const [tag] of styleTags) {
      expect.unreachable(`inline <style> gevonden: ${tag}`);
    }

    const styleAttributes = [...html.matchAll(/\sstyle\s*=\s*["']/gi)];
    expect(styleAttributes.length, 'style-attribuut gevonden in dist/index.html').toBe(0);
  });
});

describe('gebouwde bestanden', () => {
  it('bevat geen sourcemaps', () => {
    const maps = distFiles().filter((file) => file.endsWith('.map'));

    expect(maps, `sourcemaps meegeleverd: ${maps.join(', ')}`).toEqual([]);
  });

  it('roept nergens eval of new Function aan', () => {
    const chunks = distFiles().filter((file) => file.endsWith('.js'));
    expect(chunks.length).toBeGreaterThan(0);

    for (const chunk of chunks) {
      const code = readFileSync(chunk, 'utf-8');
      expect(/\beval\s*\(/.test(code), `eval( gevonden in ${chunk}`).toBe(false);
      expect(/\bnew\s+Function\s*\(/.test(code), `new Function( gevonden in ${chunk}`).toBe(false);
    }
  });
});
