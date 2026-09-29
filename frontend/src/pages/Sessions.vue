<script setup lang="ts">
/**
 * Linked sessions of the CLI login (`plak login`): every browser-approved
 * device-flow session of the current member, with a way to revoke one. One
 * machine can hold several; what a row names is the client program, not a
 * device.
 * Route: /-/sessions (see router.ts; the backend keeps its own hard-coded
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
import { PLAK_LOGIN_DOCS_URL } from '@/urls';

const route = useRoute();

/**
 * The sentence around `plak login`, split on the placeholder so the command
 * keeps its own <code> while the rest of the sentence stays one message.
 */
const subtitle = computed(() => t('admin.sessions.subtitle').split('{command}'));

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
  setBreadcrumbs(route.path, [{ text: t('nav.overview'), href: '/' }, { text: t('nav.sessions') }]);
  void load();
});

function errorText(f: unknown, fallback: string): string {
  return f instanceof ApiError ? (f.problem.detail ?? f.problem.title) : fallback;
}

function lastUsedLabel(session: CliSession): string {
  return session.lastUsedAt ? formatTimestamp(session.lastUsedAt) : t('admin.sessions.neverUsed');
}

function clientName(session: CliSession | null | undefined): string {
  return session?.clientName || t('admin.sessions.unknownClient');
}

function supportingText(session: CliSession): string {
  return t('admin.sessions.times', {
    linked: formatTimestamp(session.createdAt),
    lastUsed: lastUsedLabel(session),
    expires: formatTimestamp(session.expiresAt),
  });
}

async function confirmRevoke(): Promise<void> {
  const session = revoking.value;
  /* v8 ignore start -- the modal only confirms while it is open, so there is a session. */
  if (!session) return;
  /* v8 ignore stop */
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
      t('admin.sessions.revokeFailed', { name: clientName(session) }),
      errorText(f, t('admin.sessions.revokeFailed.detail')),
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
      <h1>{{ t('nav.sessions') }}</h1>
      <span slot="subtitle">{{ subtitle[0] }}<code>plak login</code>{{ subtitle[1] }}</span>
    </nldd-title>

    <nldd-spacer size="24"></nldd-spacer>

    <nldd-activity-indicator
      v-if="loading"
      :text="t('admin.sessions.loading')"
    ></nldd-activity-indicator>

    <ErrorBanner v-else-if="error" :error="error" />

    <template v-else>
      <nldd-list variant="box-tinted" :accessible-label="t('nav.sessions')">
        <nldd-inline-dialog
          v-if="sessions.length === 0"
          slot="empty"
          icon="link"
          :text="t('admin.sessions.empty')"
          :supporting-text="t('admin.sessions.empty.detail')"
          data-testid="sessies-leeg"
        ></nldd-inline-dialog>
        <nldd-list-item
          v-for="session in sessions"
          :key="session.id"
          size="md"
          :data-testid="`sessie-${session.id}`"
        >
          <nldd-text-cell
            :text="clientName(session)"
            :supporting-text="supportingText(session)"
          ></nldd-text-cell>
          <nldd-list-item-segment
            button
            :accessible-label="t('admin.sessions.revoke.label', { name: clientName(session) })"
            :data-testid="`sessie-intrekken-${session.id}`"
            @click="revoking = session"
          >
            <nldd-text-cell
              :text="t('admin.sessions.revoke')"
              color="critical"
              width="fit-content"
            ></nldd-text-cell>
          </nldd-list-item-segment>
        </nldd-list-item>
      </nldd-list>

      <!-- A sibling block, not the inline-dialog's own content slot: that slot
           sits outside the layout that centres the icon and the heading (see
           Sessions.test.ts), which would leave this sentence hanging beside
           them instead of below. -->
      <template v-if="sessions.length === 0">
        <nldd-spacer size="16"></nldd-spacer>
        <nldd-rich-text>
          <p>
            {{ t('admin.sessions.empty.install.before')
            }}<nldd-link :href="PLAK_LOGIN_DOCS_URL" target="_blank" data-testid="sessies-cli-install"
              >{{ t('admin.sessions.empty.install.link') }}</nldd-link
            >
          </p>
        </nldd-rich-text>
      </template>
    </template>

    <ConfirmModal
      :open="revoking !== null"
      :title="t('admin.sessions.confirm.title', { name: clientName(revoking) })"
      :text="t('admin.sessions.confirm.text')"
      :keep-label="t('admin.sessions.confirm.keep')"
      :confirm-label="t('admin.sessions.confirm.confirm')"
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
