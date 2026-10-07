/**
 * Keeps the current tab of a tab bar in view. The bar scrolls sideways inside
 * its wrapper (the `.tabs-scroll` of global.css) rather than squeezing the
 * labels of its tabs, so on a phone the tab you are on can sit past the edge
 * after loading the page by its address or after a route change.
 *
 * `wrapper` is the template ref of that wrapper. The tab is found by its
 * position, not by `current`: the design system reflects that attribute a
 * render later, and the position is the order the page renders its tabs in
 * anyway. The page has no bar until it has loaded, so the wrapper appearing
 * counts as a change too. A tab that is in view already stays where it is.
 *
 * The scroll waits for the next frame. The design system renders the bar and
 * its tabs in the microtasks after they are inserted, and a tab that has not
 * rendered yet has no width, so scrolling to it at once lands nowhere.
 *
 * The wrapper is scrolled itself, not through `scrollIntoView`: that scrolls
 * every box between the tab and the window, the page up and down included.
 */
import { type Ref, watch } from 'vue';

export function useCurrentTabInView(
  wrapper: Readonly<Ref<HTMLElement | null>>,
  currentIndex: () => number,
): void {
  watch(
    [currentIndex, wrapper],
    () => {
      requestAnimationFrame(() => {
        const box = wrapper.value;
        const tab = box?.querySelectorAll('nldd-tab-bar-item')[currentIndex()];
        if (!box || !tab) return;
        const boxRect = box.getBoundingClientRect();
        const tabRect = tab.getBoundingClientRect();
        const start = tabRect.left - boxRect.left + box.scrollLeft;
        const end = start + tabRect.width - box.clientWidth;
        box.scrollLeft = Math.min(Math.max(box.scrollLeft, Math.ceil(end)), Math.floor(start));
      });
    },
    { flush: 'post' },
  );
}
