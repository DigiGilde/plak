/**
 * Mock OIDC helpers. All browser traffic goes through the mini forward proxy
 * (helpers/proxy.ts), which lets the redirect to the internal issuer origin
 * http://mock-oidc:8080 be followed in the browser as usual and keeps cookies
 * (Secure over http on *.localhost included) browser-native.
 *
 * The e2e mock configuration runs with interactiveLogin, so the suite can log
 * in as different identities (admin, second member, invitee).
 */

import { expect, type Page } from '@playwright/test';

export function onMockLoginPage(page: Page) {
  return page.locator('input[name="username"]');
}

/**
 * Fills in the interactive login form of mock-oauth2-server. The username
 * becomes the sub claim; extra claims (email, for instance) ride into the id
 * token through the claims text field. acr and email_verified come from the
 * tokenCallback in mock-oidc-config.e2e.json.
 */
export async function fillMockLogin(
  page: Page,
  username: string,
  claims: Record<string, unknown> = {},
): Promise<void> {
  const usernameField = onMockLoginPage(page);
  await expect(usernameField).toBeVisible();
  await usernameField.fill(username);

  const claimsField = page.locator('textarea[name="claims"]');
  if ((await claimsField.count()) > 0 && Object.keys(claims).length > 0) {
    await claimsField.fill(JSON.stringify(claims));
  }

  await page
    .locator('form button[type="submit"], form input[type="submit"], form button')
    .first()
    .click();
}

/** Full admin login: start at /-/login and finish the mock login. */
export async function logInOnAdmin(
  page: Page,
  adminUrl: string,
  username: string,
  claims: Record<string, unknown> = {},
): Promise<void> {
  await page.goto(`${adminUrl}/-/login`);
  await fillMockLogin(page, username, claims);
  // The callback redirects to returnTo (the SPA root by default) on the admin
  // host; which page sits there does not matter here.
  await page.waitForURL((url) => url.href.startsWith(adminUrl), { timeout: 15_000 });
}

/**
 * Visits a gated content URL and, when the app redirects to login, finishes
 * the mock login (content origin login handoff, spec 4a/7). Deliberately
 * agnostic about the handoff mechanism: only the end result is checked (back
 * on the requested page).
 */
export async function visitWithLogin(
  page: Page,
  targetUrl: string,
  username: string,
  claims: Record<string, unknown> = {},
): Promise<void> {
  await page.goto(targetUrl);
  if ((await onMockLoginPage(page).count()) > 0) {
    await fillMockLogin(page, username, claims);
    await page.waitForURL(targetUrl, { timeout: 15_000 });
  }
}
