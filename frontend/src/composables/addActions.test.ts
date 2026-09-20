import { flushPromises, mount } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { defineComponent, h, ref, watch } from 'vue';
import { createMemoryHistory, createRouter, RouterView, type Router } from 'vue-router';

import { _resetAddActions, useAddActions } from './addActions';

/** What the pages opened, in order: the proof that an action arrived. */
let opened: string[] = [];
let requestNewSite: () => void;
let requestNewGroup: () => void;

/** The overview: watches both counters and opens its own sheets. */
const OverviewPage = defineComponent({
  setup() {
    const { newSite, newGroup } = useAddActions();
    watch(newSite, () => opened.push('site'));
    watch(newGroup, () => opened.push('group'));
    return () => h('div');
  },
});

/** A page without sheets, like the group and site pages. */
const QuietPage = defineComponent({
  setup: () => () => h('div'),
});

/**
 * The overview behind a session check, like Start.vue: the page watching the
 * counters only appears once that check is done.
 */
let allowOverview: () => void;
const StartPage = defineComponent({
  setup() {
    const ready = ref(false);
    allowOverview = () => {
      ready.value = true;
    };
    return () => (ready.value ? h(OverviewPage) : h('div'));
  },
});

/** The app shell: has the button, but no sheet. */
const Shell = defineComponent({
  setup() {
    ({ requestNewSite, requestNewGroup } = useAddActions());
    return () => h(RouterView);
  },
});

let wrapper: ReturnType<typeof mount> | null = null;
let router: Router;

async function mountComponent(path: string, overview = OverviewPage): Promise<void> {
  router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'overview', component: overview },
      { path: '/:group', name: 'group', component: QuietPage },
    ],
  });
  await router.push(path);
  await router.isReady();
  wrapper = mount(Shell, { global: { plugins: [router] } });
  await flushPromises();
}

beforeEach(() => {
  opened = [];
  _resetAddActions();
});

afterEach(() => {
  wrapper?.unmount();
  wrapper = null;
});

describe('add actions', () => {
  it('opens the sheet right there on a page that watches the counter', async () => {
    await mountComponent('/');

    requestNewSite();
    await flushPromises();

    expect(opened).toEqual(['site']);
    expect(router.currentRoute.value.path).toBe('/');
  });

  it('fires twice when the same action is requested twice', async () => {
    await mountComponent('/');

    requestNewGroup();
    await flushPromises();
    requestNewGroup();
    await flushPromises();

    expect(opened).toEqual(['group', 'group']);
  });

  it('sends a request nobody is watching to the route that handles it', async () => {
    await mountComponent('/nldd');

    requestNewSite();
    await flushPromises();

    expect(router.currentRoute.value.path).toBe('/');
    expect(opened).toEqual(['site']);
  });

  it('waits for the page that only watches the counter after its session check', async () => {
    await mountComponent('/nldd', StartPage);

    requestNewGroup();
    await flushPromises();

    expect(router.currentRoute.value.path).toBe('/');
    expect(opened).toEqual([]);

    allowOverview();
    await flushPromises();

    expect(opened).toEqual(['group']);
  });

  it('also waits for that page when the user is already on the handling route', async () => {
    await mountComponent('/', StartPage);

    requestNewSite();
    await flushPromises();
    expect(opened).toEqual([]);

    allowOverview();
    await flushPromises();

    expect(opened).toEqual(['site']);
  });

  it('lets the request lapse once the user clicks somewhere else', async () => {
    await mountComponent('/nldd', StartPage);

    requestNewSite();
    await flushPromises();
    await router.push('/nldd');
    await flushPromises();

    // Back on the overview, with the session check done immediately: no sheet
    // springing open out of nowhere.
    await router.push('/');
    allowOverview();
    await flushPromises();

    expect(opened).toEqual([]);
  });

  it('fires right there when there is no route to send the request to', async () => {
    router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/:group', name: 'group', component: QuietPage }],
    });
    await router.push('/nldd');
    wrapper = mount(Shell, { global: { plugins: [router] } });
    await flushPromises();

    requestNewSite();
    await flushPromises();

    expect(useAddActions().newSite.value).toBe(1);
    expect(router.currentRoute.value.path).toBe('/nldd');
  });

  it('no longer counts a page that is gone as a watcher', async () => {
    await mountComponent('/');
    await router.push('/nldd');
    await flushPromises();

    requestNewSite();
    await flushPromises();

    expect(router.currentRoute.value.path).toBe('/');
    expect(opened).toEqual(['site']);
  });
});
