/**
 * API helpers for the E2E suite.
 *
 * Admin actions run as an in-page fetch from a page on the admin host: that
 * way they carry exactly what a real SPA session carries (the __Host session
 * cookie with SameSite=Strict, Sec-Fetch-Site: same-origin and the CSRF
 * double submit).
 *
 * Anonymous and Node-side requests use an APIRequestContext that connects to
 * 127.0.0.1 with an explicit Host header: macOS resolves *.localhost to ::1,
 * while the compose stack publishes on 127.0.0.1 only.
 */

import type { APIRequestContext, Page, APIResponse } from '@playwright/test';
import { request as apiRequest } from '@playwright/test';
import { PORT } from './environment';

// Not `ApiResponse`: that differs from Playwright's own `APIResponse` in
// casing only, and both are in scope here.
export interface ApiResult {
  status: number;
  json: unknown;
  text: string;
}

/** JSON request from the page itself (same origin, CSRF header from the cookie). */
export async function adminFetch(
  page: Page,
  method: string,
  path: string,
  body?: unknown,
): Promise<ApiResult> {
  return page.evaluate(
    async ({ method, path, body }) => {
      const csrf = document.cookie
        .split('; ')
        .find((part) => part.startsWith('__Host-plak-csrf='))
        ?.substring('__Host-plak-csrf='.length);
      const headers: Record<string, string> = {};
      if (csrf) {
        headers['X-CSRF-Token'] = decodeURIComponent(csrf);
      }
      const options: RequestInit = { method, headers };
      if (body !== undefined) {
        headers['Content-Type'] = 'application/json';
        options.body = JSON.stringify(body);
      }
      const response = await fetch(path, options);
      const text = await response.text();
      let json: unknown = null;
      try {
        json = JSON.parse(text);
      } catch {
        // no JSON body (e.g. 204)
      }
      return { status: response.status, json, text };
    },
    { method, path, body },
  );
}

/**
 * Multipart upload to the deploy API over the session (the same path the SPA
 * uses). The archive goes into the page as base64 and comes out as FormData.
 */
export async function uploadViaSession(
  page: Page,
  group: string,
  site: string,
  tarGz: Buffer,
  previewRef?: string,
): Promise<ApiResult> {
  return page.evaluate(
    async ({ group, site, data, previewRef }) => {
      const csrf = document.cookie
        .split('; ')
        .find((part) => part.startsWith('__Host-plak-csrf='))
        ?.substring('__Host-plak-csrf='.length);
      const bytes = Uint8Array.from(atob(data), (char) => char.charCodeAt(0));
      const form = new FormData();
      form.set('file', new Blob([bytes], { type: 'application/gzip' }), 'site.tar.gz');
      if (previewRef) {
        form.set('preview', previewRef);
      }
      const response = await fetch(`/-/api/v1/sites/${group}/${site}/deploys`, {
        method: 'POST',
        headers: csrf ? { 'X-CSRF-Token': decodeURIComponent(csrf) } : {},
        body: form,
      });
      const text = await response.text();
      let json: unknown = null;
      try {
        json = JSON.parse(text);
      } catch {
        // no JSON body
      }
      return { status: response.status, json, text };
    },
    { group, site, data: tarGz.toString('base64'), previewRef },
  );
}

/** Node-side request context for one of the two hosts (or the mock). */
export async function newHostContext(host: string): Promise<APIRequestContext> {
  return apiRequest.newContext({
    baseURL: `http://127.0.0.1:${PORT}`,
    extraHTTPHeaders: { host },
  });
}

export async function body(response: APIResponse): Promise<string> {
  return (await response.body()).toString('utf8');
}
