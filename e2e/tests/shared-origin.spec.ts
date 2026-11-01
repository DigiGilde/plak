/**
 * Every site shares one origin, so a page of a site without the sandbox can
 * ask the browser for another site's files. The access gate and
 * `_foreign_subresource` only stop what reaches the server: whatever the
 * browser stored of a restricted site, it hands out without asking. So the
 * browser must have stored nothing (#152).
 *
 * Its own group, independent of toon.spec.ts.
 */
import { expect, test, type BrowserContext, type Page } from '@playwright/test';
import { adminFetch, uploadViaSession } from '../helpers/api';
import { logInOnAdmin, visitWithLogin } from '../helpers/oidc';
import { ADMIN_EMAIL, ADMIN_SUB, ADMIN_URL, CONTEXT_OPTIONS, CONTENT_URL } from '../helpers/environment';
import { makeTarGz } from '../helpers/tar';

const GROUP = 'e2e-gedeelde-origin';
const API = '/-/api/v1';
const MARKER = 'alleen-voor-het-siteteam';

const restricted = makeTarGz({
  'index.html':
    '<!doctype html><html lang="nl"><head><meta charset="utf-8"><title>Geheim</title>' +
    `<script src="geheim.js"></script></head><body><h1>${MARKER}</h1></body></html>`,
  'geheim.js': `window.geheim = "${MARKER}";`,
});

// Each of geheim's files in each way a page can ask for it: whether the
// answer holds the secret, or what went wrong.
const probe = `
(async () => {
  const results = {};
  for (const path of ['/${GROUP}/geheim/geheim.js', '/${GROUP}/geheim/']) {
    for (const [mode, init] of [
      ['only-if-cached', { cache: 'only-if-cached', mode: 'same-origin' }],
      ['force-cache', { cache: 'force-cache' }],
      ['default', {}],
    ]) {
      try {
        results[path + ' ' + mode] = (await (await fetch(path, init)).text()).includes('${MARKER}');
      } catch (error) {
        results[path + ' ' + mode] = String(error);
      }
    }
  }
  document.getElementById('result').textContent = JSON.stringify(results);
})();
`;

const other = makeTarGz({
  'index.html':
    '<!doctype html><html lang="nl"><head><meta charset="utf-8"><title>Andere site</title>' +
    '<script src="probe.js" defer></script></head><body><pre id="result">bezig</pre></body></html>',
  'probe.js': probe,
});

let adminContext: BrowserContext;
let adminPage: Page;

test.describe.serial('one origin for every site', () => {
  test.beforeAll(async ({ browser }) => {
    adminContext = await browser.newContext(CONTEXT_OPTIONS);
    adminPage = await adminContext.newPage();
  });

  test.afterAll(async () => {
    await adminContext?.close();
  });

  test('another site cannot read a restricted site from the browser cache', async ({ browser }) => {
    await logInOnAdmin(adminPage, ADMIN_URL, ADMIN_SUB, { email: ADMIN_EMAIL });
    expect((await adminFetch(adminPage, 'POST', `${API}/groups`, { name: 'Gedeelde origin', slug: GROUP })).status).toBe(201);
    for (const slug of ['geheim', 'ander']) {
      expect((await adminFetch(adminPage, 'POST', `${API}/groups/${GROUP}/sites`, { title: slug, slug })).status).toBe(201);
    }
    expect((await uploadViaSession(adminPage, GROUP, 'geheim', restricted)).status).toBe(201);
    expect((await uploadViaSession(adminPage, GROUP, 'ander', other)).status).toBe(201);
    // The other site at its most open: public, and without the sandbox, which
    // would give its page an opaque origin that reaches no cache at all.
    expect((await adminFetch(adminPage, 'PUT', `${API}/sites/${GROUP}/ander/access`, { base: 'public' })).status).toBe(200);
    expect((await adminFetch(adminPage, 'PUT', `${API}/sites/${GROUP}/ander/sandbox`, { sandbox: false })).status).toBe(200);

    const visitor = await browser.newContext(CONTEXT_OPTIONS);
    const page = await visitor.newPage();
    await visitWithLogin(page, `${CONTENT_URL}/${GROUP}/geheim/`, ADMIN_SUB, { email: ADMIN_EMAIL });
    await expect(page.locator('h1')).toHaveText(MARKER);
    expect(await page.evaluate(() => (window as unknown as { geheim: string }).geheim)).toBe(MARKER);

    await page.goto(`${CONTENT_URL}/${GROUP}/ander/`);
    await expect(page.locator('#result')).not.toHaveText('bezig');
    const results = JSON.parse((await page.locator('#result').textContent())!) as Record<string, unknown>;
    expect(Object.keys(results)).toHaveLength(6);
    for (const [request, result] of Object.entries(results)) {
      expect(result, request).not.toBe(true);
    }
    await visitor.close();
  });
});
