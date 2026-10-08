<script setup lang="ts">
import { computed, onMounted, ref, watch, watchEffect } from 'vue';
import { useRoute, useRouter } from 'vue-router';

import * as plak from '@/api/plak';
import { ApiError } from '@/api/client';
import type { GroupDetail, Me, Site } from '@/api/types';
import { fetchCurrentMember } from '@/composables/currentMember';
import { useCurrentTabInView } from '@/composables/currentTabInView';
import { setBreadcrumbs } from '@/composables/breadcrumbs';
import { groupPath } from '@/composables/slug';
import { setDocumentTitle } from '@/title';
import { t } from '@/i18n';
import ErrorBanner from '@/components/ErrorBanner.vue';

const route = useRoute();
const router = useRouter();

const groupSlug = computed(() => String(route.params.group ?? ''));
const siteSlug = computed(() => String(route.params.site ?? ''));

const loading = ref(true);
const error = ref<unknown>(null);
const detail = ref<GroupDetail | null>(null);
/** Content origin from `/me`; the tabs build every shared link on it. */
const contentBase = ref('');
/**
 * Which site the page shows. Its address can change under the page, and the
 * page goes on showing the site while the route follows.
 */
const siteId = ref<string | null>(null);

const site = computed<Site | null>(
  () => detail.value?.sites.find((p) => p.id === siteId.value) ?? null,
);

/** Fetches group and session; throws when the site cannot be shown. */
async function fetchData(): Promise<void> {
  const [fetched, loggedIn] = await Promise.all([
    plak.group(groupSlug.value),
    fetchCurrentMember(),
  ]);
  // currentMember stores the /me response typed as Member; contentBaseUrl rides along.
  contentBase.value = (loggedIn as Me | null)?.contentBaseUrl ?? '';
  const found = fetched.sites.find((p) => p.slug === siteSlug.value);
  if (!found) {
    throw new ApiError({
      type: 'about:blank',
      title: t('site.notFound.title'),
      status: 404,
      detail: t('site.notFound.detail', { site: siteSlug.value, group: groupSlug.value }),
    });
  }
  siteId.value = found.id;
  detail.value = fetched;
}

async function load(): Promise<void> {
  if (!groupSlug.value || !siteSlug.value) {
    return;
  }
  loading.value = true;
  error.value = null;
  try {
    await fetchData();
  } catch (f) {
    error.value = f;
    detail.value = null;
  } finally {
    loading.value = false;
  }
}

/**
 * Refresh after a change in a tab, deliberately without `loading`: that would
 * tear down title, tab bar and the whole tab subtree and rebuild them. It would
 * take the tab's notification component with it, in the very tick where the tab
 * puts its confirmation into it ("Versie gepubliceerd", "Toegang
 * opgeslagen"), and make the tab bar flicker on every change.
 */
async function refresh(): Promise<void> {
  try {
    await fetchData();
  } catch {
    // The tab refetches the same data itself and reports there what went
    // wrong; leaving the header on the last known answer beats pulling the
    // page out from under the user.
  }
}

/** The address this page has just moved the route to itself, which needs no load. */
let movedTo: string | null = null;

onMounted(load);
watch(
  () => [groupSlug.value, siteSlug.value],
  () => {
    const own = movedTo === `${groupSlug.value}/${siteSlug.value}`;
    movedTo = null;
    if (!own) void load();
  },
);

/**
 * The address of the site was changed in a tab. The page takes the site over
 * and moves the route to the new address without loading: a load would take the
 * tab down, and with it the line that says what came of the change.
 */
async function onRenamed(updated: Site): Promise<void> {
  const current = detail.value!;
  try {
    // The roles in `/me` name a site by its address.
    await fetchCurrentMember(true);
  } catch {
    // The address has changed all the same, and the tab goes on with the roles
    // it was opened with.
  }
  current.sites = current.sites.map((p) => (p.id === updated.id ? updated : p));
  movedTo = `${updated.groupSlug}/${updated.slug}`;
  await router.replace({ params: { site: updated.slug } });
  // The router has put the slug in the title, as it does on every navigation,
  // and nothing the title is made of has changed to make it come back.
  showTitle();
}

watchEffect(() => {
  setBreadcrumbs(route.path, [
    { text: t('nav.overview'), href: '/' },
    { text: detail.value?.group.name ?? groupSlug.value, href: groupPath(groupSlug.value) },
    { text: siteSlug.value },
  ]);
});

interface TabDefinition {
  name: string;
  label: string;
  path: string;
  /** Fixed, never derived from the label: a test id may not change with the language. */
  testid: string;
}

const TABS = computed<readonly TabDefinition[]>(() => [
  { name: 'site-overview', label: t('site.tabs.overview'), path: '', testid: 'tab-overview' },
  {
    name: 'site-previews',
    label: t('site.tabs.previews'),
    path: 'previews',
    testid: 'tab-previews',
  },
  {
    name: 'site-versions',
    label: t('site.tabs.versions'),
    path: 'versions',
    testid: 'tab-versions',
  },
  { name: 'site-access', label: t('site.tabs.access'), path: 'access', testid: 'tab-access' },
  { name: 'site-members', label: t('site.tabs.members'), path: 'members', testid: 'tab-members' },
  { name: 'site-deploy', label: t('site.tabs.deploy'), path: 'deploy', testid: 'tab-deploy' },
  {
    name: 'site-settings',
    label: t('site.tabs.settings'),
    path: 'settings',
    testid: 'tab-settings',
  },
]);

const currentTab = computed(() => TABS.value.find((tab) => tab.name === route.name));

const tabsScroll = ref<HTMLElement | null>(null);
useCurrentTabInView(tabsScroll, () => TABS.value.findIndex((tab) => tab.name === route.name));

// The site name is the distinguishing part, so it comes first; the tab is
// dropped on the overview, where it would only repeat the page itself.
function showTitle(): void {
  setDocumentTitle(site.value?.title || siteSlug.value, currentTab.value?.path ? currentTab.value.label : null);
}

watchEffect(showTitle);

function tabPath(tab: TabDefinition): string {
  const base = `/${groupSlug.value}/${siteSlug.value}`;
  return tab.path === '' ? base : `${base}/${tab.path}`;
}

function goToTab(tab: TabDefinition): void {
  void router.push(tabPath(tab));
}

function afterRemoval(): void {
  void router.replace('/');
}
</script>

<template>
  <!-- The deploy tab keeps the reading width itself, so its code can run to
       the work width. -->
  <nldd-simple-section :class="{ 'reading-width': route.name !== 'site-deploy' }">
    <nldd-activity-indicator v-if="loading" :text="t('site.loading')"></nldd-activity-indicator>

    <ErrorBanner v-else-if="error" :error="error" renamed-hint />

    <template v-else-if="site">
      <!--
        Vertical rhythm of the site page and all its tabs, one scale. Taken
        from the design system's form spacing (dist/css/form-section.css): every
        step stands for one relation, so equal relations get equal distance.

          4   a value and the line that explains it
          8   a heading and the content belonging under it
          16  separate blocks within one section
          24  sections among each other

        Title and tab bar together form the page header (16); the content below
        starts at the section distance (24).
      -->
      <nldd-title :size="2">
        <h1>{{ site.title || site.slug }}</h1>
        <span slot="subtitle">{{ groupSlug }}/{{ siteSlug }}</span>
      </nldd-title>

      <nldd-spacer size="16"></nldd-spacer>

      <div ref="tabsScroll" class="tabs-scroll">
        <nldd-tab-bar navigation :accessible-label="t('site.tabs.label')" data-testid="site-tabs">
          <nldd-tab-bar-item
            v-for="tab in TABS"
            :key="tab.name"
            :text="tab.label"
            :href="tabPath(tab)"
            :current="route.name === tab.name || undefined"
            :data-testid="tab.testid"
            @click.prevent="goToTab(tab)"
          ></nldd-tab-bar-item>
        </nldd-tab-bar>
      </div>

      <nldd-spacer size="24"></nldd-spacer>

      <router-view v-slot="{ Component }">
        <component
          :is="Component"
          :group="groupSlug"
          :site="siteSlug"
          :content-base="contentBase"
          :previous-slugs="site.previousSlugs"
          @removed="afterRemoval"
          @changed="refresh"
          @renamed="onRenamed"
        />
      </router-view>
    </template>
  </nldd-simple-section>
</template>
