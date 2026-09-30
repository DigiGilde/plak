import { describe, expect, it } from 'vitest';
import { createMemoryHistory, createRouter, createWebHistory, type RouterHistory } from 'vue-router';

import { goToDone, takePublishedMark } from './publishedMark';

function makeRouter(history: RouterHistory) {
  const Empty = { template: '<div />' };
  return createRouter({
    history,
    routes: [
      { path: '/', component: Empty },
      { path: '/:group/:site', component: Empty },
      { path: '/:group/:site/done', component: Empty },
    ],
  });
}

describe('publishedMark', () => {
  it('marks the navigation the publish flow makes, and only that one', async () => {
    const router = makeRouter(createMemoryHistory());
    await router.push('/');

    await router.push('/team-aurora/website/done');
    expect(takePublishedMark(router)).toBe(false);

    await router.push('/team-aurora/website');
    await goToDone(router, 'team-aurora', 'website');
    expect(router.currentRoute.value.fullPath).toBe('/team-aurora/website/done');
    expect(takePublishedMark(router)).toBe(true);
  });

  it('is taken once: asking again finds no mark, at the same address', async () => {
    const router = makeRouter(createMemoryHistory());
    await router.push('/');
    await goToDone(router, 'team-aurora', 'website');

    expect(takePublishedMark(router)).toBe(true);
    expect(takePublishedMark(router)).toBe(false);
    expect(router.options.history.location).toBe('/team-aurora/website/done');
  });

  it('clears the mark in the browser history entry, so a reload does not carry it', async () => {
    window.history.replaceState(null, '', '/');
    const router = makeRouter(createWebHistory());
    await router.push('/');
    await goToDone(router, 'team-aurora', 'website');

    expect(window.history.state.plakJustPublished).toBe(true);
    expect(takePublishedMark(router)).toBe(true);
    expect(window.location.pathname).toBe('/team-aurora/website/done');
    expect(window.history.state.plakJustPublished).toBe(false);

    // A reload builds a new router on the same history entry.
    const reloaded = makeRouter(createWebHistory());
    await reloaded.push(window.location.pathname);
    expect(takePublishedMark(reloaded)).toBe(false);
  });
});
