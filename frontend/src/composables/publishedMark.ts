/**
 * The mark the publish flow leaves on its navigation to the result screen
 * (`/{group}/{site}/done`). Done.vue only makes a secret link for a visit that
 * carries it: merely opening that route, from a link on another site for
 * instance, must not re-create a key an admin has just revoked.
 *
 * It lives in the history entry's state, which only this origin can write.
 */
import type { Router } from 'vue-router';

const MARK = 'plakJustPublished';

/** To the result screen, as the publish flow that just put this site online. */
export function goToDone(router: Router, group: string, site: string): Promise<unknown> {
  return router.push({ path: `/${group}/${site}/done`, state: { [MARK]: true } });
}

/**
 * Whether the current visit carries the mark. Taking it clears it, so a reload
 * or a return through the back button counts as an ordinary visit.
 */
export function takePublishedMark(router: Router): boolean {
  const history = router.options.history;
  if (history.state[MARK] !== true) return false;
  history.replace(history.location, { ...history.state, [MARK]: false });
  return true;
}
