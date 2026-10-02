<script setup lang="ts">
// What changed per release, for someone who follows the footer link.
import { computed } from 'vue';

import { formatCalendarDate } from '../format';
import { renderMarkdown } from '../markdown';
import { groupByDate, loadReleases, versionDay } from '../releases';
import { appVersion } from '../version';
import PlatformPage from './PlatformPage.vue';
import { currentLocale, t } from '@/i18n';

// The glob record is a prop only so a test can feed it other notes.
const props = withDefaults(defineProps<{ source?: Record<string, string> }>(), {
  source: undefined,
});

const releases = computed(() => loadReleases(currentLocale.value, props.source));
const days = computed(() => groupByDate(releases.value));

// What the member runs, and whether the notes below are about it. A build
// without a version says nothing; a version that is not a CalVer has no day,
// so it never matches the newest note.
const usingLine = computed(() => {
  const version = appVersion();
  if (version === 'dev') return '';
  const using = t('page.whatsNew.using', { version });
  const newest = days.value[0];
  if (!newest || newest.date === versionDay(version)) return using;
  return `${using} ${t('page.whatsNew.unchanged', { date: formatCalendarDate(newest.date) })}`;
});

// The notes start at `##`; under the date heading (h2) that is an h3.
const Notes = (attrs: { text: string }) => renderMarkdown(attrs.text, 3);
</script>

<template>
  <PlatformPage :title="t('page.whatsNew.title')">
    <p v-if="usingLine">{{ usingLine }}</p>
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
