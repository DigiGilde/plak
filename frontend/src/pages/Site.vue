<script setup lang="ts">
import { computed, provide, ref, watch, watchEffect, type ComputedRef, type Ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';

import * as plak from '@/api/plak';
import type { GroupDetail, Me, Site } from '@/api/types';
import { fetchCurrentMember } from '@/composables/currentMember';
import { setBreadcrumbs } from '@/composables/breadcrumbs';
import { useLoader } from '@/composables/loader';
import { groupPath } from '@/composables/slug';
import { SITE_GROUP, siteNotFound } from '@/composables/siteGroup';
import { setDocumentTitle } from '@/title';
import { t } from '@/i18n';
import ErrorBanner from '@/components/ErrorBanner.vue';

const route = useRoute();
const router = useRouter();

const groupSlug = computed(() => String(route.params.group ?? ''));
const siteSlug = computed(() => String(route.params.site ?? ''));

const detail = ref<GroupDetail | null>(null);
/** Content origin from `/me`; the tabs build every shared link on it. */
const contentBase = ref('');

const site = computed<Site | null>(
  () => detail.value?.sites.find((p) => p.slug === siteSlug.value) ?? null,
);

provide(SITE_GROUP, {
  // Tabs render only once the site is there, so for them neither is null.
  detail: detail as Ref<GroupDetail>,
  site: site as ComputedRef<Site>,
});

/** Fetches group and session; throws when the site cannot be shown. */
async function fetchData(): Promise<{ fetched: GroupDetail; contentBase: string }> {
  const [fetched, loggedIn] = await Promise.all([
    plak.group(groupSlug.value),
    fetchCurrentMember(),
  ]);
  if (!fetched.sites.some((p) => p.slug === siteSlug.value)) {
    throw siteNotFound(groupSlug.value, siteSlug.value);
  }
  // currentMember stores the /me response typed as Member; contentBaseUrl rides along.
  return { fetched, contentBase: (loggedIn as Me | null)?.contentBaseUrl ?? '' };
}

const { loading, error, reload } = useLoader(
  fetchData,
  (data) => {
    detail.value = data.fetched;
    contentBase.value = data.contentBase;
  },
  () => [groupSlug.value, siteSlug.value],
  () => Boolean(groupSlug.value && siteSlug.value),
);

watch(error, (failure) => {
  if (failure) detail.value = null;
});

/**
 * Refresh after a change in a tab, deliberately quiet: `loading` would tear
 * down title, tab bar and the whole tab subtree and rebuild them. It would
 * take the tab's notification component with it, in the very tick where the tab
 * puts its confirmation into it ("Versie gepubliceerd", "Toegang
 * opgeslagen"), and make the tab bar flicker on every change. A failure leaves
 * the header on the last known answer rather than pulling the page out from
 * under the user.
 */
function refresh(): Promise<void> {
  return reload({ quiet: true });
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
]);

const currentTab = computed(() => TABS.value.find((tab) => tab.name === route.name));

// The site name is the distinguishing part, so it comes first; the tab is
// dropped on the overview, where it would only repeat the page itself.
watchEffect(() => {
  setDocumentTitle(site.value?.title || siteSlug.value, currentTab.value?.path ? currentTab.value.label : null);
});

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

    <ErrorBanner v-else-if="error" :error="error" />

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

      <nldd-spacer size="24"></nldd-spacer>

      <router-view v-slot="{ Component }">
        <component
          :is="Component"
          :group="groupSlug"
          :site="siteSlug"
          :content-base="contentBase"
          @removed="afterRemoval"
          @changed="refresh"
        />
      </router-view>
    </template>
  </nldd-simple-section>
</template>
