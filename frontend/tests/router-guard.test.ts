import '@nldd/design-system';

import { createRouter, createMemoryHistory } from 'vue-router';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { _resetCurrentMemberCache } from '../src/composables/currentMember';
import { makeMockBackend, type MockBackend } from '../src/api/mock';
import { platformAdminGuard, routes } from '../src/router';

let backend: MockBackend;

afterEach(() => {
  vi.unstubAllGlobals();
  _resetCurrentMemberCache();
});

/**
 * Router guard for '/-/platform' (spec 4, 9): a platform admin gets through, an
 * ordinary member or an anonymous visitor is sent back to the overview. Uses a
 * memory-history router (rather than the createWebHistory singleton from
 * router.ts): createWebHistory's initial navigation hangs in jsdom (no real
 * browser navigation), memory history does not, and that is exactly what this
 * test leans on (the guard, not the real URL bar).
 */
function testRouter() {
  return createRouter({ history: createMemoryHistory(), routes });
}

describe('router-guard /-/platform', () => {
  it('laat een platformbeheerder toe', async () => {
    backend = makeMockBackend();
    vi.stubGlobal('fetch', backend.fetch);
    const router = testRouter();
    router.beforeEach(platformAdminGuard);
    await router.push('/-/platform');

    expect(router.currentRoute.value.name).toBe('platform');
  });

  it('stuurt een gewoon lid terug naar het overzicht', async () => {
    const data = makeMockBackend().data;
    data.loggedInMemberId = 'lid-2';
    data.members.find((l) => l.id === 'lid-2')!.status = 'active';
    backend = makeMockBackend(data);
    vi.stubGlobal('fetch', backend.fetch);
    const router = testRouter();
    router.beforeEach(platformAdminGuard);
    await router.push('/-/platform');

    expect(router.currentRoute.value.name).toBe('overview');
  });

  it('stuurt een anonieme bezoeker terug naar het overzicht', async () => {
    const data = makeMockBackend().data;
    data.loggedInMemberId = null;
    backend = makeMockBackend(data);
    vi.stubGlobal('fetch', backend.fetch);
    const router = testRouter();
    router.beforeEach(platformAdminGuard);
    await router.push('/-/platform');

    expect(router.currentRoute.value.name).toBe('overview');
  });

  it('stuurt terug naar het overzicht als de sessie niet is op te halen', async () => {
    // An unexpected (non-401/403) failure while resolving /me: deny by
    // default rather than let navigation hang on a network hiccup.
    vi.stubGlobal('fetch', vi.fn(() => Promise.reject(new Error('netwerkfout'))));
    const router = testRouter();
    router.beforeEach(platformAdminGuard);
    await router.push('/-/platform');

    expect(router.currentRoute.value.name).toBe('overview');
  });

  it('laat publieke platformpagina\'s door zonder sessiecheck', async () => {
    // No fetch stub: if the guard were to call me() here after all, the test fails on a network error.
    const router = testRouter();
    router.beforeEach(platformAdminGuard);
    await router.push('/-/privacy');

    expect(router.currentRoute.value.name).toBe('privacy');
  });
});
