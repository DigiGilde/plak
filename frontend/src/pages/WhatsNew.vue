<script setup lang="ts">
// What changed per release, for someone who follows the footer link.
import { computed } from 'vue';

import { formatCalendarDate } from '../format';
import { renderMarkdown } from '../markdown';
import { groupByDate, loadReleases } from '../releases';
import PlatformPage from './PlatformPage.vue';
import { currentLocale, t } from '@/i18n';

// The glob record is a prop only so a test can feed it other notes.
const props = withDefaults(defineProps<{ source?: Record<string, string> }>(), {
  source: undefined,
});

const releases = computed(() => loadReleases(currentLocale.value, props.source));
const days = computed(() => groupByDate(releases.value));

// The notes start at `##`; under the date heading (h2) that is an h3.
const Notes = (attrs: { text: string }) => renderMarkdown(attrs.text, 3);
</script>

<template>
  <PlatformPage :title="t('page.whatsNew.title')">
    <p v-if="releases.length === 0">{{ t('page.whatsNew.empty') }}</p>
    <!-- Flat on purpose: nldd-rich-text only spaces its direct children, so no
         wrapper element may sit between it and these headings and notes. -->
    <template v-for="day in days" :key="day.date">
      <h2 :id="`d${day.date}`">
        <time :datetime="day.date">{{ formatCalendarDate(day.date) }}</time>
      </h2>
      <Notes v-for="(text, index) in day.texts" :key="index" :text="text" />
    </template>
  </PlatformPage>
</template>
