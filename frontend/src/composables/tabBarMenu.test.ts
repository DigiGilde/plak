import { mount } from '@vue/test-utils';
import { describe, expect, it } from 'vitest';
import { defineComponent, h } from 'vue';
import { createMemoryHistory, createRouter } from 'vue-router';

import { useTabBarMenuInApp } from './tabBarMenu';

const Empty = defineComponent({ render: () => null });

async function mountBar() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/team-aurora/website', component: Empty },
      { path: '/team-aurora/website/versions', component: Empty },
    ],
  });
  await router.push('/team-aurora/website');
  const Bar = defineComponent({
    setup() {
      const onClick = useTabBarMenuInApp();
      return () =>
        h('nav', { onClick }, [
          h('nldd-menu-item', { href: '/team-aurora/website/versions' }, [h('a', { href: '/team-aurora/website/versions' })]),
          h('nldd-menu-item', { 'data-testid': 'action' }, [h('button')]),
          h('a', { href: '/team-aurora/website/versions', 'data-testid': 'outside' }),
        ]);
    },
  });
  const wrapper = mount(Bar, { global: { plugins: [router] }, attachTo: document.body });
  return { wrapper, router };
}

function click(target: Element, init: MouseEventInit = {}): MouseEvent {
  const event = new MouseEvent('click', { bubbles: true, cancelable: true, composed: true, ...init });
  target.dispatchEvent(event);
  return event;
}

describe('useTabBarMenuInApp', () => {
  it('takes a tab chosen from the menu into the router instead of a page load', async () => {
    const { wrapper, router } = await mountBar();

    const event = click(wrapper.find('nldd-menu-item a').element);
    await router.isReady();
    await new Promise((resolve) => setTimeout(resolve));

    expect(event.defaultPrevented).toBe(true);
    expect(router.currentRoute.value.path).toBe('/team-aurora/website/versions');
    wrapper.unmount();
  });

  it('leaves a click with a modifier or another button to the browser', async () => {
    const { wrapper, router } = await mountBar();
    const link = wrapper.find('nldd-menu-item a').element;

    for (const init of [{ metaKey: true }, { ctrlKey: true }, { shiftKey: true }, { altKey: true }, { button: 1 }]) {
      const event = click(link, init);
      expect(event.defaultPrevented, JSON.stringify(init)).toBe(false);
    }
    await new Promise((resolve) => setTimeout(resolve));
    expect(router.currentRoute.value.path).toBe('/team-aurora/website');
    wrapper.unmount();
  });

  it('leaves a click someone else already handled alone', async () => {
    const { wrapper, router } = await mountBar();
    const link = wrapper.find('nldd-menu-item a').element;
    link.addEventListener('click', (event) => event.preventDefault());

    click(link);
    await new Promise((resolve) => setTimeout(resolve));

    expect(router.currentRoute.value.path).toBe('/team-aurora/website');
    wrapper.unmount();
  });

  it('ignores a menu item without an href and a link outside the menu', async () => {
    const { wrapper, router } = await mountBar();

    const onAction = click(wrapper.find('[data-testid="action"] button').element);
    const onOutside = click(wrapper.find('[data-testid="outside"]').element);
    await new Promise((resolve) => setTimeout(resolve));

    expect(onAction.defaultPrevented).toBe(false);
    expect(onOutside.defaultPrevented).toBe(false);
    expect(router.currentRoute.value.path).toBe('/team-aurora/website');
    wrapper.unmount();
  });
});
