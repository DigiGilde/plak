<script lang="ts">
/**
 * The column widths of a site table, exported here rather than at a page,
 * because this component decides which cells fill them. The group page and the
 * site overview both use them, so one site reads the same wherever it appears.
 *
 * Only the title stretches; the rest are fixed at what their widest content
 * measures at a root font of 16 px. Fixed rather than `auto`, because `auto`
 * measures per table, so two tables on one page would drift out of line.
 */
export const SITE_COLUMNS = '2.5rem minmax(11rem, 1fr) 9.25rem 9rem';
/** Below 640 px only the marker and the title remain. */
export const SITE_COLUMNS_SM = 'auto minmax(0, 1fr)';
</script>

<script setup lang="ts">
/**
 * One site row in the overview's site table: status, visibility
 * and last publication are informational, no action buttons at row level. The
 * title is the link to the site detail. The preview count lives on the
 * site's own previews tab, not here.
 *
 * The cells follow the order of the columns Overview.vue puts on the table,
 * with the same hide-below breakpoints: a row places its cells in order in the
 * table's subgrid, so a cell that drops out here without the column dropping
 * out there shifts everything behind it one column along.
 */
import { computed } from 'vue';

import type { Site } from '../api/types';
import { accessLabel, formatTimestamp } from '../format';
import { t } from '@/i18n';

const props = defineProps<{
  site: Site;
}>();

const accessText = computed(() => accessLabel(props.site.access));

const lastPublishedLabel = computed(() => {
  if (!props.site.lastPublishedAt) {
    return t('group.siteRow.neverPublished');
  }
  return formatTimestamp(props.site.lastPublishedAt);
});

const siteHref = computed(
  () => `/${props.site.groupSlug}/${props.site.slug}`,
);
</script>

<template>
  <nldd-table-row :data-testid="`site-${site.slug}`">
    <nldd-cell>
      <nldd-badge
        :color="site.hasLiveVersion ? 'success' : 'neutral'"
        :accessible-label="site.hasLiveVersion ? t('group.siteRow.live') : t('group.siteRow.notLive')"
      ></nldd-badge>
    </nldd-cell>
    <nldd-title-cell :supporting-text="site.slug">
      <nldd-link :href="siteHref" size="inherit">{{ site.title }}</nldd-link>
    </nldd-title-cell>
    <nldd-cell hide-below="md">
      <nldd-badge color="neutral" :text="accessText"></nldd-badge>
    </nldd-cell>
    <nldd-text-cell
      size="sm"
      color="secondary"
      :text="lastPublishedLabel"
      hide-below="md"
    ></nldd-text-cell>
  </nldd-table-row>
</template>
