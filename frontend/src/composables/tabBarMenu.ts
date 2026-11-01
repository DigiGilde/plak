import { useRouter } from 'vue-router';

/**
 * A click handler for a navigation nldd-tab-bar. The tabs that do not fit go
 * behind its "Meer tabs" button, as menu items carrying the tab's href, and
 * the browser follows that href as a full page load: the app starts over and
 * focus lands on the body instead of the tab panel. This takes a plain click
 * on such an item into the router, as the tabs in the bar already go. A click
 * with a modifier or another button keeps its own meaning (a new tab, a new
 * window).
 */
export function useTabBarMenuInApp(): (event: MouseEvent) => void {
  const router = useRouter();
  return (event) => {
    if (event.defaultPrevented || event.button !== 0) return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const item = event
      .composedPath()
      .find((node): node is Element => node instanceof Element && node.localName === 'nldd-menu-item');
    const href = item?.getAttribute('href');
    if (!href) return;
    event.preventDefault();
    void router.push(href);
  };
}
