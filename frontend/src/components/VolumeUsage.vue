<script setup lang="ts">
/**
 * How full the content volume is. Shown to platform
 * administrators on the platform administration page; the endpoint refuses everyone else.
 */
import { computed, onMounted, ref } from 'vue';

import { platformStorage } from '@/api/plak';
import type { Volume } from '@/api/types';
import ErrorBanner from '@/components/ErrorBanner.vue';
import { formatBytes } from '@/format';
import { t } from '@/i18n';

const volume = ref<Volume | null>(null);
const loading = ref(true);
const error = ref<unknown>(null);

// The point where the backend would refuse a deploy of the maximum size, and
// the one /-/healthz reports; with the reserve switched off there is none.
const low = computed(
  () =>
    volume.value !== null &&
    volume.value.reserveBytes > 0 &&
    volume.value.freeBytes < volume.value.reserveBytes + volume.value.maxDeployBytes,
);

onMounted(async () => {
  try {
    volume.value = await platformStorage();
  } catch (e) {
    error.value = e;
  } finally {
    loading.value = false;
  }
});
</script>

<template>
  <section data-testid="content-volume">
    <nldd-title :size="2"><h2>{{ t('admin.volume.heading') }}</h2></nldd-title>

    <nldd-spacer size="16"></nldd-spacer>

    <nldd-activity-indicator
      v-if="loading"
      show-text
      :text="t('admin.volume.loading')"
    ></nldd-activity-indicator>

    <ErrorBanner v-else-if="error" :error="error" />

    <nldd-container v-else-if="volume" layout="stack" gap="16">
      <nldd-banner
        v-if="low"
        variant="critical"
        :text="t('admin.volume.low.title')"
        :supporting-text="
          t('admin.volume.low.detail', {
            maxDeploy: formatBytes(volume.maxDeployBytes),
            reserve: formatBytes(volume.reserveBytes),
          })
        "
      ></nldd-banner>

      <nldd-list variant="box-tinted" :accessible-label="t('admin.volume.summary.label')">
        <nldd-list-item size="md">
          <nldd-title-cell :text="t('admin.volume.used')"></nldd-title-cell>
          <nldd-text-cell
            width="fit-content"
            horizontal-alignment="right"
            :text="
              t('admin.volume.used.value', {
                used: formatBytes(volume.usedBytes),
                total: formatBytes(volume.totalBytes),
              })
            "
          ></nldd-text-cell>
        </nldd-list-item>
        <nldd-list-item size="md">
          <nldd-title-cell :text="t('admin.volume.free')"></nldd-title-cell>
          <nldd-text-cell
            width="fit-content"
            horizontal-alignment="right"
            :color="low ? 'critical' : 'content'"
            :text="formatBytes(volume.freeBytes)"
          ></nldd-text-cell>
        </nldd-list-item>
        <nldd-list-item size="md">
          <nldd-title-cell :text="t('admin.volume.reserve')"></nldd-title-cell>
          <nldd-text-cell
            width="fit-content"
            horizontal-alignment="right"
            :text="
              volume.reserveBytes > 0
                ? formatBytes(volume.reserveBytes)
                : t('admin.volume.reserve.off')
            "
          ></nldd-text-cell>
        </nldd-list-item>
      </nldd-list>
    </nldd-container>
  </section>
</template>
