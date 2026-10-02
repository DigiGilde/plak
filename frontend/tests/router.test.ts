import '@nldd/design-system';

import { flushPromises, mount } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createMemoryHistory, createRouter, RouterView } from 'vue-router';

import { _resetCurrentMemberCache } from '../src/composables/currentMember';
import { makeMockBackend, type MockBackend } from '../src/api/mock';
import { platformAdminGuard, routes, scrollBehavior } from '../src/router';
import router from '../src/router';

let backend: MockBackend;

beforeEach(() => {
  backend = makeMockBackend();
  vi.stubGlobal('fetch', backend.fetch);
});

afterEach(() => {
  vi.unstubAllGlobals();
  _resetCurrentMemberCache();
  document.body.innerHTML = '';
});

/**
 * Every configured route, lazy-loaded component included: pushing alone (as
 * router-guard.test.ts does for the admin gate) resolves the match but never
 * calls the `() => import(...)` loader, only actually rendering a
 * `<router-view>` does. Memory history rather than the default export's
 * `createWebHistory` singleton, for the same jsdom reason as
 * router-guard.test.ts.
 */
function mountRouterView() {
  const testRouter = createRouter({ history: createMemoryHistory(), routes });
  testRouter.beforeEach(platformAdminGuard);
  const wrapper = mount(RouterView, { global: { plugins: [testRouter] } });
  return { wrapper, testRouter };
}

describe('router: every configured route resolves and mounts its component', () => {
  it.each([
    ['/', 'overview'],
    ['/-/groups', 'groups'],
    ['/-/privacy', 'privacy'],
    ['/-/accessibility', 'accessibility'],
    ['/-/about', 'about'],
    ['/-/whats-new', 'whats-new'],
    ['/-/profile', 'profile'],
    ['/-/sessions', 'sessions'],
    ['/cli-link', 'cli-link'],
    ['/team-aurora', 'group-sites'],
    ['/team-aurora/-/members', 'group-members'],
    ['/team-aurora/-/settings', 'group-settings'],
    ['/team-aurora/website/done', 'site-done'],
    ['/team-aurora/website', 'site-overview'],
    ['/team-aurora/website/previews', 'site-previews'],
    ['/team-aurora/website/versions', 'site-versions'],
    ['/team-aurora/website/access', 'site-access'],
    ['/team-aurora/website/members', 'site-members'],
    ['/team-aurora/website/deploy', 'site-deploy'],
  ])('mounts %s as %s', async (path, name) => {
    const { wrapper, testRouter } = mountRouterView();
    await testRouter.push(path);
    await flushPromises();

    expect(testRouter.currentRoute.value.name).toBe(name);
    wrapper.unmount();
  });

  it('lets a platform admin reach the platform route', async () => {
    const { wrapper, testRouter } = mountRouterView();
    await testRouter.push('/-/members');
    await flushPromises();

    expect(testRouter.currentRoute.value.name).toBe('members');
    wrapper.unmount();
  });
});

describe('router: document title', () => {
  it('sets the title from the route meta on a page that has one', async () => {
    await router.push('/-/privacy');

    expect(document.title).toBe('Privacy - Plak');
  });

  it('falls back to the site slug for a route without a titleKey', async () => {
    await router.push('/team-aurora/website');

    expect(document.title).toBe('website - Plak');
  });

  it('falls back to the group slug for a group route without a titleKey', async () => {
    await router.push('/team-aurora');

    expect(document.title).toBe('team-aurora - Plak');
  });
});

describe('router: scrollBehavior', () => {
  const call = (hash: string) =>
    scrollBehavior({ hash } as never, {} as never, null);

  it('scrolls to the hash target when there is one', () => {
    expect(call('#d2026-10-01')).toEqual({ el: '#d2026-10-01' });
  });

  it('leaves every other navigation to the default', () => {
    expect(call('')).toBeUndefined();
  });
});
