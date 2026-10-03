<script setup lang="ts">
/**
 * Groups page (/-/groups): the groups this member belongs to, without the
 * sites under them. The overview shows groups AND all their sites, and
 * that is too much as soon as you are only looking for a group.
 *
 * Leans on the existing overview endpoint: it already carries the sites per
 * group, so the counts per row cost no extra request.
 */
import { onMounted, ref } from 'vue';
import { useRoute } from 'vue-router';

import { createGroup, overview } from '@/api/plak';
import type { Group, OverviewGroup } from '@/api/types';
import ErrorBanner from '@/components/ErrorBanner.vue';
import NewGroupSheet from '@/components/NewGroupSheet.vue';
import { crumbsGroups, setBreadcrumbs } from '@/composables/breadcrumbs';
import { t } from '@/i18n';

const route = useRoute();

const rows = ref<OverviewGroup[]>([]);
const loading = ref(true);
const error = ref<unknown>(null);
const sheetOpen = ref(false);

async function loadGroups(): Promise<void> {
  loading.value = true;
  error.value = null;
  try {
    rows.value = (await overview()).groups;
  } catch (e) {
    error.value = e;
  } finally {
    loading.value = false;
  }
}

onMounted(() => {
  setBreadcrumbs(route.path, crumbsGroups());
  void loadGroups();
});

function sitesLabel(row: OverviewGroup): string {
  const count = row.sites.length;
  if (count === 0) return t('group.groups.sites.none');
  return count === 1 ? t('group.groups.sites.one') : t('group.groups.sites.many', { count });
}

function onlineLabel(row: OverviewGroup): string {
  const count = row.sites.filter((site) => site.hasLiveVersion).length;
  if (count === 0) return t('group.groups.online.none');
  return count === 1 ? t('group.groups.online.one') : t('group.groups.online.many', { count });
}

function onGroupCreated(group: Group): void {
  rows.value.push({ group, sites: [] });
}
</script>

<template>
  <nldd-simple-section>
    <!-- Same shape as the overview: the action beside the h1 rather than below
         the list, so a long list does not put it out of reach. The wrap
         container lets the button drop a line when it no longer fits. -->
    <nldd-container layout="wrap" gap="16" vertical-alignment="center" class="page-header">
      <nldd-title :size="1"><h1>{{ t('group.groups.heading') }}</h1></nldd-title>
      <nldd-button
        v-if="!loading && !error"
        variant="primary"
        :text="t('group.action.newGroup')"
        start-icon="plus"
        data-testid="groups-create"
        @click="sheetOpen = true"
      ></nldd-button>
    </nldd-container>

    <nldd-spacer size="24"></nldd-spacer>

    <nldd-activity-indicator
      v-if="loading"
      show-text
      :text="t('group.groups.loading')"
    ></nldd-activity-indicator>

    <ErrorBanner v-else-if="error" :error="error" />

    <nldd-container v-else layout="stack" gap="16">
      <nldd-list variant="box-tinted" :accessible-label="t('group.groups.list.label')">
        <nldd-inline-dialog
          slot="empty"
          icon="folder-on-folder"
          :text="t('group.groups.empty')"
          :supporting-text="t('group.groups.empty.supportingText')"
        ></nldd-inline-dialog>
        <nldd-list-item
          v-for="row in rows"
          :key="row.group.slug"
          size="md"
          :href="`/${row.group.slug}`"
        >
          <nldd-title-cell
            :text="row.group.name"
            :supporting-text="row.group.slug"
          ></nldd-title-cell>
          <nldd-text-cell
            width="fit-content"
            size="sm"
            horizontal-alignment="right"
            :text="sitesLabel(row)"
          ></nldd-text-cell>
          <!-- hide-below is measured against the width of the list, not that
               of the screen; without it the last cell keeps its full width and
               nothing is left for the name. -->
          <nldd-spacer-cell size="16" hide-below="md"></nldd-spacer-cell>
          <nldd-text-cell
            width="fit-content"
            size="sm"
            horizontal-alignment="right"
            hide-below="md"
            :text="onlineLabel(row)"
          ></nldd-text-cell>
        </nldd-list-item>
      </nldd-list>
    </nldd-container>

    <NewGroupSheet
      v-model:open="sheetOpen"
      :create="createGroup"
      @created="onGroupCreated"
    />
  </nldd-simple-section>
</template>
