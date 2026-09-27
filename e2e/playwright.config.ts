import { defineConfig, devices } from '@playwright/test';
import { ADMIN_URL, PROXY_PORT } from './helpers/environment';

/**
 * The scenarios from spec 13 build on each other (activation -> group ->
 * project -> deploy -> access -> deletion): one worker, no parallelism, no
 * retries (a flaky run has to be visible, not masked).
 */
export default defineConfig({
  testDir: './tests',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  reporter: [['list']],
  globalSetup: './global-setup',
  use: {
    ...devices['Desktop Chrome'],
    baseURL: ADMIN_URL,
    // A failure in CI is otherwise a stack trace and nothing to look at.
    // Both land in test-results/, which the CI job uploads as an artefact.
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    // All browser traffic goes through the mini forward proxy
    // (helpers/proxy.ts): it sends the internal mock OIDC origin to the
    // published port and pins *.localhost on 127.0.0.1 (macOS otherwise
    // resolves that to ::1). '<-loopback>' removes Chromium's implicit
    // localhost bypass, without which exactly this traffic would go around
    // the proxy.
    proxy: {
      server: `http://127.0.0.1:${PROXY_PORT}`,
      bypass: '<-loopback>',
    },
  },
});
