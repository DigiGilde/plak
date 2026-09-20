<script setup lang="ts">
// What Plak is, for someone who lands here from the footer.
import PlatformPage from './PlatformPage.vue';
import { t } from '@/i18n';
</script>

<template>
  <PlatformPage :title="t('page.about.title')">
    <p>{{ t('page.about.intro') }}</p>
    <h2>{{ t('page.about.features.heading') }}</h2>
    <ul>
      <li>
        <strong>{{ t('page.about.features.share.term') }}</strong>
        {{ t('page.about.features.share.body') }}
      </li>
      <li>
        <strong>{{ t('page.about.features.update.term') }}</strong>
        {{ t('page.about.features.update.body') }}
      </li>
      <li>
        <strong>{{ t('page.about.features.audience.term') }}</strong>
        {{ t('page.about.features.audience.body') }}
      </li>
      <li>
        <strong>{{ t('page.about.features.revoke.term') }}</strong>
        {{ t('page.about.features.revoke.body') }}
      </li>
      <li>
        <strong>{{ t('page.about.features.site.term') }}</strong>
        {{ t('page.about.features.site.body') }}
      </li>
      <li>
        <strong>{{ t('page.about.features.cli.term') }}</strong>
        {{ t('page.about.features.cli.body') }}
      </li>
    </ul>
    <h2>{{ t('page.about.name.heading') }}</h2>
    <p>{{ t('page.about.name.body') }}</p>
  </PlatformPage>
</template>
