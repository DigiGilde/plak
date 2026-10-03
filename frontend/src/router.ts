import {
  createRouter,
  createWebHistory,
  type NavigationGuardWithThis,
  type RouterScrollBehavior,
} from 'vue-router';

import { fetchCurrentMember, isPlatformAdmin } from './composables/currentMember';
import { t, type MessageKey } from './i18n';
import { setDocumentTitle } from './title';

export const routes = [
  {
    path: '/',
    name: 'overview',
    component: () => import('./pages/Start.vue'),
    meta: { titleKey: 'nav.overview' },
  },
  // '/-/' is the prefix for pages that are not a group; it is not a valid
  // group slug, so this route can never get in the way of '/:group'.
  {
    path: '/-/groups',
    name: 'groups',
    component: () => import('./pages/Groups.vue'),
    meta: { titleKey: 'nav.groups' },
  },
  {
    path: '/-/platform',
    name: 'platform',
    component: () => import('./pages/Members.vue'),
    meta: { platformAdminRequired: true, titleKey: 'nav.platform' },
  },
  {
    path: '/-/privacy',
    name: 'privacy',
    component: () => import('./pages/Privacy.vue'),
    meta: { public: true, titleKey: 'footer.privacy' },
  },
  {
    path: '/-/accessibility',
    name: 'accessibility',
    component: () => import('./pages/Accessibility.vue'),
    meta: { public: true, titleKey: 'footer.accessibility' },
  },
  {
    path: '/-/about',
    name: 'about',
    component: () => import('./pages/About.vue'),
    meta: { public: true, titleKey: 'footer.about' },
  },
  {
    path: '/-/whats-new',
    name: 'whats-new',
    component: () => import('./pages/WhatsNew.vue'),
    meta: { public: true, titleKey: 'page.whatsNew.title' },
  },
  {
    path: '/-/profile',
    name: 'profile',
    component: () => import('./pages/Profiel.vue'),
    meta: { titleKey: 'profile.title' },
  },
  {
    path: '/-/sessions',
    name: 'sessions',
    component: () => import('./pages/Sessions.vue'),
    meta: { titleKey: 'nav.sessions' },
  },
  // Above '/:group': the CLI's device flow opens this path directly (not
  // under '/-/'), so it has to be matched before the group route below.
  {
    path: '/cli-link',
    name: 'cli-link',
    component: () => import('./pages/CliKoppelen.vue'),
    meta: { public: true, titleKey: 'page.cliPair.title' },
  },
  // The group page's tabs live under the "-" segment, like the platform
  // pages: per SLUG_RE (backend/constants.py) "-" can never be a site
  // slug, so /:group/-/members shadows no site named "members". The first tab
  // is the empty path, so that /{group} itself lands on Sites;
  // that is why the parent carries no name.
  {
    path: '/:group',
    component: () => import('./pages/Group.vue'),
    children: [
      {
        path: '',
        name: 'group-sites',
        component: () => import('./components/group/TabSites.vue'),
      },
      {
        path: '-/members',
        name: 'group-members',
        component: () => import('./components/group/TabMembers.vue'),
      },
      {
        path: '-/settings',
        name: 'group-settings',
        component: () => import('./components/group/TabSettings.vue'),
      },
    ],
  },
  // The result screen of "Zet een site online" deliberately sits beside the
  // site page rather than as a tab inside it: it is the answer to the
  // task, not the management screen that follows. Its own route makes it
  // reloadable and shareable.
  {
    path: '/:group/:site/done',
    name: 'site-done',
    component: () => import('./pages/Done.vue'),
    meta: { titleKey: 'page.done.title' },
  },
  {
    path: '/:group/:site',
    component: () => import('./pages/Site.vue'),
    children: [
      {
        path: '',
        name: 'site-overview',
        component: () => import('./components/site/TabOverview.vue'),
      },
      {
        path: 'previews',
        name: 'site-previews',
        component: () => import('./components/site/TabPreviews.vue'),
      },
      {
        path: 'versions',
        name: 'site-versions',
        component: () => import('./components/site/TabVersions.vue'),
      },
      {
        path: 'access',
        name: 'site-access',
        component: () => import('./components/site/TabAccess.vue'),
      },
      {
        path: 'members',
        name: 'site-members',
        component: () => import('./components/site/TabMembers.vue'),
      },
      {
        path: 'deploy',
        name: 'site-deploy',
        component: () => import('./components/site/TabDeploy.vue'),
      },
    ],
  },
];

// Route guard: '/-/platform' is platform administration, non-admins
// do not get in. The platform pages are explicitly public (no login needed);
// for the rest, the session-dependent UI (Start.vue, the site pages)
// decides for itself what to show. Exported separately (rather than only as a
// `router.beforeEach` callback) so a test can mount it on a memory-history
// router without needing the production singleton (createWebHistory).
export const platformAdminGuard: NavigationGuardWithThis<undefined> = async (to) => {
  if (!to.meta.platformAdminRequired) {
    return true;
  }
  // An unexpected (non-401) error while resolving the session counts as "not
  // a platform admin" here: deny by default, rather than letting navigation
  // hang on a network hiccup.
  const member = await fetchCurrentMember().catch(() => null);
  if (!isPlatformAdmin(member)) {
    return { name: 'overview' };
  }
  return true;
};

/** A link with a hash (the footer's version) scrolls to its target; every other navigation keeps the default. */
export const scrollBehavior: RouterScrollBehavior = (to) => (to.hash ? { el: to.hash } : undefined);

const router = createRouter({
  history: createWebHistory('/'),
  routes,
  scrollBehavior,
});

router.beforeEach(platformAdminGuard);

/**
 * The title of a page whose name comes from the server (a group, a site) is
 * only known once it has loaded, so Group.vue and Site.vue set their own as
 * soon as they have it. What the router can do meanwhile is stop the previous
 * page's title from sticking around: the slug is already in the path.
 */
router.afterEach((to) => {
  if (typeof to.meta.titleKey === 'string') {
    setDocumentTitle(t(to.meta.titleKey as MessageKey));
    return;
  }
  const { group, site } = to.params as { group?: string; site?: string };
  setDocumentTitle(site ?? group);
});

export default router;
