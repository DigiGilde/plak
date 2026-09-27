/**
 * E2E suite against the compose stack (spec 13), across the two origins of
 * spec 4a: admin actions on the admin host, viewing content on the content
 * host.
 *
 * The scenarios build on each other and run serially (see
 * playwright.config.ts). Admin actions run as an in-page fetch from the
 * admin host: the session API with __Host cookie, SameSite=Strict,
 * Sec-Fetch-Site and CSRF double submit is exactly the contract the SPA uses.
 * Viewing gated content goes through the content origin login (/-/login on
 * the content host, spec 4a) and yields a content session without admin
 * authority.
 */

import { expect, test, type APIRequestContext, type BrowserContext, type Page } from '@playwright/test';
import { adminFetch, body, newHostContext, uploadViaSession } from '../helpers/api';
import { cliLoginToken } from '../helpers/cli';
import { logInOnAdmin, visitWithLogin } from '../helpers/oidc';
import {
  ADMIN_EMAIL,
  ADMIN_HOST,
  ADMIN_SUB,
  ADMIN_URL,
  CONTEXT_OPTIONS,
  CONTENT_HOST,
  CONTENT_URL,
  INVITEE_EMAIL,
  INVITEE_SUB,
  NEUTRAL_404,
  SECOND_MEMBER_EMAIL,
  SECOND_MEMBER_SUB,
} from '../helpers/environment';
import { makeTarGz } from '../helpers/tar';

const GROUP = 'e2e-groep';
const SITE = 'website';
const SITE_PATH = `/${GROUP}/${SITE}`;
const API = '/-/api/v1';

const distV1 = makeTarGz({
  'index.html':
    '<!doctype html><html lang="nl"><head><meta charset="utf-8">' +
    '<title>E2E</title><link rel="stylesheet" href="stijl.css"></head>' +
    '<body><h1>Plak E2E versie 1</h1>' +
    '<a href="onderdeel/pagina.html">onderdeel</a></body></html>',
  'stijl.css': 'h1 { color: rebeccapurple; }',
  'onderdeel/pagina.html':
    '<!doctype html><html lang="nl"><head><meta charset="utf-8">' +
    '<title>Onderdeel</title></head><body><h1>Subpagina versie 1</h1></body></html>',
});

const distV2 = makeTarGz({
  'index.html':
    '<!doctype html><html lang="nl"><head><meta charset="utf-8">' +
    '<title>E2E</title><link rel="stylesheet" href="stijl.css"></head>' +
    '<body><h1>Plak E2E versie 2</h1>' +
    '<a href="onderdeel/pagina.html">onderdeel</a></body></html>',
  'stijl.css': 'h1 { color: seagreen; }',
  'onderdeel/pagina.html':
    '<!doctype html><html lang="nl"><head><meta charset="utf-8">' +
    '<title>Onderdeel</title></head><body><h1>Subpagina versie 2</h1></body></html>',
});

const distPreview = makeTarGz({
  'index.html':
    '<!doctype html><html lang="nl"><head><meta charset="utf-8">' +
    '<title>Preview</title></head><body><h1>Plak E2E preview pr-42</h1></body></html>',
});

let adminContext: BrowserContext;
let secondMemberContext: BrowserContext;
let inviteeContext: BrowserContext;
let adminPage: Page;
let secondMemberPage: Page;

// Node-side contexts: anonymous requests per host, plus one for the bearer
// deploys (the CI path without an Origin header, the way the publiceer action goes).
let contentApi: APIRequestContext;
let adminApi: APIRequestContext;

let versionId1: string;
let versionId2: string;
let cliToken: string;

test.describe.serial('Plak E2E (spec 13)', () => {
  test.beforeAll(async ({ browser }) => {
    adminContext = await browser.newContext(CONTEXT_OPTIONS);
    secondMemberContext = await browser.newContext(CONTEXT_OPTIONS);
    inviteeContext = await browser.newContext(CONTEXT_OPTIONS);
    adminPage = await adminContext.newPage();
    secondMemberPage = await secondMemberContext.newPage();
    contentApi = await newHostContext(CONTENT_HOST);
    adminApi = await newHostContext(ADMIN_HOST);
  });

  test.afterAll(async () => {
    await contentApi?.dispose();
    await adminApi?.dispose();
    await adminContext?.close();
    await secondMemberContext?.close();
    await inviteeContext?.close();
  });

  // Proves the content origin login (spec 4a) is present in the stack: a
  // gated content URL leads anonymously to a working login route. Agnostic
  // about the mechanism (relative path, admin host or external IdP origin);
  // a regression fails hard here instead of skipping.
  let handoffStatus: boolean | null = null;
  async function handoffPresent(): Promise<boolean> {
    if (handoffStatus !== null) {
      return handoffStatus;
    }
    const first = await contentApi.get(`${SITE_PATH}/`, { maxRedirects: 0 });
    if (first.status() !== 302) {
      handoffStatus = false;
      return handoffStatus;
    }
    const location = first.headers()['location'] ?? '';
    const target = new URL(location, CONTENT_URL);
    if (target.host !== CONTENT_HOST && target.host !== ADMIN_HOST) {
      // Redirect to another origin (IdP): there is a handoff.
      handoffStatus = true;
      return handoffStatus;
    }
    const followUpContext = target.host === ADMIN_HOST ? adminApi : contentApi;
    const followUp = await followUpContext.get(target.pathname + target.search, { maxRedirects: 0 });
    handoffStatus = followUp.status() < 400;
    return handoffStatus;
  }

  const HANDOFF_MESSAGE = 'content-origin login (spec 4a) missing from the stack';

  test('stack is healthy on both hosts', async () => {
    const admin = await adminApi.get('/healthz-proxy');
    const content = await contentApi.get('/healthz-proxy');
    expect(admin.status()).toBe(200);
    expect(content.status()).toBe(200);
  });

  test('bootstrap-login: dev-admin logs in on the admin host', async () => {
    await logInOnAdmin(adminPage, ADMIN_URL, ADMIN_SUB, { email: ADMIN_EMAIL });

    // The admin session cookie is a __Host cookie on the admin host and
    // does not exist on the content host (origin separation).
    const adminCookies = await adminContext.cookies(ADMIN_URL);
    expect(adminCookies.some((cookie) => cookie.name === '__Host-plak-session')).toBe(true);
    const contentCookies = await adminContext.cookies(CONTENT_URL);
    expect(contentCookies).toHaveLength(0);

    const me = await adminFetch(adminPage, 'GET', `${API}/me`);
    expect(me.status).toBe(200);
    const member = me.json as { ssoSubject: string; platformRole: string; status: string };
    expect(member.ssoSubject).toBe(ADMIN_SUB);
    expect(member.platformRole).toBe('admin');
    expect(member.status).toBe('active');
  });

  test('admin SPA is served on the admin host', async () => {
    // Straight to the root, where the SPA now lives. Not to /admin/: the
    // Node-side client drops the explicit Host header when following that
    // 301, after which TrustedHost answers 400.
    const response = await adminApi.get('/');
    const contentType = response.headers()['content-type'] ?? '';
    test.skip(
      response.status() !== 200 || !contentType.includes('text/html'),
      'frontend/dist is not built (just build-spa); the app then answers 503 on /',
    );
    expect(response.status()).toBe(200);
    expect(contentType).toContain('text/html');
  });

  test('second member signs up and is immediately active', async () => {
    await logInOnAdmin(secondMemberPage, ADMIN_URL, SECOND_MEMBER_SUB, { email: SECOND_MEMBER_EMAIL });

    // The first admin touch creates the member record as 'active': there is
    // no approval step, an administrator only deactivates afterwards.
    const me = await adminFetch(secondMemberPage, 'GET', `${API}/me`);
    expect(me.status).toBe(200);
    expect((me.json as { status: string }).status).toBe('active');

    const members = await adminFetch(adminPage, 'GET', `${API}/platform/members`);
    expect(members.status).toBe(200);
    const newcomer = (members.json as Array<{ ssoSubject: string; status: string }>).find(
      (member) => member.ssoSubject === SECOND_MEMBER_SUB,
    );
    expect(newcomer).toBeDefined();
    expect(newcomer!.status).toBe('active');
  });

  test('create group and site on the admin host', async () => {
    const group = await adminFetch(adminPage, 'POST', `${API}/groups`, {
      name: 'E2E-groep',
      slug: GROUP,
    });
    expect(group.status).toBe(201);

    // Creating sites requires group membership. The creator already has
    // it (adding again is the ordinary duplicate); the second member still
    // has to be added.
    const alreadyMember = await adminFetch(adminPage, 'POST', `${API}/groups/${GROUP}/members`, {
      identifier: ADMIN_EMAIL,
    });
    expect(alreadyMember.status).toBe(409);
    const added = await adminFetch(adminPage, 'POST', `${API}/groups/${GROUP}/members`, {
      identifier: SECOND_MEMBER_EMAIL,
    });
    expect(added.status).toBe(201);

    const site = await adminFetch(adminPage, 'POST', `${API}/groups/${GROUP}/sites`, {
      title: 'Website',
      slug: SITE,
    });
    expect(site.status).toBe(201);
    expect((site.json as { access: { base: string } }).access.base).toBe('site_team');

    // Without a live version the site is a neutral 404 for everyone,
    // whatever the access setting (spec 5.6).
    const access = await adminFetch(adminPage, 'PUT', `${API}/sites/${GROUP}/${SITE}/access`, {
      base: 'public',
    });
    expect(access.status).toBe(200);
    const withoutLive = await contentApi.get(`${SITE_PATH}/`);
    expect(withoutLive.status()).toBe(404);
    expect(await body(withoutLive)).toBe(NEUTRAL_404);
  });

  test('dist upload (html, css and subfolder) goes live on the content host', async () => {
    const upload = await uploadViaSession(adminPage, GROUP, SITE, distV1);
    expect(upload.status).toBe(201);
    versionId1 = (upload.json as { versionId: string }).versionId;
    expect(versionId1).toBeTruthy();

    // Anonymous in the browser on the content host: page, stylesheet and subdirectory.
    const anonymous = await adminContext.browser()!.newContext(CONTEXT_OPTIONS);
    const page = await anonymous.newPage();
    await page.goto(`${CONTENT_URL}${SITE_PATH}/`);
    await expect(page.locator('h1')).toHaveText('Plak E2E versie 1');
    await anonymous.close();

    const style = await contentApi.get(`${SITE_PATH}/stijl.css`);
    expect(style.status()).toBe(200);
    expect(style.headers()['content-type']).toContain('text/css');
    const subPage = await contentApi.get(`${SITE_PATH}/onderdeel/pagina.html`);
    expect(subPage.status()).toBe(200);
    expect(await body(subPage)).toContain('Subpagina versie 1');

    // Lexical trailing-slash 301 (spec 5.2).
    const withoutSlash = await contentApi.get(SITE_PATH, { maxRedirects: 0 });
    expect(withoutSlash.status()).toBe(301);
    expect(withoutSlash.headers()['location']).toBe(`${SITE_PATH}/`);

    // Two origins: the admin host never serves site content (the same path is
    // the SPA's own site page there), and the admin API does not exist on
    // the content host (spec 4a).
    const wrongOrigin = await adminApi.get(`${SITE_PATH}/`);
    const adminBody = await body(wrongOrigin);
    expect(adminBody).toContain('<div id="app">');
    expect(adminBody).not.toContain('Plak E2E versie 1');
    const apiOnContent = await contentApi.get(`${API}/overview`);
    expect(apiOnContent.status()).toBe(404);
    expect(await body(apiOnContent)).toBe(NEUTRAL_404);
  });

  test('second version live; old version via _version only for group members', async () => {
    const upload = await uploadViaSession(adminPage, GROUP, SITE, distV2);
    expect(upload.status).toBe(201);
    versionId2 = (upload.json as { versionId: string }).versionId;

    const live = await contentApi.get(`${SITE_PATH}/`);
    expect(await body(live)).toContain('Plak E2E versie 2');

    const versions = await adminFetch(adminPage, 'GET', `${API}/sites/${GROUP}/${SITE}/versions`);
    expect(versions.status).toBe(200);
    const list = versions.json as Array<{ id: string; isLive: boolean }>;
    expect(list).toHaveLength(2);
    expect(list.find((version) => version.id === versionId1)?.isLive).toBe(false);
    expect(list.find((version) => version.id === versionId2)?.isLive).toBe(true);

    // Anonymously a _version view is always the neutral 404, never a login
    // redirect (spec 7).
    const anonymousVersion = await contentApi.get(`${SITE_PATH}/_version/${versionId1}/`, {
      maxRedirects: 0,
    });
    expect(anonymousVersion.status()).toBe(404);
    expect(await body(anonymousVersion)).toBe(NEUTRAL_404);
  });

  test('group member views the old version via _version on the content host', async () => {
    // Gate it so the content host demands a login; that gives the group
    // member a content session without admin authority.
    const access = await adminFetch(adminPage, 'PUT', `${API}/sites/${GROUP}/${SITE}/access`, {
      base: 'site_team',
    });
    expect(access.status).toBe(200);

    expect(await handoffPresent(), HANDOFF_MESSAGE).toBe(true);

    const contentPage = await secondMemberContext.newPage();
    await visitWithLogin(contentPage, `${CONTENT_URL}${SITE_PATH}/`, SECOND_MEMBER_SUB, {
      email: SECOND_MEMBER_EMAIL,
    });
    await expect(contentPage.locator('h1')).toHaveText('Plak E2E versie 2');

    await contentPage.goto(`${CONTENT_URL}${SITE_PATH}/_version/${versionId1}/`);
    await expect(contentPage.locator('h1')).toHaveText('Plak E2E versie 1');
    await contentPage.close();
  });

  test('deploy a preview via the deploy API with a CLI token', async () => {
    const access = await adminFetch(adminPage, 'PUT', `${API}/sites/${GROUP}/${SITE}/access`, {
      base: 'public',
    });
    expect(access.status).toBe(200);

    // `plak login`: device authorization, approval in the real SPA at
    // /cli-link, and the token exchange, all Node-side plus the
    // already-logged-in admin page, the way a human and the CLI do it together.
    cliToken = await cliLoginToken(adminApi, adminPage, ADMIN_URL, ADMIN_SUB, { email: ADMIN_EMAIL });
    expect(cliToken).toMatch(/^plakcli_/);

    // Bearer deploy without an Origin header: the CI path of the publiceer action
    // (a CLI token is accepted the same way).
    const deploy = await adminApi.post(`${API}/sites/${GROUP}/${SITE}/deploys`, {
      headers: { authorization: `Bearer ${cliToken}` },
      multipart: {
        file: { name: 'site.tar.gz', mimeType: 'application/gzip', buffer: distPreview },
        preview: 'pr-42',
      },
    });
    expect(deploy.status()).toBe(201);

    const preview = await contentApi.get(`${SITE_PATH}/_preview/pr-42/`);
    expect(preview.status()).toBe(200);
    expect(await body(preview)).toContain('Plak E2E preview pr-42');
    expect(preview.headers()['x-robots-tag']).toContain('noindex');

    // Teardown is idempotent (spec 8): 204 twice, then the neutral 404.
    for (let attempt = 0; attempt < 2; attempt += 1) {
      const teardown = await adminApi.delete(`${API}/sites/${GROUP}/${SITE}/previews/pr-42`, {
        headers: { authorization: `Bearer ${cliToken}` },
      });
      expect(teardown.status()).toBe(204);
    }
    const gone = await contentApi.get(`${SITE_PATH}/_preview/pr-42/`);
    expect(gone.status()).toBe(404);
    expect(await body(gone)).toBe(NEUTRAL_404);
  });

  test('create a secret link and use it anonymously', async () => {
    // Base nobody plus the secret link: the link is the only way in, which is
    // what the old single level called 'key'.
    const access = await adminFetch(adminPage, 'PUT', `${API}/sites/${GROUP}/${SITE}/access`, {
      base: 'nobody',
      keys: true,
    });
    expect(access.status).toBe(200);

    const key = await adminFetch(adminPage, 'POST', `${API}/sites/${GROUP}/${SITE}/keys`, {
      label: 'e2e-link',
    });
    expect(key.status).toBe(201);
    const { value, key: keyJson } = key.json as {
      value: string;
      key: { selector: string };
    };
    expect(value).toContain('.');

    // Without a key: neutral 404, no login redirect.
    const without = await contentApi.get(`${SITE_PATH}/`, { maxRedirects: 0 });
    expect(without.status()).toBe(404);
    expect(await body(without)).toBe(NEUTRAL_404);

    // Redeem: a valid ?key= sets the cookie and redirects to the same URL
    // without the key; the key disappears from the address bar and the
    // cookie route serves from there.
    const withKey = await contentApi.get(`${SITE_PATH}/?key=${encodeURIComponent(value)}&x=1`, {
      maxRedirects: 0,
    });
    expect(withKey.status()).toBe(302);
    expect(withKey.headers()['location']).toBe(`${SITE_PATH}/?x=1`);
    expect(withKey.headers()['set-cookie']).toContain('__Secure-plak-key=');

    // With the key in an anonymous browser: content visible on the clean URL,
    // and the __Secure cookie carries the follow-up navigation without ?key.
    const anonymous = await adminContext.browser()!.newContext(CONTEXT_OPTIONS);
    const page = await anonymous.newPage();
    await page.goto(`${CONTENT_URL}${SITE_PATH}/?key=${encodeURIComponent(value)}`);
    await expect(page.locator('h1')).toHaveText('Plak E2E versie 2');
    expect(page.url()).toBe(`${CONTENT_URL}${SITE_PATH}/`);
    await page.goto(`${CONTENT_URL}${SITE_PATH}/onderdeel/pagina.html`);
    await expect(page.locator('h1')).toHaveText('Subpagina versie 2');

    // Revoking breaks outstanding cookies immediately (spec 7).
    const revoke = await adminFetch(
      adminPage,
      'DELETE',
      `${API}/sites/${GROUP}/${SITE}/keys/${keyJson.selector}`,
    );
    expect(revoke.status).toBe(204);
    await page.goto(`${CONTENT_URL}${SITE_PATH}/`);
    await expect(page.locator('body')).toContainText('Niet gevonden');
    await anonymous.close();
  });

  test('invitee flow: invite by email, log in on the content host, return', async () => {
    const access = await adminFetch(adminPage, 'PUT', `${API}/sites/${GROUP}/${SITE}/access`, {
      base: 'nobody',
      invitees: true,
    });
    expect(access.status).toBe(200);

    const invitee = await adminFetch(adminPage, 'POST', `${API}/sites/${GROUP}/${SITE}/invitees`, {
      identifier: INVITEE_EMAIL,
    });
    expect(invitee.status).toBe(201);
    expect((invitee.json as { identifier: string }).identifier).toBe(INVITEE_EMAIL);

    expect(await handoffPresent(), HANDOFF_MESSAGE).toBe(true);

    // Deep link: after the login handoff the invitee belongs back on exactly
    // the page that was requested (spec 7, returnTo).
    const targetUrl = `${CONTENT_URL}${SITE_PATH}/onderdeel/pagina.html`;
    const page = await inviteeContext.newPage();
    await visitWithLogin(page, targetUrl, INVITEE_SUB, { email: INVITEE_EMAIL });
    expect(page.url()).toBe(targetUrl);
    await expect(page.locator('h1')).toHaveText('Subpagina versie 2');

    // The content session rides in cookies of its own on the content host
    // and carries no admin authority: the admin API does not exist on the
    // content host, and on the admin host the invitee holds no cookie at all
    // (the content login set nothing there). The session id itself is
    // path-scoped to this site (auth/sessions.py), so it only shows up for a
    // URL under the site; on the root of the host there is the presence flag
    // and nothing that grants anything.
    const siteCookies = await inviteeContext.cookies(`${CONTENT_URL}${SITE_PATH}/`);
    expect(siteCookies.some((cookie) => cookie.name === '__Secure-plak-content')).toBe(true);
    const contentCookies = await inviteeContext.cookies(CONTENT_URL);
    expect(contentCookies.some((cookie) => cookie.name === '__Host-plak-content-present')).toBe(true);
    expect(contentCookies.some((cookie) => cookie.name === '__Secure-plak-content')).toBe(false);
    expect(contentCookies.some((cookie) => cookie.name === '__Host-plak-session')).toBe(false);
    // From inside the page the call does not even leave the browser: the
    // sandbox gives the document an opaque origin, so connect-src 'self'
    // no longer matches its own host (serving/response.py). From outside
    // the sandbox the API really is absent on the content host.
    const apiOnContent = await page.evaluate(async () => {
      try {
        return String((await fetch('/-/api/v1/me')).status);
      } catch {
        return 'blocked';
      }
    });
    expect(apiOnContent).toBe('blocked');
    const apiFromNode = await contentApi.get('/-/api/v1/me');
    expect(apiFromNode.status()).toBe(404);
    const adminCookies = await inviteeContext.cookies(ADMIN_URL);
    expect(adminCookies.filter((cookie) => cookie.name === '__Host-plak-session')).toHaveLength(0);
    await page.close();

    // And in the browser itself: on the admin origin the invitee is
    // anonymous; the content session (host-only cookie on the content host)
    // does not travel along.
    const adminHostPage = await inviteeContext.newPage();
    await adminHostPage.goto(`${ADMIN_URL}/`);
    const meOnAdmin = await adminHostPage.evaluate(async () => (await fetch('/-/api/v1/me')).status);
    expect(meOnAdmin).toBe(401);
    await adminHostPage.close();

    // Whoever is not on the list gets the neutral 404 after logging in: the
    // second member is a group member but not an invitee, and the base is
    // 'nobody' (group membership does not count there).
    const memberPage = await secondMemberContext.newPage();
    await visitWithLogin(memberPage, `${CONTENT_URL}${SITE_PATH}/`, SECOND_MEMBER_SUB, {
      email: SECOND_MEMBER_EMAIL,
    });
    await expect(memberPage.locator('body')).toContainText('Niet gevonden');
    await memberPage.close();
  });

  test('deleting a site cleans up live and previews', async () => {
    // A fresh preview so the cascade over previews is demonstrable.
    const deploy = await adminApi.post(`${API}/sites/${GROUP}/${SITE}/deploys`, {
      headers: { authorization: `Bearer ${cliToken}` },
      multipart: {
        file: { name: 'site.tar.gz', mimeType: 'application/gzip', buffer: distPreview },
        preview: 'pr-42',
      },
    });
    expect(deploy.status()).toBe(201);

    const deleted = await adminFetch(adminPage, 'DELETE', `${API}/sites/${GROUP}/${SITE}`);
    expect(deleted.status).toBe(204);

    const overview = await adminFetch(adminPage, 'GET', `${API}/overview`);
    expect(overview.status).toBe(200);
    const groups = (overview.json as { groups: Array<{ group: { slug: string }; sites: unknown[] }> })
      .groups;
    const ownGroup = groups.find((row) => row.group.slug === GROUP);
    expect(ownGroup).toBeDefined();
    expect(ownGroup!.sites).toHaveLength(0);

    for (const path of [`${SITE_PATH}/`, `${SITE_PATH}/_preview/pr-42/`]) {
      const response = await contentApi.get(path, { maxRedirects: 0 });
      expect(response.status()).toBe(404);
      expect(await body(response)).toBe(NEUTRAL_404);
    }
  });

  // Last, because it changes something about the account itself rather than
  // about content: the interface language. Chromium sends
  // Accept-Language: en-US,en, and the suite deliberately leaves that alone
  // instead of pinning nl-NL in playwright.config.ts: that way this test sees
  // the same negotiation a browser abroad would get, and the rest of the
  // suite never reads an interface label, only content it uploaded itself.
  test('the interface language follows the browser, and the account overrules it', async () => {
    await adminPage.goto(`${ADMIN_URL}/-/profile`);
    await expect(adminPage.locator('h1')).toHaveText('Profile');
    await expect(adminPage.locator('html')).toHaveAttribute('lang', 'en');

    const saved = await adminFetch(adminPage, 'PUT', `${API}/me/language`, { language: 'nl' });
    expect(saved.status).toBe(204);

    // A fresh load, so this proves the choice comes back from the account and
    // not from anything this page kept in memory.
    await adminPage.goto(`${ADMIN_URL}/-/profile`);
    await expect(adminPage.locator('h1')).toHaveText('Profiel');
    await expect(adminPage.locator('html')).toHaveAttribute('lang', 'nl');

    // And the SPA asks the API for that language, so a refusal comes back in
    // the language on screen instead of in the API's own default. One more
    // load, because the first one still asked in the browser's language and
    // only learned about the account halfway through.
    const meRequest = adminPage.waitForRequest((request) =>
      request.url().endsWith(`${API}/me`),
    );
    await adminPage.goto(`${ADMIN_URL}/-/profile`);
    expect((await meRequest).headers()['accept-language']).toBe('nl');

    const cleared = await adminFetch(adminPage, 'PUT', `${API}/me/language`, { language: null });
    expect(cleared.status).toBe(204);
  });
});
