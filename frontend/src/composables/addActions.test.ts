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
    await mountComponent('/team-aurora');

    requestNewSite();
    await flushPromises();

    expect(router.currentRoute.value.path).toBe('/');
    expect(opened).toEqual(['site']);
  });

  it('waits for the page that only watches the counter after its session check', async () => {
    await mountComponent('/team-aurora', StartPage);

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
    await mountComponent('/team-aurora', StartPage);

    requestNewSite();
    await flushPromises();
    await router.push('/team-aurora');
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
    await router.push('/team-aurora');
    wrapper = mount(Shell, { global: { plugins: [router] } });
    await flushPromises();

    requestNewSite();
    await flushPromises();

    expect(useAddActions().newSite.value).toBe(1);
    expect(router.currentRoute.value.path).toBe('/team-aurora');
  });

  it('lets the request drop silently when the navigation itself fails', async () => {
    router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', name: 'overview', component: OverviewPage },
        { path: '/:group', name: 'group', component: QuietPage },
      ],
    });
    router.beforeEach((to) => {
      if (to.name === 'overview') throw new Error('navigatie mislukt');
    });
    await router.push('/team-aurora');
    wrapper = mount(Shell, { global: { plugins: [router] } });
    await flushPromises();

    expect(() => requestNewSite()).not.toThrow();
    await flushPromises();

    expect(opened).toEqual([]);
    expect(router.currentRoute.value.path).toBe('/team-aurora');
  });

  it('no longer counts a page that is gone as a watcher', async () => {
    await mountComponent('/');
    await router.push('/team-aurora');
    await flushPromises();

    requestNewSite();
    await flushPromises();

    expect(router.currentRoute.value.path).toBe('/');
    expect(opened).toEqual(['site']);
  });

  it('delivers only once to a route that mounts two watchers of the same action', async () => {
    // Both watch in the same setup pass, so both see the in-flight request and
    // schedule their own delivery; only the first may actually fire it. Both
    // still observe the single resulting bump of the shared counter, so
    // `opened` (one push per watcher) is not the signal here; the counter
    // itself is.
    const DoubleOverview = defineComponent({
      setup: () => () => h('div', [h(OverviewPage), h(OverviewPage)]),
    });
    await mountComponent('/team-aurora', DoubleOverview);

    requestNewSite();
    await flushPromises();

    expect(router.currentRoute.value.path).toBe('/');
    expect(useAddActions().newSite.value).toBe(1);
  });

  it('does not remember a router when the component has none installed', async () => {
    let actions!: ReturnType<typeof useAddActions>;
    const Bare = defineComponent({
      setup() {
        actions = useAddActions();
        return () => h('div');
      },
    });
    const bareWrapper = mount(Bare);

    expect(() => actions.requestNewSite()).not.toThrow();
    expect(actions.newSite.value).toBe(1);

    bareWrapper.unmount();
  });

  it('does not remember a router when called outside a component setup', () => {
    // rememberRouter() reads the app context off the current component
    // instance; called from a plain function there is none, and it must not
    // throw or misbehave, only skip.
    const actions = useAddActions();

    expect(() => actions.requestNewSite()).not.toThrow();
    // Nowhere to send it (no router, so no handling route either): fires
    // right here instead of leaving the request stranded.
    expect(actions.newSite.value).toBe(1);
  });
});
