/**
 * Shared open triggers for the fixed "Nieuw" button in the toolbar. The button
 * lives in the app shell, the sheets it opens live in the page below it (a
 * child of router-view); those two are not in a parent-child relation, so a
 * prop cannot carry the signal.
 *
 * More than one page can serve the same action: both the overview and the
 * group page open the publish sheet. Whoever is watching handles it.
 *
 * Counters rather than booleans: choosing the same action twice has to fire
 * twice, and bumping a counter is always a change a watcher sees, even when
 * the previous sheet has since closed again.
 *
 * The button is on every route, the sheets are not: only a page watching the
 * counter can carry out the action. So this module tracks who is watching and
 * routes a request nobody here watches to the route that does have a sheet for
 * it. Disabling the menu item would be the alternative, but the NLDD design
 * guideline rejects disabled buttons: let the action proceed and give the
 * feedback in the current view or on the next one.
 */
import { getCurrentInstance, getCurrentScope, nextTick, onScopeDispose, ref, type Ref } from 'vue';
import type { Router } from 'vue-router';

type Action = 'newSite' | 'newGroup';

/**
 * The overview: the only route that can handle both actions. The group page
 * watches `newSite` only and subscribes to it itself, so that a request
 * there is handled on the spot instead of via a detour past the overview.
 */
const HANDLER_ROUTE = { name: 'overview' } as const;

const counters: Record<Action, Ref<number>> = {
  newSite: ref(0),
  newGroup: ref(0),
};
const watchers: Record<Action, number> = { newSite: 0, newGroup: 0 };

let router: Router | null = null;
/** A request in transit to the page that can handle it. */
let inFlight: { action: Action; path: string; release: () => void } | null = null;

/**
 * The router comes from the app context of the component calling the
 * composable: the menu item requests the action from an event handler, where
 * there is no setup context left for `useRouter()` to reach.
 */
function rememberRouter(): void {
  if (router) return;
  const instance = getCurrentInstance();
  if (!instance) return;
  router = (instance.appContext.config.globalProperties as { $router?: Router }).$router ?? null;
}

/**
 * Reading the counter IS subscribing as a watcher: a page that destructures it
 * also watches it. Only inside a component setup, because only there can the
 * unsubscribe be tied to a lifetime; the shell destructures just the request
 * functions and therefore does not count as a watcher.
 */
function watchFor(action: Action): Ref<number> {
  if (getCurrentScope()) {
    watchers[action] += 1;
    onScopeDispose(() => {
      watchers[action] = Math.max(0, watchers[action] - 1);
    });
    // After this setup, so that this page's watcher sees the bump.
    if (inFlight?.action === action) void nextTick(deliver);
  }
  return counters[action];
}

function forgetInFlight(): void {
  inFlight?.release();
  inFlight = null;
}

function deliver(): void {
  if (!inFlight || watchers[inFlight.action] === 0) return;
  // If the navigation did not land (a guard redirected elsewhere), this page
  // is not the one that should receive the request.
  if (router && router.currentRoute.value.fullPath !== inFlight.path) {
    forgetInFlight();
    return;
  }
  const action = inFlight.action;
  forgetInFlight();
  counters[action].value += 1;
}

/** The handling route, or undefined when this app has none. */
function handlerRoute() {
  try {
    return router?.resolve(HANDLER_ROUTE);
  } catch {
    return undefined;
  }
}

function request(action: Action): void {
  forgetInFlight();
  if (watchers[action] > 0) {
    counters[action].value += 1;
    return;
  }

  const target = handlerRoute();
  if (!router || !target) {
    // Nowhere to send it: fire here instead, so that a page that does listen
    // but could not subscribe still gets the action.
    counters[action].value += 1;
    return;
  }

  // The request belongs to this one navigation: if the user clicks somewhere
  // else in the meantime, it lapses. Otherwise the sheet would later spring
  // open out of nowhere on a page that never asked for it.
  const release = router.afterEach((to) => {
    if (to.fullPath !== target.fullPath) forgetInFlight();
  });
  inFlight = { action, path: target.fullPath, release };
  void router.push(target).catch(() => forgetInFlight());
}

export function useAddActions() {
  rememberRouter();
  return {
    get newSite(): Ref<number> {
      return watchFor('newSite');
    },
    get newGroup(): Ref<number> {
      return watchFor('newGroup');
    },
    requestNewSite: (): void => request('newSite'),
    requestNewGroup: (): void => request('newGroup'),
  };
}

/** For tests only: reset the counters, the watchers and the router. */
export function _resetAddActions(): void {
  counters.newSite.value = 0;
  counters.newGroup.value = 0;
  watchers.newSite = 0;
  watchers.newGroup = 0;
  forgetInFlight();
  router = null;
}
