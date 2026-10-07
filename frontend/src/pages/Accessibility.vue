<script setup lang="ts">
// Accessibility statement (NeRDS toegankelijkheid), modelled on the one of
// the Digi Handboek: norm, status, how it was tested, known limitations,
// where to report and who enforces.
import PlatformPage from './PlatformPage.vue';
import { t } from '@/i18n';
</script>

<template>
  <PlatformPage :title="t('page.accessibility.title')">
    <p>{{ t('page.accessibility.intro') }}</p>
    <p>
      <strong>{{ t('page.accessibility.draft.before') }}</strong
      ><a href="https://www.toegankelijkheidsverklaring.nl">{{
        t('page.accessibility.draft.link')
      }}</a
      ><strong>{{ t('page.accessibility.draft.after') }}</strong>
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
    <p>
      {{ t('page.accessibility.report.bodyBeforeEmail') }}
      <a href="mailto:digigilde@rijksoverheid.nl">digigilde@rijksoverheid.nl</a
      >{{ t('page.accessibility.report.bodyAfterEmail') }}
    </p>
    <h2>{{ t('page.accessibility.enforcement.heading') }}</h2>
    <p>
      {{ t('page.accessibility.enforcement.before')
      }}<a href="https://www.mensenrechten.nl">{{ t('page.accessibility.enforcement.link') }}</a
      >{{ t('page.accessibility.enforcement.after') }}
    </p>
    <p>{{ t('page.accessibility.prepared') }}</p>
  </PlatformPage>
</template>
