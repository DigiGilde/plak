<script setup lang="ts">
/**
 * Sites tab of the group page: every site in this group as a row to its own
 * site page.
 *
 * No add action of its own: the group page carries one in its header, and a
 * second button for the same job below the list only asks which of the two
 * you are supposed to press.
 */
import type { Site } from '@/api/types';
import SiteRow, { SITE_COLUMNS, SITE_COLUMNS_SM } from '@/components/SiteRow.vue';
import { t } from '@/i18n';
import SectionHeading from '@/components/SectionHeading.vue';

// The tabs receive the props of every tab; none of them should fall through
// to the markup.
defineOptions({ inheritAttrs: false });

defineProps<{ sites: Site[] }>();


</script>

<template>
  <section aria-labelledby="heading-sites">
    <nldd-container layout="stack" gap="8">
      <SectionHeading id="heading-sites" :text="t('group.sites.heading')" :intro="t('group.sites.intro')" />

      <!-- The same table as the site overview, filled by the same row
           component: one site reads the same wherever you meet it, and the
           columns cannot drift apart because both take them from SiteRow. -->
      <nldd-table
        background="tinted"
        :accessible-label="t('group.sites.table.label')"
        data-testid="sites-list"
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
          :text="t('group.sites.table.empty')"
          :supporting-text="t('group.sites.table.empty.supportingText')"
          data-testid="sites-empty"
        ></nldd-inline-dialog>
        <SiteRow v-for="site in sites" :key="site.slug" :site="site" />
      </nldd-table>
    </nldd-container>
  </section>
</template>
