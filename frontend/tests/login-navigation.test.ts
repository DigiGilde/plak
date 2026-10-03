/**
 * No script navigation to the login route. A login counts as fresh enough to
 * approve `plak login` when it was started from the admin origin, and the
 * backend can only see that as `Sec-Fetch-Site: same-origin`: a script on a
 * admin page that sends the browser to `/-/login` by itself looks exactly
 * like a member who clicked. A link from elsewhere that lands on such a page
 * would then walk the browser through a silent SSO round trip into a fresh
 * session (see docs/security.md, CLI device flow). So the SPA only ever
 * offers the login as a link the member follows themselves.
 *
 * The check is a heuristic, per file: a file that names the login route may
 * not also contain a navigation sink (location assignment, location.assign or
 * replace, window.open, a form submit, a router push or replace). That is
 * broader than one statement on purpose, so the URL built in a variable and
 * navigated to a few lines later is caught too. A login URL imported from
 * another module and navigated to there is not; a reviewer has to catch that.
 * A plain `href` in a template (ErrorBanner.vue, Landing.vue) is no sink.
 */
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

const SRC = resolve(dirname(fileURLToPath(import.meta.url)), '../src');

const LOGIN_ROUTE = /\/-\/login\b/;

const SINKS: ReadonlyArray<readonly [string, RegExp]> = [
  ['location assignment', /\blocation(?:\s*\.\s*href)?\s*=(?!=)/],
  ['location.assign or location.replace', /\blocation\s*\.\s*(?:assign|replace)\s*\(/],
  ['window.open', /\bwindow\s*\.\s*open\s*\(/],
  ['form submit', /\.\s*(?:submit|requestSubmit)\s*\(/],
  ['router push or replace', /\brouter\s*\.\s*(?:push|replace)\s*\(/],
];

function sourceFiles(directory: string): string[] {
  const found: string[] = [];
  for (const entry of readdirSync(directory)) {
    const full = join(directory, entry);
    if (statSync(full).isDirectory()) {
      found.push(...sourceFiles(full));
      continue;
    }
    if (!/\.(vue|ts)$/.test(entry) || entry.endsWith('.test.ts') || entry.endsWith('.d.ts')) {
      continue;
    }
    found.push(full);
  }
  return found;
}

/** The source without script comments, so prose about the login is no
 * finding. HTML comments stay in: stripping them is what CodeQL reads as
 * incomplete sanitisation, and keeping them only makes the guard stricter. */
function withoutComments(source: string): string {
  return source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/[^\n]*/g, '$1');
}

/** The sinks in a file that also names the login route; empty when it is fine. */
function scriptNavigationsToLogin(source: string): string[] {
  const code = withoutComments(source);
  if (!LOGIN_ROUTE.test(code)) return [];
  return SINKS.filter(([, pattern]) => pattern.test(code)).map(([name]) => name);
}

describe('the detector', () => {
  it('flags the CliLink.vue code that let a link re-arm a fresh session', () => {
    // Verbatim from CliLink.vue before the fix.
    const before = `
function redirectToLogin(rawCode: string | null): void {
  const query = rawCode ? \`?code=\${encodeURIComponent(rawCode)}\` : '';
  window.location.href = \`/-/login?returnTo=\${encodeURIComponent(\`/cli-link\${query}\`)}\`;
}`;
    expect(scriptNavigationsToLogin(before)).toEqual(['location assignment']);
  });

  it.each([
    ['location = url', "const url = '/-/login'; window.location = url;", 'location assignment'],
    ['document.location.href', "document.location.href = '/-/login';", 'location assignment'],
    ['location.assign', "location.assign('/-/login?returnTo=/');", 'location.assign or location.replace'],
    ['location.replace', "window.location.replace('/-/login');", 'location.assign or location.replace'],
    ['window.open', "window.open('/-/login', '_self');", 'window.open'],
    ['form submit', '<form ref="f" action="/-/login"></form> f.value?.submit();', 'form submit'],
    ['form requestSubmit', '<form ref="f" action="/-/login"></form> f.value?.requestSubmit();', 'form submit'],
    ['router push', "void router.push('/-/login');", 'router push or replace'],
    ['router replace', "void router.replace({ path: '/-/login' });", 'router push or replace'],
  ])('flags %s', (_name, source, sink) => {
    expect(scriptNavigationsToLogin(source)).toEqual([sink]);
  });

  it.each([
    // ErrorBanner.vue: the href is built in script, followed by a click.
    ['a computed href', 'const loginHref = computed(() => `/-/login?returnTo=${encodeURIComponent(window.location.pathname)}`);'],
    // Landing.vue: a static href in the template.
    ['a template href', '<nldd-button href="/-/login" variant="primary"></nldd-button>'],
    ['a sink without the login route', "window.open(viewUrl(version), '_blank', 'noopener');"],
    ['a comparison, not an assignment', "if (window.location.href === '/-/login') {}"],
    ['the login route in a comment only', "// window.location.href = '/-/login'\nconst a = 1;"],
  ])('does not flag %s', (_name, source) => {
    expect(scriptNavigationsToLogin(source)).toEqual([]);
  });
});

describe('No script navigation to the login route', () => {
  const files = sourceFiles(SRC);
  const names = files.map((file) => relative(SRC, file));

  it('scans the files that offer the login', () => {
    // A check that silently scans nothing is worse than no check.
    expect(files.length).toBeGreaterThan(30);
    expect(names).toEqual(
      expect.arrayContaining(['pages/CliLink.vue', 'components/ErrorBanner.vue', 'pages/Landing.vue']),
    );
  });

  it.each(files.map((file) => [relative(SRC, file), file]))('%s', (_name, file) => {
    expect(scriptNavigationsToLogin(readFileSync(file, 'utf8'))).toEqual([]);
  });
});
