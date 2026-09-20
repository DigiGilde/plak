/**
 * Environment constants for the E2E suite.
 *
 * The stack runs as a separate compose site (plak-e2e) on its own ports,
 * so the dev stack (port 8080) and parallel test runs stay untouched.
 * Two origins behind the same dumb nginx (spec 4a): the admin host and the
 * content host differ only in their Host header; nginx passes it through and
 * the app does the host separation itself.
 */

export const PORT = process.env.PLAK_E2E_PORT ?? '18888';
export const OIDC_PORT = process.env.PLAK_E2E_OIDC_PORT ?? '18889';
export const PROXY_PORT = process.env.PLAK_E2E_PROXY_PORT ?? '18890';

// The hostname itself stays Dutch: the SPA sits on the root of this host and
// everything the app owns sits under /-/ (dictionary).
export const ADMIN_HOST = `beheer.plak.localhost:${PORT}`;
export const CONTENT_HOST = `plak.localhost:${PORT}`;
export const ADMIN_URL = `http://${ADMIN_HOST}`;
export const CONTENT_URL = `http://${CONTENT_HOST}`;

// mock-oauth2-server: the app talks to http://mock-oidc:8080 internally (the
// issuer origin); the browser reaches the same container through the port
// published on the host. The oidc helper proxies the internal origin there.
export const MOCK_OIDC_INTERNAL = 'http://mock-oidc:8080';
export const MOCK_OIDC_PUBLIC = `http://127.0.0.1:${OIDC_PORT}`;

// dev/compose.yml sets the issuer to http://oidc.plak.localhost:8080/default:
// a network alias on the e2e nginx, which proxies to the e2e mock. The browser
// has to make that hostname (with dev port 8080 in the issuer URL) come out on
// the e2e nginx, not on a dev stack that happens to be running on 8080.
export const MOCK_OIDC_ALIAS_HOSTNAME = 'oidc.plak.localhost';

// Byte-identical to serving/response.py:NEUTRAL_404_BODY; the app's own host
// separation returns exactly this on the wrong origin (anti-enumeration).
export const NEUTRAL_404 = 'Niet gevonden\n';

// Context options for manually created browser contexts: use.proxy from
// playwright.config.ts applies to fixture contexts only, so pass the proxy
// explicitly here.
export const CONTEXT_OPTIONS = {
  proxy: {
    server: `http://127.0.0.1:${PROXY_PORT}`,
    bypass: '<-loopback>',
  },
} as const;

export const ADMIN_SUB = 'dev-beheerder';
export const ADMIN_EMAIL = 'beheerder@plak.local';
export const SECOND_MEMBER_SUB = 'tweede-lid';
export const SECOND_MEMBER_EMAIL = 'tweede-lid@plak.local';
export const INVITEE_SUB = 'genodigde';
export const INVITEE_EMAIL = 'genodigde@plak.local';
