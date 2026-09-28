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

// An end tag may carry whitespace and even ignored attributes before the `>`,
// so `</script >` and `</script foo>` close a script just like `</script>`. A
// pattern that misses those forms swallows a following inline script into the
// content of an earlier one that does have a src, and the guard below then
// waves it through.
const SCRIPT_TAG_RE = /<script\b[^>]*>([\s\S]*?)<\/script(?:\s[^>]*)?>/gi;

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

    const scriptTags = [...html.matchAll(SCRIPT_TAG_RE)];
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

describe('script-tagpatroon', () => {
  it.each(['</script >', '</script\t\n foo>'])('ziet elk script apart na %s', (endTag) => {
    const html = `<script src="a.js">${endTag}<script>alert(1)</script>`;

    const tags = [...html.matchAll(SCRIPT_TAG_RE)];

    expect(tags.length).toBe(2);
    expect(tags[1][1]).toBe('alert(1)');
  });

  it('ziet <scriptx> niet aan voor een eindtag', () => {
    const tags = [...'<script>alert(1)</scriptx></script>'.matchAll(SCRIPT_TAG_RE)];

    expect(tags.length).toBe(1);
    expect(tags[0][1]).toBe('alert(1)</scriptx>');
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
