<script setup lang="ts">
/**
 * Group page: the header with "Zet een site online" and below it
 * three tabs, each with its own route: sites, group members and settings.
 * Route: /:group (base '/', so in-app path `/{group}`);
 * the follow-up paths sit under `-/`, see router.ts.
 *
 * The page loads the group once and keeps it up to date: the tabs get their
 * part as a prop and report a change back. That way switching tabs refetches
 * nothing and the header stays put.
 *
 * "Zet een site online" sits in the header rather than on a tab: the action
 * belongs to the group, not to one part of it, and stays reachable everywhere.
 * It only shows for someone who may actually create a site here (group role
 * editor/beheerder, via `mayCreateSiteIn`); without it the button would only
 * lead to a 403 after the form is filled in.
 *
 * The breadcrumb trail travels to the app shell's footer via the shared
 * breadcrumb state. This page watches the publish request from the toolbar
 * itself. Without being a watcher, composables/addActions would send the
 * user to the overview, a detour past a page they have just left.
 */
import { computed, onMounted, ref, watch, watchEffect } from 'vue';
import { useRoute, useRouter } from 'vue-router';

import * as api from '@/api/plak';
import type { Group, GroupDetail, GroupMember, Me, Site } from '@/api/types';
import ErrorBanner from '@/components/ErrorBanner.vue';
import PublishSheet from '@/components/PublishSheet.vue';
import { fetchCurrentMember } from '@/composables/currentMember';
import { resolveDroppedFile, useDropState, useWindowDropGuard } from '@/composables/fileDrop';
import { goToDone } from '@/composables/publishedMark';
import { isGroupAdmin, mayCreateSiteIn } from '@/composables/roles';
import { crumbOverview, setBreadcrumbs } from '@/composables/breadcrumbs';
import { t, type MessageKey } from '@/i18n';
import { setDocumentTitle } from '@/title';
import { useAddActions } from '@/composables/addActions';

const route = useRoute();
const router = useRouter();
/* v8 ignore start -- the '?? ''' fallback is unreachable while this page
   only mounts under the ':group' route, which always supplies the param. */
const groupSlug = computed(() => String(route.params.group ?? ''));
/* v8 ignore stop */

const loading = ref(true);
const error = ref<unknown>(null);
const detail = ref<GroupDetail | null>(null);
const sheetOpen = ref(false);
/** Content origin from `/me`; the sheet uses it to show the site's address. */
const contentBase = ref('');
/** From `/me`; gates the publish button and feeds the sheet's own filtering. */
const me = ref<Me | null>(null);
// A failed session fetch must not topple the page (same stance as
// `contentBase` above): with the role unknown, the button stays rather than
// stranding a flow that may well be allowed.
const canPublish = computed(() => me.value == null || mayCreateSiteIn(me.value, groupSlug.value));
// Unlike canPublish, an unknown role hides it: deleting is not a flow to strand.
const canDeleteGroup = computed(() => isGroupAdmin(me.value, groupSlug.value));

async function loadGroup(): Promise<void> {
  loading.value = true;
  error.value = null;
  try {
    // The session is not a precondition here: if it fails, only the host in
    // the displayed address drops out, and that must not topple the group page.
    const [fetched, loggedIn] = await Promise.all([
      api.group(groupSlug.value),
      fetchCurrentMember().catch(() => null),
    ]);
    me.value = loggedIn as Me | null;
    contentBase.value = me.value?.contentBaseUrl ?? '';
    detail.value = fetched;
  } catch (e) {
    error.value = e;
  } finally {
    loading.value = false;
  }
}

onMounted(loadGroup);

watchEffect(() => {
  setBreadcrumbs(route.path, [
    crumbOverview(),
    { text: detail.value?.group.name ?? groupSlug.value },
  ]);
});

// -- Tabs -----------------------------------------------------------------

interface TabDefinition {
  name: string;
  /** The catalogue key, not the word: the tab bar renders in the current language. */
  labelKey: MessageKey;
  path: string;
}

const TABS: readonly TabDefinition[] = [
  { name: 'group-sites', labelKey: 'group.page.tab.sites', path: '' },
  { name: 'group-members', labelKey: 'group.page.tab.members', path: '-/members' },
  { name: 'group-settings', labelKey: 'group.page.tab.settings', path: '-/settings' },
];

const currentTab = computed(() => TABS.find((tab) => tab.name === route.name));

watchEffect(() => {
  setDocumentTitle(detail.value?.group.name ?? groupSlug.value, currentTab.value?.path ? t(currentTab.value.labelKey) : null);
});

function tabPath(tab: TabDefinition): string {
  const base = `/${groupSlug.value}`;
  return tab.path === '' ? base : `${base}/${tab.path}`;
}

function goToTab(tab: TabDefinition): void {
  void router.push(tabPath(tab));
}

function tabTestId(tab: TabDefinition): string {
  return `tab-${tab.name.replace('group-', '')}`;
}

// -- Putting a site online ------------------------------------------------

function publish(group: string, site: string, file: File): Promise<void> {
  return api.upload(group, site, file, file.name).then(() => undefined);
}

function onSiteCreated(site: Site): void {
  detail.value?.sites.push(site);
}

function onPublished(site: Site): void {
  void goToDone(router, site.groupSlug, site.slug);
}

const { newSite } = useAddActions();

watch(newSite, () => {
  if (canPublish.value) sheetOpen.value = true;
});

// -- Dropping a file straight onto the page --------------------------------
// The group is already fixed here (the sheet gets only this one group), so a
// drop needs no group picker: it only has to fill the file and open the
// sheet, gated by the same `canPublish` as the header button.

const {
  isOver: dropTargetActive,
  onDragEnter: dropOnDragEnter,
  onDragOver: dropOnDragOver,
  onDragLeave: dropOnDragLeave,
  reset: resetDropState,
} = useDropState();
const droppedFile = ref<File | null>(null);
const dropError = ref<string | null>(null);

useWindowDropGuard(canPublish);

function onPageDragEnter(event: DragEvent): void {
  if (canPublish.value) dropOnDragEnter(event);
}

function onPageDragOver(event: DragEvent): void {
  if (canPublish.value) dropOnDragOver(event);
}

function onPageDragLeave(event: DragEvent): void {
  if (canPublish.value) dropOnDragLeave(event);
}

function onPageDrop(event: DragEvent): void {
  if (!canPublish.value) return;
  event.preventDefault();
  resetDropState();
  const { file, error: rejection } = resolveDroppedFile(event.dataTransfer);
  if (rejection) {
    dropError.value = rejection;
    return;
  }
  if (!file) return;
  dropError.value = null;
  droppedFile.value = file;
  sheetOpen.value = true;
}

watch(sheetOpen, (open) => {
  if (!open) droppedFile.value = null;
});

// -- Changes coming out of the tabs ---------------------------------------

function afterRemoval(): void {
  void router.replace('/');
}

function onMemberAdded(member: GroupMember): void {
  detail.value?.members.push(member);
}

/* v8 ignore start -- these three handlers only fire from tab children that
   render behind 'v-else-if="detail"', so detail is always set by the time
   any of them runs; the guards are defensive against a type of null,
   not a reachable state. */
function onMemberRemoved(identifier: string): void {
  if (!detail.value) return;
  detail.value.members = detail.value.members.filter((l) => l.identifier !== identifier);
}

function onMemberRoleChanged(member: GroupMember): void {
  if (!detail.value) return;
  const index = detail.value.members.findIndex((l) => l.identifier === member.identifier);
  // The backend always answers about the same member it was called for.
  if (index !== -1) detail.value.members.splice(index, 1, member);
}

function onGroupChanged(group: Group): void {
  if (detail.value) detail.value.group = group;
}
/* v8 ignore stop */
</script>

<template>
  <nldd-simple-section
    @dragenter="onPageDragEnter"
    @dragover="onPageDragOver"
    @dragleave="onPageDragLeave"
    @drop="onPageDrop"
  >
    <nldd-banner
      v-if="dropTargetActive && canPublish"
      variant="accent"
      :text="t('group.drop.release')"
      data-testid="groep-sleep-actief"
    ></nldd-banner>

    <nldd-banner
      v-if="dropError"
      variant="critical"
      dismissible
      :text="dropError"
      data-testid="groep-sleep-fout"
      @dismiss="dropError = null"
    ></nldd-banner>

    <nldd-inline-dialog
      v-if="loading"
      variant="loading"
      :text="t('group.page.loading')"
    ></nldd-inline-dialog>

    <ErrorBanner v-else-if="error" :error="error" />

    <template v-else-if="detail">
      <!-- Title and tab bar together form the page header (16), the content
           below it starts at the section distance (24): the same scale as the
           site page, see the comment there. -->
      <!-- See Overview.vue: the end slot of nldd-title does not let the button
           drop and squashes the title flat on a narrow screen. -->
      <nldd-container layout="wrap" gap="16" vertical-alignment="center" class="page-header">
        <nldd-title :size="3"><h1>{{ detail.group.name }}</h1></nldd-title>
        <nldd-button
          v-if="canPublish"
          variant="primary"
          :text="t('group.action.publishSite')"
          start-icon="plus"
          data-testid="groep-publiceren"
          @click="sheetOpen = true"
        ></nldd-button>
      </nldd-container>

      <nldd-spacer size="16"></nldd-spacer>

      <nldd-tab-bar
        navigation
        :accessible-label="t('group.page.tabs.label')"
        data-testid="groep-tabs"
      >
        <nldd-tab-bar-item
          v-for="tab in TABS"
          :key="tab.name"
          :text="t(tab.labelKey)"
          :href="tabPath(tab)"
          :current="route.name === tab.name || undefined"
          :data-testid="tabTestId(tab)"
          @click.prevent="goToTab(tab)"
        ></nldd-tab-bar-item>
      </nldd-tab-bar>

      <nldd-spacer size="24"></nldd-spacer>

      <router-view v-slot="{ Component }">
        <component
          :is="Component"
          :group="groupSlug"
          :sites="detail.sites"
          :members="detail.members"
          :access="detail.group.defaultAccess"
          :can-delete="canDeleteGroup"
          @member-added="onMemberAdded"
          @member-removed="onMemberRemoved"
          @member-role-changed="onMemberRoleChanged"
          @group-changed="onGroupChanged"
          @removed="afterRemoval"
        />
      </router-view>

      <PublishSheet
        v-model:open="sheetOpen"
        :groups="[detail.group]"
        :me="me"
        :content-base="contentBase"
        :create-group="api.createGroup"
        :create-site="api.createSite"
        :set-access="api.setAccess"
        :publish="publish"
        :initial-file="droppedFile"
        @created="onSiteCreated"
        @published="onPublished"
      />
    </template>
  </nldd-simple-section>
</template>
