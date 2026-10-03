/**
 * CLI login helper (`plak login`, OAuth 2.0 device authorization grant).
 * Drives the real flow: a Node-side request starts the device
 * authorization, the already-logged-in admin page approves it at
 * /cli-link (the SPA, exactly like a human would), and a Node-side poll
 * exchanges the device code for a CLI access token, rather than a deploy
 * token minted directly.
 */

import { expect, type APIRequestContext, type Page } from '@playwright/test';
import { fillMockLogin, onMockLoginPage } from './oidc';

interface DeviceAuthorizationOut {
  deviceCode: string;
  userCode: string;
  verificationUriComplete: string;
  interval: number;
}

interface TokensOut {
  accessToken: string;
}

/**
 * Logs the CLI in as the member behind `adminPage`'s session and returns a
 * fresh CLI access token (`plakcli_...`). `adminApi` is a Node-side request
 * context on the admin host (no session, no CSRF: device-authorization
 * creation needs neither); `adminPage` is a browser page already logged in
 * on the admin host, used to approve the code the way a human would.
 */
export async function cliLoginToken(
  adminApi: APIRequestContext,
  adminPage: Page,
  adminUrl: string,
  memberSub: string,
  memberClaims: Record<string, unknown>,
): Promise<string> {
  const start = await adminApi.post('/-/api/v1/cli/device-authorizations', {
    data: { clientName: 'e2e' },
  });
  expect(start.status()).toBe(200);
  const authorization = (await start.json()) as DeviceAuthorizationOut;

  await adminPage.goto(authorization.verificationUriComplete);
  // An admin session older than a quarter hour bounces back through login
  // (SESSION_NOT_FRESH, surfaced only once the page tries the lookup call);
  // the suite's existing mock-login helper finishes that the same way the
  // other scenarios do. Race the two outcomes instead of a fixed check
  // right after navigation, since either one takes a round trip to appear.
  const codeShown = adminPage.getByTestId('code-display');
  const loginForm = onMockLoginPage(adminPage);
  await expect(codeShown.or(loginForm)).toBeVisible({ timeout: 15_000 });
  if (await loginForm.isVisible()) {
    await fillMockLogin(adminPage, memberSub, memberClaims);
    await adminPage.waitForURL((url) => url.href.startsWith(adminUrl), { timeout: 15_000 });
  }
  await expect(codeShown).toContainText(authorization.userCode);
  await adminPage.getByTestId('code-link').click();
  await expect(adminPage.getByTestId('code-linked')).toBeVisible();

  // The approval already happened above, so the first exchange should
  // succeed; a short retry absorbs the AUTHORIZATION_PENDING window on a
  // slow CI runner instead of hardcoding the 5s poll interval.
  for (let attempt = 0; attempt < 5; attempt += 1) {
    const tokens = await adminApi.post('/-/api/v1/cli/tokens', {
      data: { grantType: 'device_code', deviceCode: authorization.deviceCode },
    });
    if (tokens.status() === 200) {
      return ((await tokens.json()) as TokensOut).accessToken;
    }
    await adminPage.waitForTimeout(authorization.interval * 1000);
  }
  throw new Error('CLI token exchange did not succeed after approval.');
}
