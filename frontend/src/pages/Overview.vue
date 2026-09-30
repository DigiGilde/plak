<script setup lang="ts">
/**
 * Site overview: the start page after login. One table per group,
 * with a row per site carrying the title as a link to the site detail.
 * Loads via the session API (`overview()`); see src/api/mock.ts for the dev
 * mock.
 *
 * The page has one primary action ("Zet een site online") that does the whole
 * task, including creating a group when the user has none yet. So the empty
 * state does not send anyone off to a platform admin.
 *
 * Both create actions live here on the page, next to the thing they act on:
 * one split button beside the h1, publishing on the button itself and creating
 * a group in its menu. The menu in the toolbar carries navigation and account
 * only.
 */
import { computed, onMounted, ref, watch } from 'vue';
import { useRouter } from 'vue-router';

import { createGroup, overview, createSite, setAccess, upload } from '../api/plak';
import type { Group, Me, Overview, Site } from '../api/types';
import GroupHeading from '../components/GroupHeading.vue';
import NewGroupSheet from '../components/NewGroupSheet.vue';
import SiteRow, { SITE_COLUMNS, SITE_COLUMNS_SM } from '../components/SiteRow.vue';
import PublishSheet from '../components/PublishSheet.vue';
import { fetchCurrentMember } from '../composables/currentMember';
import { resolveDroppedFile, useDropState, useWindowDropGuard } from '../composables/fileDrop';
import { goToDone } from '../composables/publishedMark';
import { mayCreateSiteIn } from '../composables/roles';
import { t } from '@/i18n';

type State = 'loading' | 'filled' | 'empty' | 'error';



const router = useRouter();

const state = ref<State>('loading');
const data = ref<Overview | null>(null);
const errorMessage = ref('');
/** Content origin from `/me`; the sheet uses it to show the site's address. */
const contentBase = ref('');
/** From `/me`; the sheet uses `groupRoles` to filter the group picker. */
const me = ref<Me | null>(null);

async function loading(): Promise<void> {
  state.value = 'loading';
  try {
    // The session is not a precondition here: if it fails, only the host in
    // the displayed address drops out, and that must not topple the overview.
    const [result, loggedIn] = await Promise.all([
      overview(),
      fetchCurrentMember().catch(() => null),
    ]);
    me.value = loggedIn as Me | null;
    contentBase.value = me.value?.contentBaseUrl ?? '';
    data.value = result;
    const hasContent = result.groups.length > 0;
    state.value = hasContent ? 'filled' : 'empty';
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : t('group.overview.errorFallback');
    state.value = 'error';
  }
}

onMounted(loading);

defineExpose({ loading });

// -- Creating from this page ----------------------------------------------

const siteSheetOpen = ref(false);
const groupSheetOpen = ref(false);

/** The groups the user is a member of: exactly what the overview holds. */
const groups = computed<Group[]>(() => data.value?.groups.map((row) => row.group) ?? []);

function publish(): void {
  siteSheetOpen.value = true;
}

function uploadToSite(groupSlug: string, siteSlug: string, file: File): Promise<void> {
  return upload(groupSlug, siteSlug, file, file.name).then(() => undefined);
}

function onSiteCreated(site: Site): void {
  data.value?.groups.find((row) => row.group.slug === site.groupSlug)?.sites.push(site);
}

function onGroupCreated(group: Group): void {
  data.value?.groups.push({ group, sites: [] });
  state.value = 'filled';
}

function onPublished(site: Site): void {
  void goToDone(router, site.groupSlug, site.slug);
}

// -- Dropping a file straight onto the page --------------------------------

/** Dropping opens the sheet for someone the sheet can serve: a group they may
 * publish in, or no group at all, because the sheet then asks for a name and
 * creates one along the way. An unconfirmed `me` (failed session fetch) is
 * treated as "no", unlike the sheet's own filtering, since here there is
 * nothing to strand. */
const canPublishHere = computed(
  () =>
    me.value != null &&
    (groups.value.length === 0 ||
      groups.value.some((group) => mayCreateSiteIn(me.value, group.slug))),
);

const {
  isOver: dropTargetActive,
  onDragEnter: dropOnDragEnter,
  onDragOver: dropOnDragOver,
  onDragLeave: dropOnDragLeave,
  reset: resetDropState,
} = useDropState();
const droppedFile = ref<File | null>(null);
const dropError = ref<string | null>(null);

useWindowDropGuard(canPublishHere);

function onPageDragEnter(event: DragEvent): void {
  if (canPublishHere.value) dropOnDragEnter(event);
}

function onPageDragOver(event: DragEvent): void {
  if (canPublishHere.value) dropOnDragOver(event);
}

function onPageDragLeave(event: DragEvent): void {
  if (canPublishHere.value) dropOnDragLeave(event);
}

function onPageDrop(event: DragEvent): void {
  if (!canPublishHere.value) return;
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
  siteSheetOpen.value = true;
}

// The sheet has consumed it once it applies it on open; drop it here too so
// reopening later through the button does not silently carry it along.
watch(siteSheetOpen, (open) => {
  if (!open) droppedFile.value = null;
});
</script>

<template>
  <nldd-simple-section
    @dragenter="onPageDragEnter"
    @dragover="onPageDragOver"
    @dragleave="onPageDragLeave"
    @drop="onPageDrop"
  >
    <nldd-banner
      v-if="dropTargetActive && canPublishHere"
      variant="accent"
      :text="t('group.drop.release')"
      data-testid="overzicht-sleep-actief"
    ></nldd-banner>

    <nldd-banner
      v-if="dropError"
      variant="critical"
      dismissible
      :text="dropError"
      data-testid="overzicht-sleep-fout"
      @dismiss="dropError = null"
    ></nldd-banner>

    <!-- Not in the end slot of nldd-title: that slot neither shrinks along nor
         lets the button drop, which breaks the title letter by letter at 320 px
         and pushes the button outside the viewport (measured: h1 0 px wide,
         scrollWidth 352 at clientWidth 320). A wrap container lets the button
         drop a line as soon as it no longer fits beside the title. -->
    <nldd-container layout="wrap" gap="16" vertical-alignment="center" class="page-header">
      <nldd-title :size="1"><h1>{{ t('group.overview.heading') }}</h1></nldd-title>
      <!-- A split button, not two buttons: publishing is the action people come
           for, creating a group is the rarer one. Below the list it would mean
           scrolling to the bottom to reach it on a long list; in the chevron
           it is one click away from the top. -->
      <nldd-split-button
        v-if="state !== 'loading' && state !== 'error'"
        variant="primary"
        :text="t('group.action.publishSite')"
        icon="plus"
        data-testid="overzicht-publiceren"
        @action-click="publish()"
      >
        <nldd-menu placement="bottom-end">
          <nldd-menu-item
            :text="t('group.action.newGroup')"
            icon="folder-on-folder"
            data-testid="overzicht-groep-aanmaken"
            @select="groupSheetOpen = true"
          ></nldd-menu-item>
        </nldd-menu>
      </nldd-split-button>
    </nldd-container>

    <nldd-spacer size="24"></nldd-spacer>

    <nldd-activity-indicator
      v-if="state === 'loading'"
      show-text
      :text="t('group.overview.loading')"
    ></nldd-activity-indicator>

    <nldd-banner
      v-else-if="state === 'error'"
      variant="critical"
      :text="t('group.overview.error')"
      :supporting-text="errorMessage"
    ></nldd-banner>

    <nldd-inline-dialog
      v-else-if="state === 'empty'"
      icon="globe"
      :text="t('group.overview.empty')"
      :supporting-text="t('group.overview.empty.supportingText')"
    >
      <nldd-button
        slot="actions"
        variant="primary"
        :text="t('group.action.publishSite')"
        start-icon="plus"
        data-testid="overzicht-leeg-publiceren"
        @click="publish()"
      ></nldd-button>
    </nldd-inline-dialog>

    <nldd-container v-else-if="data" layout="stack" gap="24">
      <nldd-container
        v-for="overviewGroup in data.groups"
        :key="overviewGroup.group.slug"
        layout="stack"
        gap="8"
      >
        <GroupHeading :group="overviewGroup.group" />

        <nldd-table
          background="tinted"
          :accessible-label="t('group.overview.table.label', { group: overviewGroup.group.name })"
          :columns="SITE_COLUMNS"
          :sm-columns="SITE_COLUMNS_SM"
        >
          <nldd-table-row slot="header">
            <nldd-text-cell
              :text="`**${t('group.sites.column.live')}**`"
            ></nldd-text-cell>
            <nldd-text-cell
              :text="`**${t('group.sites.column.site')}**`"
            ></nldd-text-cell>
            <nldd-text-cell
              :text="`**${t('group.sites.column.access')}**`"
              hide-below="md"
            ></nldd-text-cell>
            <nldd-text-cell
              :text="`**${t('group.sites.column.lastPublished')}**`"
              hide-below="md"
            ></nldd-text-cell>
          </nldd-table-row>
          <nldd-inline-dialog
            slot="empty"
            icon="rectangle-stack"
            :text="t('group.overview.table.empty')"
          ></nldd-inline-dialog>
          <SiteRow
            v-for="site in overviewGroup.sites"
            :key="site.slug"
            :site="site"
          />
        </nldd-table>
      </nldd-container>
    </nldd-container>

    <PublishSheet
      v-model:open="siteSheetOpen"
      :groups="groups"
      :me="me"
      :content-base="contentBase"
      :create-group="createGroup"
      :create-site="createSite"
      :set-access="setAccess"
      :publish="uploadToSite"
      :initial-file="droppedFile"
      @group-created="onGroupCreated"
      @created="onSiteCreated"
      @published="onPublished"
    />
    <NewGroupSheet
      v-model:open="groupSheetOpen"
      :create="createGroup"
      @created="onGroupCreated"
    />
  </nldd-simple-section>
</template>
