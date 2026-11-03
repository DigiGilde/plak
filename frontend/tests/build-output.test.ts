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
      `dist/index.html not found at ${distIndexPath}. Run 'npm run build' first.`,
    );
  }
}

// An end tag may carry whitespace and even ignored attributes before the `>`,
// so `</script >` and `</script foo>` close a script just like `</script>`. A
// pattern that misses those forms reads straight past the real end tag: an
// inline script then counts as the content of an earlier tag that does have a
// src, and an inline <style> that ends that way is not seen at all. Both
// guards below would let through exactly what they are there to catch.
const SCRIPT_TAG_RE = /<script\b[^>]*>([\s\S]*?)<\/script(?:\s[^>]*)?>/gi;
const STYLE_TAG_RE = /<style\b[^>]*>([\s\S]*?)<\/style(?:\s[^>]*)?>/gi;

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
describe('built index.html', () => {
  it('has no inline <script> without src', () => {
    const html = builtIndex();

    const scriptTags = [...html.matchAll(SCRIPT_TAG_RE)];
    expect(scriptTags.length).toBeGreaterThan(0);

    for (const [tag, content] of scriptTags) {
      const hasSrc = /\bsrc\s*=/.test(tag);
      const isEmpty = content.trim().length === 0;
      expect(hasSrc || isEmpty, `inline script without src found: ${tag}`).toBe(true);
    }
  });

  it('has no inline <style> or style attribute', () => {
    const html = builtIndex();

    const styleTags = [...html.matchAll(STYLE_TAG_RE)];
    for (const [tag] of styleTags) {
      expect.unreachable(`inline <style> found: ${tag}`);
    }

    const styleAttributes = [...html.matchAll(/\sstyle\s*=\s*["']/gi)];
    expect(styleAttributes.length, 'style attribute found in dist/index.html').toBe(0);
  });
});

describe('script tag pattern', () => {
  it.each(['</script >', '</script\t\n foo>'])('sees each script separately after %s', (endTag) => {
    const html = `<script src="a.js">${endTag}<script>alert(1)</script>`;

    const tags = [...html.matchAll(SCRIPT_TAG_RE)];

    expect(tags.length).toBe(2);
    expect(tags[1][1]).toBe('alert(1)');
  });

  it('does not take <scriptx> for an end tag', () => {
    const tags = [...'<script>alert(1)</scriptx></script>'.matchAll(SCRIPT_TAG_RE)];

    expect(tags.length).toBe(1);
    expect(tags[0][1]).toBe('alert(1)</scriptx>');
  });
});

describe('style tag pattern', () => {
  it.each(['</style >', '</style\t\n foo>'])('sees an inline <style> that closes with %s', (endTag) => {
    const tags = [...`<style>body{color:red}${endTag}`.matchAll(STYLE_TAG_RE)];

    expect(tags.length).toBe(1);
    expect(tags[0][1]).toBe('body{color:red}');
  });

  it('does not take <stylex> for an end tag', () => {
    const tags = [...'<style>a</stylex></style>'.matchAll(STYLE_TAG_RE)];

    expect(tags.length).toBe(1);
    expect(tags[0][1]).toBe('a</stylex>');
  });
});

describe('built files', () => {
  it('has no sourcemaps', () => {
    const maps = distFiles().filter((file) => file.endsWith('.map'));

    expect(maps, `sourcemaps shipped: ${maps.join(', ')}`).toEqual([]);
  });

  it('never calls eval or new Function', () => {
    const chunks = distFiles().filter((file) => file.endsWith('.js'));
    expect(chunks.length).toBeGreaterThan(0);

    for (const chunk of chunks) {
      const code = readFileSync(chunk, 'utf-8');
      expect(/\beval\s*\(/.test(code), `eval( found in ${chunk}`).toBe(false);
      expect(/\bnew\s+Function\s*\(/.test(code), `new Function( found in ${chunk}`).toBe(false);
    }
  });
});
