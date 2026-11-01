<script setup lang="ts">
/** How an upload is going, right below the field that received it. */
import type { IntakeStep } from './uploadIntake';
import { t } from '@/i18n';

defineProps<{
  step: IntakeStep;
  progress: { done: number; total: number } | null;
  ready: string;
  error: string | null;
}>();
</script>

<template>
  <nldd-progress-bar
    v-if="step !== ''"
    :text="
      step === 'reading'
        ? t('publish.upload.progress.reading')
        : t('publish.upload.progress.packing')
    "
    :value="progress?.done ?? 0"
    :max="progress?.total || 1"
    :indeterminate="step === 'reading' || undefined"
    data-testid="drag-progress"
  ></nldd-progress-bar>

  <!-- nldd-banner announces itself (role="status"), so the outcome of a
       drop is reported without a separate live region. -->
  <nldd-banner
    v-if="ready && step === ''"
    variant="success"
    size="sm"
    :text="t('publish.upload.ready.title')"
    :supporting-text="ready"
    data-testid="drag-done"
  ></nldd-banner>

  <nldd-banner
    v-if="error"
    variant="warning"
    size="sm"
    :text="t('publish.upload.error.title')"
    :supporting-text="error"
    data-testid="drag-error"
  ></nldd-banner>
</template>
