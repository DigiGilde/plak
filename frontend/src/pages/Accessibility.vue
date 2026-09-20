<script setup lang="ts">
// Accessibility statement (NeRDS toegankelijkheid). Plak is in
// beta and has no entry in the register yet; the wording follows Wies, which
// does: say what works, what does not, and where to report a problem.
import PlatformPage from './PlatformPage.vue';
import { t } from '@/i18n';
</script>

<template>
  <PlatformPage :title="t('page.accessibility.title')">
    <p>{{ t('page.accessibility.intro') }}</p>
    <h2>{{ t('page.accessibility.works.heading') }}</h2>
    <ul>
      <li>{{ t('page.accessibility.works.keyboard') }}</li>
      <li>{{ t('page.accessibility.works.skipLink') }}</li>
      <li>{{ t('page.accessibility.works.colorScheme') }}</li>
      <li>{{ t('page.accessibility.works.checks') }}</li>
    </ul>
    <h2>{{ t('page.accessibility.todo.heading') }}</h2>
    <p>{{ t('page.accessibility.todo.body') }}</p>
    <h2>{{ t('page.accessibility.report.heading') }}</h2>
    <p>
      {{ t('page.accessibility.report.bodyBeforeEmail') }}
      <a href="mailto:digigilde@rijksoverheid.nl">digigilde@rijksoverheid.nl</a
      >{{ t('page.accessibility.report.bodyAfterEmail') }}
    </p>
  </PlatformPage>
</template>
