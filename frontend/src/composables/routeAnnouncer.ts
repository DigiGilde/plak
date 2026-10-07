import { nextTick, onBeforeUnmount, ref } from 'vue';
import {
  START_LOCATION,
  type RouteLocationNormalized,
  type Router,
} from 'vue-router';

/** The page's own heading: where focus goes after a navigation to another page. */
const PAGE_HEADING = '#main-content h1';
/** The content below a tab bar: where focus goes when only the tab changed. */
const TAB_PANEL = '#main-content [data-tab-panel]';

/**
 * A change of tab keeps the page (site or group) and swaps only the child
 * route, so the heading and tab bar stay and only the panel is new.
 */
function isTabChange(to: RouteLocationNormalized, from: RouteLocationNormalized): boolean {
  return (
    to.matched.length > 1 &&
    to.matched[0] === from.matched[0] &&
    to.params.group === from.params.group &&
    to.params.site === from.params.site
  );
}

/**
 * Runs `found` with the element as soon as it is in the page; the page may
 * still be loading, and its heading only appears with the data. Returns what
 * stops the waiting.
 */
function whenPresent(selector: string, found: (element: HTMLElement) => void): () => void {
  const now = document.querySelector<HTMLElement>(selector);
  if (now) {
    found(now);
    return () => undefined;
  }
  const observer = new MutationObserver(() => {
    const element = document.querySelector<HTMLElement>(selector);
    if (!element) return;
    observer.disconnect();
    found(element);
  });
  observer.observe(document.body, { childList: true, subtree: true });
  return () => observer.disconnect();
}

/**
 * Makes a navigation inside the SPA noticeable without sight (WCAG 2.4.3 and
 * 4.1.3): without this, focus stays on the link that was clicked, which has
 * just left the page, and a screen reader says nothing about where it landed.
 * After each navigation but the first load, focus moves to the new page's
 * heading (to the tab panel when only the tab changed) and the title of the
 * page goes into the returned message, which belongs in a `role="status"`
 * region.
 */
export function useRouteAnnouncer(router: Router) {
  const message = ref('');
  let stopWaiting: () => void = () => undefined;

  function arrive(element: HTMLElement): void {
    // A heading or panel is no control: it takes focus only from a script.
    element.setAttribute('tabindex', '-1');
    element.focus();
    message.value = document.title;
  }

  const removeHook = router.afterEach((to, from, failure) => {
    stopWaiting();
    if (failure || from === START_LOCATION) return;
    const selector = isTabChange(to, from) ? TAB_PANEL : PAGE_HEADING;
    // The page is only rendered once the route change has been flushed.
    void nextTick(() => {
      stopWaiting = whenPresent(selector, arrive);
    });
  });

  onBeforeUnmount(() => {
    removeHook();
    stopWaiting();
  });

  return message;
}
