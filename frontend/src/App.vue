<script setup lang="ts">
/**
 * App shell: toolbar with the wordmark and a single menu button on the right,
 * the page itself, and a footer that carries the page's breadcrumb trail
 * alongside the legal bar (NLDD guideline: a breadcrumb trail is supporting
 * navigation, not the main navigation).
 *
 * The menu carries both the navigation and the account actions. The create
 * actions are deliberately not in it: they belong next to the thing they act
 * on, so next to the h1 of the overview and below every group table.
 */
import { computed, onMounted, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';

import { fetchCurrentMember, currentMemberState, isPlatformAdmin } from './composables/currentMember';
import { breadcrumbsFor } from './composables/breadcrumbs';
import { t } from './i18n';

interface MenuChoice {
  text: string;
  icon: string;
  /** In-app path of a navigation item; an action item has none. */
  path?: string;
}

const route = useRoute();
const router = useRouter();
const { member } = currentMemberState();
const logoutForm = ref<HTMLFormElement | null>(null);

const crumbs = computed(() => breadcrumbsFor(route.path));
const admin = computed(() => isPlatformAdmin(member.value));
// The IdP does not always supply a name; without a fallback the menu header
// would stay empty.
const userName = computed(() => member.value?.name?.trim() || member.value?.email || '');

// Every item is named after the h1 of the page it leads to, so that the menu
// and the heading carry the same word.
const navigation = computed<MenuChoice[]>(() => [
  { text: t('nav.overview'), icon: 'rectangle-stack', path: '/' },
  { text: t('nav.groups'), icon: 'folder-on-folder', path: '/-/groups' },
  // A page like any other, so it belongs with the navigation; what gates it is
  // the platform role, not the fact that it is about accounts.
  ...(admin.value ? [{ text: t('nav.platform'), icon: 'person-2', path: '/-/members' }] : []),
]);

/**
 * Logging out is the only item that acts instead of navigating. Computed, not
 * a constant: the labels change with the language, and this menu is where the
 * page that changes it is reached from.
 */
const account = computed<MenuChoice[]>(() => [
  { text: t('nav.profile'), icon: 'person', path: '/-/profiel' },
  { text: t('nav.devices'), icon: 'link', path: '/-/apparaten' },
  { text: t('nav.logout'), icon: 'logout' },
]);

onMounted(() => {
  // No session is not an error here: the public platform pages run in the
  // same shell and then simply show no menu.
  void fetchCurrentMember().catch(() => null);
});

/**
 * Navigation items are real links (middle click, open in a new tab, copy link)
 * yet stay inside the SPA on a plain left click. A click with a modifier key
 * leaves the native behaviour alone.
 */
function onMenuClick(event: MouseEvent, choice: MenuChoice): void {
  if (choice.path === undefined) return;
  if (
    event.metaKey ||
    event.ctrlKey ||
    event.shiftKey ||
    event.altKey ||
    event.button !== 0
  ) {
    return;
  }
  event.preventDefault();
  void router.push(choice.path);
}

/**
 * NLDD renders an item with an href as an <a>: Enter activates natively (and
 * becomes a click), space only scrolls. This makes links and buttons behave
 * the same.
 */
function onMenuKey(event: KeyboardEvent, choice: MenuChoice): void {
  if (choice.path === undefined || event.key !== ' ') return;
  event.preventDefault();
  void router.push(choice.path);
}

/** Only the action item does anything on select; a navigation item is a link. */
function onMenuChoice(choice: MenuChoice): void {
  if (choice.path === undefined) logout();
}

function logout(): void {
  // POST, because the backend only ends the session on POST and answers with
  // a 303 to the landing page. A real form navigates along and resets the
  // whole SPA state; a fetch would swallow that redirect inside the app.
  // Without a CSRF token: a form cannot set the X-CSRF-Token header and the
  // route does not demand one (the session cookie is SameSite=Strict, so a
  // cross-site POST does not carry it). Should the route ever require CSRF,
  // this has to become a fetch with that header.
  logoutForm.value?.submit();
}
</script>

<template>
  <!-- Short on purpose: the control inside nldd-skip-link is white-space:
       nowrap with no max-width, so the label decides the width. "Direct naar
       de inhoud" measures 352 px at 200 percent text and pushes every page
       into a horizontal scroll at a 320 px viewport (WCAG 1.4.10); this one
       measures 260 px. -->
  <nldd-skip-link href="#hoofdinhoud" :text="t('shell.skip')"></nldd-skip-link>
  <nldd-app-view>
    <nldd-page>
      <div slot="header">
        <!-- Above everything, the way invulhulpen carries the same notice: a
             status bar is page-wide system state, not a message inside the
             content. It shows one line and truncates with an ellipsis, so the
             first word has to carry the meaning. -->
        <nldd-status-bar
          variant="warning"
          role="status"
          aria-live="polite"
          aria-atomic="true"
          :text="t('beta.bar')"
          data-testid="beta-banner"
        ></nldd-status-bar>
        <div class="toolbar">
          <nldd-toolbar label="Plak">
            <!-- The whole wordmark sits in the media slot and there is no
                 `text`: with both, the P would be read twice ("[P] Plak"). The
                 min-width is the width of that wordmark: without it the title
                 shrinks below its own content on a narrow screen and the buttons
                 slide over it. -->
            <nldd-toolbar-title slot="start" href="/" min-width="5rem">
              <span slot="media" class="brand" role="img" :aria-label="t('shell.brand.label')">
                <span class="brand__block" aria-hidden="true">P</span>
                <span class="brand__word" aria-hidden="true">lak</span>
              </span>
            </nldd-toolbar-title>
            <nldd-toolbar-item v-if="member" slot="end">
              <!-- Transparent, no fill or shadow: the button belongs to the
                   page, not to a bar lying over it.
                   Deliberately without `expandable`: that attribute draws the
                   chevron AND forces aria-expanded (button.template.js:
                   `expandable || popup-type`), and there is no separate attribute
                   for the chevron alone. popup-type="menu" already forces
                   aria-expanded here and sets aria-haspopup, so the chevron can
                   go without losing the keyboard and screen reader state. -->
              <nldd-button
                variant="neutral-transparent"
                :text="t('shell.menu')"
                start-icon="menu"
                popup-type="menu"
              >
                <nldd-menu slot="popup" placement="bottom-end">
                  <nldd-menu-item
                    v-for="choice in navigation"
                    :key="choice.text"
                    :text="choice.text"
                    :icon="choice.icon"
                    :href="choice.path"
                    @click="onMenuClick($event as MouseEvent, choice)"
                    @keydown="onMenuKey($event as KeyboardEvent, choice)"
                  ></nldd-menu-item>
                  <!-- A group rather than a bare separator: it draws that line
                       itself AND names what the second half is about. Who you are
                       sits inside it, not in the menu header: otherwise the menu
                       reads as three blocks (account info, pages, account
                       actions) where two say the same thing.
                       aria-hidden on the wrapper: the children of a menu are
                       menu items, and an identity is not one. The same name and
                       address are already on the page behind the menu. -->
                  <nldd-menu-group :text="t('nav.account')">
                    <nldd-container aria-hidden="true" padding="12">
                      <nldd-identity
                        :text="userName"
                        :supporting-text="userName === member.email ? undefined : member.email"
                      ></nldd-identity>
                    </nldd-container>
                    <nldd-menu-item
                      v-for="choice in account"
                      :key="choice.text"
                      :text="choice.text"
                      :icon="choice.icon"
                      :href="choice.path"
                      @click="onMenuClick($event as MouseEvent, choice)"
                      @keydown="onMenuKey($event as KeyboardEvent, choice)"
                      @select="onMenuChoice(choice)"
                    ></nldd-menu-item>
                  </nldd-menu-group>
                </nldd-menu>
              </nldd-button>
              <!-- Mandatory twin of the menu above: if the item does not fit in
                   the bar, nldd-toolbar hides the button and clones only what
                   sits here in the overflow slot. Without these lines there is no
                   way left to reach these actions on overflow (320 CSS px at 200%
                   text). The clone carries no click listener, only its select
                   reaches the original; a navigation item is a plain link there
                   that reloads the page. -->
              <nldd-menu-item
                v-for="choice in navigation"
                :key="choice.text"
                slot="overflow"
                :text="choice.text"
                :icon="choice.icon"
                :href="choice.path"
              ></nldd-menu-item>
              <nldd-menu-group slot="overflow" :text="t('nav.account')">
                <nldd-container aria-hidden="true" padding="12">
                  <nldd-identity
                    :text="userName"
                    :supporting-text="userName === member.email ? undefined : member.email"
                  ></nldd-identity>
                </nldd-container>
                <nldd-menu-item
                  v-for="choice in account"
                  :key="choice.text"
                  :text="choice.text"
                  :icon="choice.icon"
                  :href="choice.path"
                  @select="onMenuChoice(choice)"
                ></nldd-menu-item>
              </nldd-menu-group>
            </nldd-toolbar-item>
          </nldd-toolbar>
        </div>
      </div>

      <!-- nldd-page draws the main landmark itself in its shadow root; an own
           <main> around this would produce a second, nested main. This block is
           only the skip link's target, and tabindex really puts focus on it
           instead of only the scroll position. -->
      <div id="hoofdinhoud" tabindex="-1">
        <router-view />
      </div>
      <nldd-page-footer slot="footer">
        <nldd-breadcrumbs v-if="crumbs.length > 0" slot="breadcrumbs">
          <nldd-breadcrumbs-item
            v-for="crumb in crumbs"
            :key="crumb.text"
            :text="crumb.text"
            :href="crumb.href"
            :current="crumb.href === undefined || undefined"
          ></nldd-breadcrumbs-item>
        </nldd-breadcrumbs>
        <!-- Everything in the end slot: per NLDD that slot is for "privacy,
             accessibility", while start is for a "© notice, version". Plak has
             no copyright line, so start stays empty and the four links wrap as
             one group on a narrow screen instead of as two rows below each
             other. -->
        <nldd-page-footer-legal-bar slot="legal-bar">
          <nldd-page-footer-legal-bar-item
            slot="end"
            href="/-/about"
            :text="t('footer.about')"
          ></nldd-page-footer-legal-bar-item>
          <nldd-page-footer-legal-bar-item
            slot="end"
            href="/-/accessibility"
            :text="t('footer.accessibility')"
          ></nldd-page-footer-legal-bar-item>
          <nldd-page-footer-legal-bar-item
            slot="end"
            href="/-/privacy"
            :text="t('footer.privacy')"
          ></nldd-page-footer-legal-bar-item>
          <nldd-page-footer-legal-bar-item
            slot="end"
            href="/-/api/docs"
            :text="t('footer.api')"
          ></nldd-page-footer-legal-bar-item>
        </nldd-page-footer-legal-bar>
      </nldd-page-footer>
    </nldd-page>
  </nldd-app-view>
  <form ref="logoutForm" method="post" action="/-/logout" hidden></form>
</template>

<style scoped>
/* A page section and the footer draw their own gutter; nldd-toolbar does not,
   so the wordmark started at x=0 and the left half of its focus ring fell off
   screen. This is the composition nldd-top-navigation-bar uses internally: the
   wrapper measures itself, the bar inside carries the page-section margin and
   is capped at the body width, and is centred. That puts the wordmark at the
   same x as the h1, the group headings and the lists, and runs the bar to the
   same right edge as the tables and the footer. */
.toolbar {
  display: flex;
  container-type: inline-size;
  justify-content: center;
  /* Room above and below the bar, the way regelrecht puts its toolbar in an
     nldd-container with padding. Without this the wordmark sticks to the edge
     of the viewport and its focus ring falls off screen. */
  padding-block: var(--primitives-space-8);
}

.toolbar > nldd-toolbar {
  min-width: 0;
  flex: 1 1 auto;
  max-width: var(--semantics-page-sections-body-max-width);
  margin-inline: var(--semantics-page-sections-sm-margin-inline);
}

@container (min-width: 641px) and (max-width: 1007px) {
  .toolbar > nldd-toolbar {
    margin-inline: var(--semantics-page-sections-md-margin-inline);
  }
}

@container (min-width: 1008px) {
  .toolbar > nldd-toolbar {
    margin-inline: var(--semantics-page-sections-lg-margin-inline);
  }
}

/* The block with the "P" followed by "lak": together one wordmark, [P]lak. The
   typeface is the one of the title nldd-toolbar-title would draw itself, so the
   wordmark sits in the row as if it were the title. */
.brand {
  display: inline-flex;
  align-items: center;
  gap: var(--primitives-space-4);
  /* The display scale, not the body scale: the wordmark is a name and reads at
     the size of a page title, the way waggle sets its wordmark too. It fits
     within the toolbar's title group (44px at size md), so it does not stretch
     the row. */
  font: var(--primitives-font-display-3-sm);
}

.brand__block {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: var(--primitives-space-40);
  height: var(--primitives-space-40);
  border-radius: var(--primitives-corner-radius-sm);
  /* A pair from one family: the content colour moves with the background, so
     the "P" keeps its contrast in both schemes. Standalone link or text colours
     do not. */
  background: var(--semantics-categories-accent-filled-background-color);
  color: var(--semantics-categories-accent-filled-content-color);
  /* The letter stands apart from the wordmark: inside the block it fills the
     space, beside it it reads as text. Without a size of its own it inherits
     the wordmark size and swims in a block half again as large. */
  font-size: var(--primitives-font-size-300);
  font-weight: var(--primitives-font-weight-display-bold);
  line-height: 1;
}
</style>
