<script setup lang="ts">
// Privacy statement: what Plak keeps, for how long, who can see
// it and what someone can ask of us.
import PlatformPage from './PlatformPage.vue';
import { t } from '@/i18n';
</script>

<template>
  <PlatformPage :title="t('page.privacy.title')">
    <p>{{ t('page.privacy.intro') }}</p>
    <h2>{{ t('page.privacy.data.heading') }}</h2>
    <ul>
      <li>{{ t('page.privacy.data.account') }}</li>
      <li>{{ t('page.privacy.data.audit') }}</li>
      <li>{{ t('page.privacy.data.visits') }}</li>
    </ul>
    <h2>{{ t('page.privacy.retention.heading') }}</h2>
    <p>{{ t('page.privacy.retention.body') }}</p>
    <p>{{ t('page.privacy.retention.ip') }}</p>
    <p>{{ t('page.privacy.retention.session') }}</p>
    <h2>{{ t('page.privacy.visibility.heading') }}</h2>
    <p>{{ t('page.privacy.visibility.body') }}</p>
    <h2>{{ t('page.privacy.rights.heading') }}</h2>
    <p>
      {{ t('page.privacy.rights.bodyBeforeEmail') }}
      <a href="mailto:digigilde@rijksoverheid.nl">digigilde@rijksoverheid.nl</a
      >{{ t('page.privacy.rights.bodyAfterEmail') }}
    </p>
  </PlatformPage>
</template>
