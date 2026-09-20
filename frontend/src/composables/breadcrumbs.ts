/**
 * Breadcrumb trail shared between the page and the app shell. The NLDD
 * guideline wants breadcrumbs as a supporting interaction at the bottom (the
 * `breadcrumbs` slot of `nldd-page-footer`), but only the page knows its own
 * crumbs, and those two are not in a parent-child relation, so a prop cannot
 * carry them.
 *
 * The crumbs are stored together with the route path they belong to, and
 * App.vue only shows them while that path is the current route. That way a
 * trail from a page you have left can never linger, whatever the order in
 * which components mount and unmount.
 */
import { ref } from 'vue';

import { t } from '@/i18n';

export interface Crumb {
  text: string;
  /** Leave empty for the current page: that crumb is not a link. */
  href?: string;
}

// Functions, not constants: a constant is built once at import, which would
// freeze the crumb in whatever language was current then.

/** The overview is the root of every breadcrumb trail in the admin SPA. */
export function crumbOverview(): Crumb {
  return { text: t('nav.overview'), href: '/' };
}

/** Breadcrumb trail of the groups page; the individual group pages hang below it. */
export function crumbsGroups(): Crumb[] {
  return [crumbOverview(), { text: t('nav.groups') }];
}

const path = ref('');
const crumbs = ref<Crumb[]>([]);

export function setBreadcrumbs(routePath: string, items: Crumb[]): void {
  path.value = routePath;
  crumbs.value = items;
}

export function breadcrumbsFor(routePath: string): Crumb[] {
  return path.value === routePath ? crumbs.value : [];
}

/** For tests only: forget the breadcrumb trail that was set. */
export function _resetBreadcrumbs(): void {
  path.value = '';
  crumbs.value = [];
}
