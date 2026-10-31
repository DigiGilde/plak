<script setup lang="ts">
// Accessibility statement (NeRDS toegankelijkheid), modelled on the one of
// the Digi Handboek: norm, status, how it was tested, known limitations,
// where to report and who enforces.
import PlatformPage from './PlatformPage.vue';
import { useOperator } from '@/composables/operator';
import { t } from '@/i18n';

const operator = useOperator();
</script>

<template>
  <PlatformPage :title="t('page.accessibility.title')">
    <p>{{ t('page.accessibility.intro') }}</p>
    <p>
      <strong>{{ t('page.accessibility.standard.before') }}</strong
      ><a href="https://www.toegankelijkheidsverklaring.nl">{{
        t('page.accessibility.standard.link')
      }}</a
      ><strong>{{ t('page.accessibility.standard.after') }}</strong>
    </p>
    <h2>{{ t('page.accessibility.status.heading') }}</h2>
    <p>{{ t('page.accessibility.status.body') }}</p>
    <h2>{{ t('page.accessibility.tested.heading') }}</h2>
    <p>{{ t('page.accessibility.tested.components') }}</p>
    <p>{{ t('page.accessibility.tested.automated') }}</p>
    <p>{{ t('page.accessibility.tested.notDone') }}</p>
    <h2>{{ t('page.accessibility.limits.heading') }}</h2>
    <ul>
      <li>{{ t('page.accessibility.limits.contrast') }}</li>
      <li>{{ t('page.accessibility.limits.pages') }}</li>
      <li>{{ t('page.accessibility.limits.shadow') }}</li>
      <li>{{ t('page.accessibility.limits.published') }}</li>
    </ul>
    <h2>{{ t('page.accessibility.report.heading') }}</h2>
    <p v-if="operator?.email">
      {{ t('page.accessibility.report.bodyBeforeEmail') }}
      <a :href="`mailto:${operator.email}`">{{ operator.email }}</a
      >{{ t('page.accessibility.report.bodyAfterEmail') }}
    </p>
    <p v-else>{{ t('page.accessibility.report.unnamed') }}</p>
    <h2>{{ t('page.accessibility.enforcement.heading') }}</h2>
    <p>
      {{ t('page.accessibility.enforcement.before')
      }}<a href="https://www.mensenrechten.nl">{{ t('page.accessibility.enforcement.link') }}</a
      >{{ t('page.accessibility.enforcement.after') }}
    </p>
    <p>{{ t('page.accessibility.prepared') }}</p>
  </PlatformPage>
</template>
