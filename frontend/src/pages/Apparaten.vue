<script setup lang="ts">
/**
 * Linked devices for the CLI login (`plak login`): every browser-approved
 * device-flow session for the current member, with a way to revoke one.
 * Route: /-/apparaten (see router.ts; the backend keeps its own hard-coded
 * list of SPA pages under /-/, so this route's name has to match there too).
 */
import { computed, onMounted, ref } from 'vue';
import { useRoute } from 'vue-router';

import { cliSessions, revokeCliSession } from '@/api/plak';
import { ApiError } from '@/api/client';
import type { CliSession } from '@/api/types';
import ConfirmModal from '@/components/ConfirmModal.vue';
import ErrorBanner from '@/components/ErrorBanner.vue';
import Notices from '@/components/site/Notices.vue';
import { setBreadcrumbs } from '@/composables/breadcrumbs';
import { formatTimestamp } from '@/format';
import { t } from '@/i18n';

const route = useRoute();

/**
 * The sentence around `plak login`, split on the placeholder so the command
 * keeps its own <code> while the rest of the sentence stays one message.
 */
const subtitle = computed(() => t('admin.devices.subtitle').split('{command}'));

const loading = ref(true);
const error = ref<unknown>(null);
const sessions = ref<CliSession[]>([]);
const notices = ref<InstanceType<typeof Notices> | null>(null);

const revoking = ref<CliSession | null>(null);
const revokeBusy = ref(false);

async function load(): Promise<void> {
  loading.value = true;
  error.value = null;
  try {
    sessions.value = await cliSessions();
  } catch (f) {
    error.value = f;
  } finally {
    loading.value = false;
  }
}

onMounted(() => {
  setBreadcrumbs(route.path, [{ text: t('nav.overview'), href: '/' }, { text: t('nav.devices') }]);
  void load();
});

function errorText(f: unknown, fallback: string): string {
  return f instanceof ApiError ? (f.problem.detail ?? f.problem.title) : fallback;
}

function lastUsedLabel(session: CliSession): string {
  return session.lastUsedAt ? formatTimestamp(session.lastUsedAt) : t('admin.devices.neverUsed');
}

function clientName(session: CliSession | null | undefined): string {
  return session?.clientName || t('admin.devices.unknownClient');
}

function supportingText(session: CliSession): string {
  return t('admin.devices.times', {
    linked: formatTimestamp(session.createdAt),
    lastUsed: lastUsedLabel(session),
    expires: formatTimestamp(session.expiresAt),
  });
}

async function confirmRevoke(): Promise<void> {
  const session = revoking.value;
  if (!session) return;
  revokeBusy.value = true;
  try {
    await revokeCliSession(session.id);
    sessions.value = sessions.value.filter((s) => s.id !== session.id);
    revoking.value = null;
  } catch (f) {
    // The modal sits in the top layer and renders the page below it inert; a
    // notification there would be unreachable. So close first, then notify.
    revoking.value = null;
    notices.value?.notify(
      'critical',
      t('admin.devices.unlinkFailed', { name: clientName(session) }),
      errorText(f, t('admin.devices.unlinkFailed.detail')),
    );
  } finally {
    revokeBusy.value = false;
  }
}
</script>

<template>
  <nldd-simple-section>
    <Notices ref="notices" />

    <nldd-title :size="1">
      <h1>{{ t('nav.devices') }}</h1>
      <span slot="subtitle">{{ subtitle[0] }}<code>plak login</code>{{ subtitle[1] }}</span>
    </nldd-title>

    <nldd-spacer size="24"></nldd-spacer>

    <nldd-activity-indicator
      v-if="loading"
      :text="t('admin.devices.loading')"
    ></nldd-activity-indicator>

    <ErrorBanner v-else-if="error" :error="error" />

    <nldd-list v-else variant="box-tinted" :accessible-label="t('nav.devices')">
      <nldd-inline-dialog
        v-if="sessions.length === 0"
        slot="empty"
        icon="link"
        :text="t('admin.devices.empty')"
        :supporting-text="t('admin.devices.empty.detail')"
        data-testid="apparaten-leeg"
      ></nldd-inline-dialog>
      <nldd-list-item
        v-for="session in sessions"
        :key="session.id"
        size="md"
        :data-testid="`apparaat-${session.id}`"
      >
        <nldd-text-cell
          :text="clientName(session)"
          :supporting-text="supportingText(session)"
        ></nldd-text-cell>
        <nldd-list-item-segment
          button
          :accessible-label="t('admin.devices.unlink.label', { name: clientName(session) })"
          :data-testid="`apparaat-ontkoppelen-${session.id}`"
          @click="revoking = session"
        >
          <nldd-text-cell
            :text="t('admin.devices.unlink')"
            color="critical"
            width="fit-content"
          ></nldd-text-cell>
        </nldd-list-item-segment>
      </nldd-list-item>
    </nldd-list>

    <ConfirmModal
      :open="revoking !== null"
      :title="t('admin.devices.confirm.title', { name: clientName(revoking) })"
      :text="t('admin.devices.confirm.text')"
      :keep-label="t('admin.devices.confirm.keep')"
      :confirm-label="t('admin.devices.confirm.confirm')"
      :busy="revokeBusy"
      @confirm="confirmRevoke"
      @close="revoking = null"
    />
  </nldd-simple-section>
</template>

<style scoped>
/* nldd-rich-text styles its own inline <code> (rich-text.css) with exactly
   this chip; the code here sits outside it (in the title's subtitle),
   where the browser default renders
   monospace noticeably larger than the surrounding text. Matching the design
   system's own look keeps it calm instead of shouting the command. */
code {
  padding: var(--primitives-space-4, 0.25em);
  border-radius: var(--semantics-controls-xs-corner-radius, 0.25em);
  background-color: var(--semantics-surfaces-tinted-background-color, transparent);
  font-family: var(--primitives-font-family-monospace, monospace);
  font-size: 0.85em;
}
</style>
