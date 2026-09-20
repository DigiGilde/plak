/**
 * The real CLI against the e2e stack: `plak publish` (preview and live),
 * `plak preview-remove` and `plak logout`, invoked the way a contributor
 * does with `uv run --project cli plak ...`.
 *
 * The session comes from the device flow through the SPA (helpers/cli.ts),
 * so the token the CLI works with is the one a human ends up with; the
 * assertions are made on the content host, against what a visitor sees.
 */

import { execFile } from 'node:child_process';
import { mkdirSync, mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { promisify } from 'node:util';
import { expect, test, type APIRequestContext, type BrowserContext, type Page } from '@playwright/test';
import { adminFetch, body, newHostContext } from '../helpers/api';
import { cliLoginToken } from '../helpers/cli';
import { logInOnAdmin } from '../helpers/oidc';
import {
  ADMIN_EMAIL,
  ADMIN_HOST,
  ADMIN_SUB,
  ADMIN_URL,
  CONTENT_HOST,
  CONTEXT_OPTIONS,
  NEUTRAL_404,
} from '../helpers/environment';

const execFileAsync = promisify(execFile);

const REPO_ROOT = path.resolve(__dirname, '../..');
const GROUP = 'cli-groep';
const SITE = 'website';
const SITE_PATH = `/${GROUP}/${SITE}`;
const API = '/-/api/v1';
const PREVIEW_REF = 'pr-7';
const VERSION_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

let adminContext: BrowserContext;
let adminPage: Page;
let adminApi: APIRequestContext;
let contentApi: APIRequestContext;
let workDir: string;
let previewDist: string;
let liveDist: string;

interface CliResult {
  code: number;
  stdout: string;
  stderr: string;
}

/**
 * Runs the CLI as a contributor does. The working directory is a temp dir,
 * not the repo root: the CLI keeps its session in .env.plak next to the
 * working directory, and a run must not overwrite the one a contributor
 * may have lying there.
 */
async function plak(args: string[], extraEnv: Record<string, string> = {}): Promise<CliResult> {
  const env: Record<string, string> = {};
  for (const [key, value] of Object.entries(process.env)) {
    if (value !== undefined && !key.startsWith('PLAK_')) {
      env[key] = value;
    }
  }
  const command = ['run', '--project', path.join(REPO_ROOT, 'cli'), 'plak', ...args];
  try {
    const { stdout, stderr } = await execFileAsync('uv', command, {
      cwd: workDir,
      env: { ...env, ...extraEnv },
    });
    return { code: 0, stdout, stderr };
  } catch (error) {
    const failed = error as { code?: number; stdout?: string; stderr?: string };
    return { code: failed.code ?? 1, stdout: failed.stdout ?? '', stderr: failed.stderr ?? '' };
  }
}

function writeDist(name: string, heading: string): string {
  const folder = path.join(workDir, name);
  mkdirSync(path.join(folder, 'assets'), { recursive: true });
  writeFileSync(
    path.join(folder, 'index.html'),
    '<!doctype html><html lang="nl"><head><meta charset="utf-8">' +
      `<title>CLI</title><link rel="stylesheet" href="assets/stijl.css"></head>` +
      `<body><h1>${heading}</h1></body></html>`,
  );
  writeFileSync(path.join(folder, 'assets', 'stijl.css'), 'h1 { color: rebeccapurple; }');
  return folder;
}

test.describe.serial('Plak CLI against the e2e stack', () => {
  test.beforeAll(async ({ browser }) => {
    adminContext = await browser.newContext(CONTEXT_OPTIONS);
    adminPage = await adminContext.newPage();
    adminApi = await newHostContext(ADMIN_HOST);
    contentApi = await newHostContext(CONTENT_HOST);

    workDir = mkdtempSync(path.join(tmpdir(), 'plak-cli-e2e-'));
    previewDist = writeDist('preview-dist', 'CLI preview pr-7');
    liveDist = writeDist('live-dist', 'CLI live');

    await logInOnAdmin(adminPage, ADMIN_URL, ADMIN_SUB, { email: ADMIN_EMAIL });
    const group = await adminFetch(adminPage, 'POST', `${API}/groups`, {
      name: 'CLI-groep',
      slug: GROUP,
    });
    expect(group.status).toBe(201);
    const site = await adminFetch(adminPage, 'POST', `${API}/groups/${GROUP}/sites`, {
      title: 'Website',
      slug: SITE,
    });
    expect(site.status).toBe(201);
    // Public, so the assertions below are about what the CLI published and
    // not about the access gate (that has its own scenarios).
    const access = await adminFetch(adminPage, 'PUT', `${API}/sites/${GROUP}/${SITE}/access`, {
      base: 'public',
    });
    expect(access.status).toBe(200);
  });

  test.afterAll(async () => {
    await adminApi?.dispose();
    await contentApi?.dispose();
    await adminContext?.close();
  });

  test('login, publish preview and live, preview-remove and logout', async () => {
    // Five CLI runs plus the device flow through the browser: more than the
    // suite's default minute, and deliberately one test instead of five that
    // each log in again.
    test.setTimeout(180_000);

    const token = await cliLoginToken(adminApi, adminPage, ADMIN_URL, ADMIN_SUB, {
      email: ADMIN_EMAIL,
    });
    expect(token).toMatch(/^plakcli_/);
    // What `plak login` itself writes after the device flow: the host and the
    // session, only readable by the owner (the CLI refuses a looser file).
    writeFileSync(path.join(workDir, '.env.plak'), `PLAK_HOST=${ADMIN_URL}\nPLAK_ACCESS_TOKEN=${token}\n`, {
      mode: 0o600,
    });

    const preview = await plak([
      'publish',
      previewDist,
      '--host',
      ADMIN_URL,
      '--site',
      `${GROUP}/${SITE}`,
      '--preview',
      PREVIEW_REF,
    ]);
    expect(preview.code, preview.stderr).toBe(0);
    expect(preview.stdout.trim()).toMatch(VERSION_ID);

    const previewPage = await contentApi.get(`${SITE_PATH}/_preview/${PREVIEW_REF}/`);
    expect(previewPage.status()).toBe(200);
    expect(await body(previewPage)).toContain('CLI preview pr-7');
    const previewStyle = await contentApi.get(`${SITE_PATH}/_preview/${PREVIEW_REF}/assets/stijl.css`);
    expect(previewStyle.status()).toBe(200);
    expect(previewStyle.headers()['content-type']).toContain('text/css');

    // Without --preview it is a live deploy, and the site itself serves it.
    const live = await plak(['publish', liveDist, '--host', ADMIN_URL, '--site', `${GROUP}/${SITE}`]);
    expect(live.code, live.stderr).toBe(0);
    expect(live.stdout.trim()).toMatch(VERSION_ID);

    const livePage = await contentApi.get(`${SITE_PATH}/`);
    expect(livePage.status()).toBe(200);
    expect(await body(livePage)).toContain('CLI live');

    const removed = await plak([
      'preview-remove',
      PREVIEW_REF,
      '--host',
      ADMIN_URL,
      '--site',
      `${GROUP}/${SITE}`,
    ]);
    expect(removed.code, removed.stderr).toBe(0);
    expect(removed.stdout).toContain('removed');

    const gone = await contentApi.get(`${SITE_PATH}/_preview/${PREVIEW_REF}/`);
    expect(gone.status()).toBe(404);
    expect(await body(gone)).toBe(NEUTRAL_404);
    // The live version stays: removing a preview touches nothing else.
    const stillLive = await contentApi.get(`${SITE_PATH}/`);
    expect(stillLive.status()).toBe(200);

    const logout = await plak(['logout', '--host', ADMIN_URL]);
    expect(logout.code, logout.stderr).toBe(0);
    expect(logout.stdout).toContain('Logged out');

    // The token is revoked at the server, not just forgotten locally: the
    // same token in the environment no longer publishes anything.
    const afterLogout = await plak(
      ['publish', liveDist, '--host', ADMIN_URL, '--site', `${GROUP}/${SITE}`],
      { PLAK_ACCESS_TOKEN: token },
    );
    expect(afterLogout.code).toBe(1);
    expect(afterLogout.stderr).toContain('Error:');
  });
});
