import { mount } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { defineComponent, h, nextTick, ref } from 'vue';

import { fakeTabBarLayout, stubFrames } from '@/components/site/testHelpers';
import { useCurrentTabInView } from './currentTabInView';

/**
 * A bar of three tabs in the wrapper the page keeps, like the site and group
 * pages build it. `shown` stands for the page that has not loaded yet.
 */
const Bar = defineComponent({
  props: {
    current: { type: Number, required: true },
    shown: { type: Boolean, default: true },
  },
  setup(props) {
    const wrapper = ref<HTMLElement | null>(null);
    useCurrentTabInView(wrapper, () => props.current);
    return () =>
      props.shown
        ? h('div', { ref: wrapper, class: 'tabs-scroll' }, [
            h('nldd-tab-bar', [
              h('nldd-tab-bar-item', { text: 'een' }),
              h('nldd-tab-bar-item', { text: 'twee' }),
              h('nldd-tab-bar-item', { text: 'drie' }),
            ]),
          ])
        : h('p', 'laden');
  },
});

// Three tabs of 40 in a bar that shows 100: the third one sticks out.
const LAYOUT = { barWidth: 100, tabWidth: 40 };

let frames: ReturnType<typeof stubFrames>;

beforeEach(() => {
  frames = stubFrames();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function scroller(wrapper: ReturnType<typeof mount>): HTMLElement {
  return wrapper.find('.tabs-scroll').element as HTMLElement;
}

describe('useCurrentTabInView', () => {
  it('scrolls the bar just far enough to show the current tab whole', async () => {
    const wrapper = mount(Bar, { props: { current: 2 } });
    await nextTick();
    fakeTabBarLayout(scroller(wrapper), LAYOUT);

    frames.run();

    // The third tab ends at 120 and the bar shows 100.
    expect(scroller(wrapper).scrollLeft).toBe(20);
  });

  it('waits for the next frame, when the design system has rendered the tabs', async () => {
    const wrapper = mount(Bar, { props: { current: 2 } });
    await nextTick();
    fakeTabBarLayout(scroller(wrapper), LAYOUT);

    // A tab that has not rendered has no width: scrolling to it lands nowhere.
    expect(scroller(wrapper).scrollLeft).toBe(0);

    frames.run();

    expect(scroller(wrapper).scrollLeft).toBe(20);
  });

  it('follows the current tab back to the start', async () => {
    const wrapper = mount(Bar, { props: { current: 2 } });
    await nextTick();
    fakeTabBarLayout(scroller(wrapper), LAYOUT);
    frames.run();

    await wrapper.setProps({ current: 0 });
    frames.run();

    expect(scroller(wrapper).scrollLeft).toBe(0);
  });

  it('leaves a tab that is in view where it is', async () => {
    const wrapper = mount(Bar, { props: { current: 1 } });
    await nextTick();
    fakeTabBarLayout(scroller(wrapper), LAYOUT);
    scroller(wrapper).scrollLeft = 10;

    frames.run();

    // The second tab spans 40 to 80, inside the 10 to 110 the bar shows.
    expect(scroller(wrapper).scrollLeft).toBe(10);
  });

  it('rounds towards showing the tab whole, as the browser keeps the scroll position in whole pixels', async () => {
    const wrapper = mount(Bar, { props: { current: 2 } });
    await nextTick();
    fakeTabBarLayout(scroller(wrapper), { barWidth: 100, tabWidth: 40.5 });

    frames.run();

    // The third tab ends at 121.5 and the bar shows 100: 21.5 is a sliver short.
    expect(scroller(wrapper).scrollLeft).toBe(22);

    await wrapper.setProps({ current: 1 });
    scroller(wrapper).scrollLeft = 60;
    frames.run();

    // The second tab starts at 40.5: 40 shows it whole, 41 would cut a sliver off.
    expect(scroller(wrapper).scrollLeft).toBe(40);
  });

  it('starts a tab that is wider than the bar at its left edge', async () => {
    const wrapper = mount(Bar, { props: { current: 1 } });
    await nextTick();
    fakeTabBarLayout(scroller(wrapper), { barWidth: 30, tabWidth: 40 });

    frames.run();

    expect(scroller(wrapper).scrollLeft).toBe(40);
  });

  it('scrolls the bar and nothing else, so the page stays where it is', async () => {
    const scrollIntoView = vi.fn();
    Element.prototype.scrollIntoView = scrollIntoView;
    try {
      const wrapper = mount(Bar, { props: { current: 2 } });
      await nextTick();
      fakeTabBarLayout(scroller(wrapper), LAYOUT);

      frames.run();

      expect(scroller(wrapper).scrollLeft).toBe(20);
      expect(scrollIntoView).not.toHaveBeenCalled();
    } finally {
      // @ts-expect-error jsdom has no scrollIntoView: back to how it was.
      delete Element.prototype.scrollIntoView;
    }
  });

  it('scrolls to the tab that is current by the time the frame comes', async () => {
    const wrapper = mount(Bar, { props: { current: 2 } });
    await nextTick();
    await wrapper.setProps({ current: 1 });
    fakeTabBarLayout(scroller(wrapper), { barWidth: 50, tabWidth: 40 });

    frames.run();

    // The second tab ends at 80 and the bar shows 50.
    expect(scroller(wrapper).scrollLeft).toBe(30);
  });

  it('does nothing for a route that is none of the tabs', async () => {
    const wrapper = mount(Bar, { props: { current: -1 } });
    await nextTick();
    fakeTabBarLayout(scroller(wrapper), LAYOUT);

    frames.run();

    expect(scroller(wrapper).scrollLeft).toBe(0);
  });

  it('waits for the bar, and scrolls when it appears', async () => {
    const wrapper = mount(Bar, { props: { current: 2, shown: false } });
    await nextTick();
    frames.run();

    await wrapper.setProps({ shown: true });
    fakeTabBarLayout(scroller(wrapper), LAYOUT);
    frames.run();

    expect(scroller(wrapper).scrollLeft).toBe(20);
  });

  it('does nothing when the bar has gone again by the time the frame comes', async () => {
    const wrapper = mount(Bar, { props: { current: 1 } });
    await nextTick();
    await wrapper.setProps({ shown: false });

    expect(() => frames.run()).not.toThrow();
    expect(wrapper.find('.tabs-scroll').exists()).toBe(false);
  });
});
