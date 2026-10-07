import { flushPromises, mount } from '@vue/test-utils';
import { afterEach, describe, expect, it } from 'vitest';
import { defineComponent, h, onMounted, ref } from 'vue';
import { createMemoryHistory, createRouter, RouterView, type Router } from 'vue-router';

import { useRouteAnnouncer } from './routeAnnouncer';

let wrapper: ReturnType<typeof mount> | null = null;

afterEach(() => {
  wrapper?.unmount();
  wrapper = null;
  document.body.innerHTML = '';
  document.title = '';
});

const Page = (title: string) =>
  defineComponent({ render: () => h('div', [h('h1', title), h('button', 'iets')]) });

/** A page whose heading only shows up once its "data" has arrived. */
const SlowPage = defineComponent({
  setup() {
    const loaded = ref(false);
    const label = ref('Laden');
    // Something changes in the page before the heading arrives.
    onMounted(() => setTimeout(() => (label.value = 'Nog even'), 10));
    onMounted(() => setTimeout(() => (loaded.value = true), 40));
    return () => h('div', loaded.value ? [h('h1', 'Traag')] : [h('p', label.value)]);
  },
});

const Tab = (name: string) => defineComponent({ render: () => h('p', name) });

const SitePage = defineComponent({
  render: () =>
    h('div', [
      h('h1', 'Een site'),
      h('div', { 'data-tab-panel': '' }, [h(RouterView)]),
    ]),
});

function makeRouter(): Router {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: Page('Overzicht') },
      { path: '/groups', component: Page('Groepen') },
      { path: '/slow', component: SlowPage },
      { path: '/nothing', component: Tab('zonder kop') },
      {
        path: '/:group/:site',
        component: SitePage,
        children: [
          { path: '', component: Tab('overzicht') },
          { path: 'versions', component: Tab('versies') },
        ],
      },
    ],
  });
}

async function mountShell(router: Router, start = '/') {
  await router.push(start);
  await router.isReady();
  const Shell = defineComponent({
    setup() {
      const message = useRouteAnnouncer(router);
      return () =>
        h('div', [
          h('div', { id: 'main-content' }, [h(RouterView)]),
          h('div', { role: 'status', 'data-testid': 'status' }, message.value),
        ]);
    },
  });
  wrapper = mount(Shell, { global: { plugins: [router] }, attachTo: document.body });
  await flushPromises();
  return wrapper;
}

const status = () => document.querySelector('[data-testid="status"]')!.textContent;

describe('useRouteAnnouncer', () => {
  it('moves focus to the heading of the new page and announces its title', async () => {
    const router = makeRouter();
    await mountShell(router);
    document.title = 'Groepen - Plak';

    await router.push('/groups');
    await flushPromises();

    expect(document.activeElement).toBe(document.querySelector('h1'));
    expect(document.activeElement!.textContent).toBe('Groepen');
    expect(document.activeElement!.getAttribute('tabindex')).toBe('-1');
    expect(status()).toBe('Groepen - Plak');
  });

  it('leaves the first load alone', async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/', component: Page('Overzicht') }],
    });
    const Shell = defineComponent({
      setup() {
        const message = useRouteAnnouncer(router);
        return () =>
          h('div', [
            h('div', { id: 'main-content' }, [h(RouterView)]),
            h('div', { role: 'status', 'data-testid': 'status' }, message.value),
          ]);
      },
    });
    wrapper = mount(Shell, { global: { plugins: [router] }, attachTo: document.body });
    await router.isReady();
    await flushPromises();

    expect(document.activeElement).toBe(document.body);
    expect(status()).toBe('');
  });

  it('waits for a heading that only appears once the page has loaded', async () => {
    const router = makeRouter();
    await mountShell(router);
    document.title = 'Traag - Plak';

    await router.push('/slow');
    await flushPromises();
    expect(document.querySelector('h1')).toBeNull();
    expect(status()).toBe('');

    await new Promise((resolve) => setTimeout(resolve, 80));
    await flushPromises();

    expect(document.activeElement).toBe(document.querySelector('h1'));
    expect(status()).toBe('Traag - Plak');
  });

  it('stops waiting for a heading when the visitor navigates on', async () => {
    const router = makeRouter();
    await mountShell(router);

    await router.push('/slow');
    await flushPromises();
    await router.push('/groups');
    await flushPromises();
    await new Promise((resolve) => setTimeout(resolve, 80));
    await flushPromises();

    expect(document.activeElement!.textContent).toBe('Groepen');
  });

  it('moves focus to the tab panel, not the heading, when only the tab changes', async () => {
    const router = makeRouter();
    await mountShell(router, '/team/website');
    document.title = 'Een site - Versies - Plak';

    await router.push('/team/website/versions');
    await flushPromises();

    const panel = document.querySelector('[data-tab-panel]');
    expect(document.activeElement).toBe(panel);
    expect(status()).toBe('Een site - Versies - Plak');
  });

  it('treats another site as another page: focus goes to the heading', async () => {
    const router = makeRouter();
    await mountShell(router, '/team/website');

    await router.push('/team/other');
    await flushPromises();

    expect(document.activeElement).toBe(document.querySelector('h1'));
  });

  it('does nothing when the navigation failed', async () => {
    const router = makeRouter();
    await mountShell(router);

    await router.push('/');
    await flushPromises();

    expect(document.activeElement).toBe(document.body);
    expect(status()).toBe('');
  });

  it('stops listening when the shell goes away', async () => {
    const router = makeRouter();
    const shell = await mountShell(router);
    shell.unmount();
    wrapper = null;

    await router.push('/groups');
    await flushPromises();

    expect(document.activeElement).toBe(document.body);
  });
});
