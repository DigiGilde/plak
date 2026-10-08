<script setup lang="ts">
/**
 * One failed request, told to the reader.
 *
 * A 401 gets its own treatment. It does not mean something went wrong but that
 * the session is gone, and the only way on is to log in again. Shown as a bare
 * error it is a dead end: "Niet geauthenticeerd. Niet ingelogd." with nothing
 * to press. The way back carries the current path, so logging in returns you
 * to where you were instead of to the overview.
 */
import { computed } from 'vue';

import { ApiError } from '@/api/client';
import { t } from '@/i18n';

const props = defineProps<{
  error: unknown;
  /** On the page of a site or a group: what is not found there may have been renamed. */
  renamedHint?: boolean;
}>();

const expired = computed(
  () => props.error instanceof ApiError && props.error.problem.status === 401,
);

const renamed = computed(
  () =>
    props.renamedHint === true &&
    props.error instanceof ApiError &&
    props.error.problem.status === 404,
);

const title = computed(() => {
  if (expired.value) return t('error.expired.title');
  if (props.error instanceof ApiError) return props.error.problem.title;
  return t('error.generic.title');
});

const detail = computed(() => {
  if (expired.value) return t('error.expired.detail');
  if (props.error instanceof ApiError) {
    return (
      props.error.problem.detail ??
      t('admin.errorBanner.status', { status: props.error.problem.status })
    );
  }
  return t('error.generic.detail');
});

/**
 * A real link, not a router push: logging in leaves the SPA for the identity
 * provider and comes back through the callback, which sends the browser to
 * this path.
 */
const loginHref = computed(
  () => `/-/login?returnTo=${encodeURIComponent(window.location.pathname)}`,
);
</script>

<template>
  <nldd-banner
    :variant="expired ? 'warning' : 'critical'"
    :text="title"
    :supporting-text="detail"
  >
    <nldd-rich-text v-if="renamed" data-testid="renamed-hint">
      <p>{{ t('error.renamed.hint') }}</p>
    </nldd-rich-text>
    <nldd-button
      v-if="renamed"
      slot="actions"
      variant="secondary"
      :text="t('error.renamed.link')"
      href="/"
      data-testid="renamed-overview"
    ></nldd-button>
    <nldd-button
      v-if="expired"
      slot="actions"
      variant="primary"
      :text="t('error.expired.action')"
      :href="loginHref"
      data-testid="sign-in-again"
    ></nldd-button>
  </nldd-banner>
</template>
