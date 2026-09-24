<script setup lang="ts">
/**
 * Device-flow approval for `plak login` (the OAuth device flow). The
 * CLI opens the browser here, with the user code from the terminal in the
 * query string; a human confirms the code matches before approving, so a
 * phished link or a shared code cannot silently grant access.
 *
 * Route: /cli-koppelen (NOT under /-/: the CLI opens this path directly, see
 * router.ts). Public (meta.public), because the session state decides what to
 * show, the same split Landing.vue and Start.vue use.
 */
import { computed, onMounted, ref } from 'vue';
import { useRoute } from 'vue-router';

import { approveDeviceAuthorization, denyDeviceAuthorization, lookupDeviceAuthorization } from '@/api/plak';
import { ApiError } from '@/api/client';
import type { DeviceAuthorization, Member } from '@/api/types';
import ErrorBanner from '@/components/ErrorBanner.vue';
import Landing from './Landing.vue';
import { fetchSession, type Session, type SessionState } from '@/composables/currentMember';
import { formatTimestamp } from '@/format';
import { t } from '@/i18n';

type Stage = 'loading' | 'blocked' | 'error' | 'code-entry' | 'lookup' | 'approved' | 'denied';

const route = useRoute();

const stage = ref<Stage>('loading');
const sessionState = ref<SessionState>('no-session');
const sessionReason = ref('');
const member = ref<Member | null>(null);
const genericError = ref<unknown>(null);

const manualCode = ref('');
const manualTouched = ref(false);
const lookupError = ref<string | null>(null);
const lookupBusy = ref(false);

const authorization = ref<DeviceAuthorization | null>(null);
const actionBusy = ref<'approve' | 'deny' | null>(null);

/** Uppercase, no spaces or hyphens; the backend accepts either form, this UI too. */
function normalizeCode(raw: string): string {
  const stripped = raw.toUpperCase().replace(/[\s-]/g, '');
  return stripped.length > 4 ? `${stripped.slice(0, 4)}-${stripped.slice(4, 8)}` : stripped;
}

function redirectToLogin(rawCode: string | null): void {
  const query = rawCode ? `?code=${encodeURIComponent(rawCode)}` : '';
  window.location.href = `/-/login?returnTo=${encodeURIComponent(`/cli-koppelen${query}`)}`;
}

function codeFromQuery(): string | null {
  const value = route.query.code;
  return typeof value === 'string' && value !== '' ? value : null;
}

async function doLookup(raw: string): Promise<void> {
  const normalized = normalizeCode(raw);
  lookupError.value = null;
  lookupBusy.value = true;
  try {
    authorization.value = await lookupDeviceAuthorization(normalized);
    stage.value = 'lookup';
  } catch (f) {
    if (f instanceof ApiError && f.problem.code === 'SESSION_NOT_FRESH') {
      redirectToLogin(normalized);
      return;
    }
    if (f instanceof ApiError && f.problem.code === 'USER_CODE_UNKNOWN') {
      lookupError.value = t('page.cliPair.error.unknownCode');
      stage.value = 'code-entry';
      return;
    }
    if (f instanceof ApiError && f.problem.status === 429) {
      lookupError.value = f.problem.detail ?? t('page.cliPair.error.tooManyAttempts');
      stage.value = 'code-entry';
      return;
    }
    genericError.value = f;
    stage.value = 'error';
  } finally {
    lookupBusy.value = false;
  }
}

function submitManualCode(): void {
  manualTouched.value = true;
  if (manualCode.value.trim() === '') return;
  void doLookup(manualCode.value);
}

async function respond(action: 'approve' | 'deny'): Promise<void> {
  const code = authorization.value?.userCode;
  if (!code) return;
  actionBusy.value = action;
  try {
    if (action === 'approve') {
      await approveDeviceAuthorization(code);
      stage.value = 'approved';
    } else {
      await denyDeviceAuthorization(code);
      stage.value = 'denied';
    }
  } catch (f) {
    if (f instanceof ApiError && f.problem.code === 'SESSION_NOT_FRESH') {
      redirectToLogin(code);
      return;
    }
    if (f instanceof ApiError && f.problem.code === 'USER_CODE_UNKNOWN') {
      lookupError.value = t('page.cliPair.error.unknownCode');
      authorization.value = null;
      stage.value = 'code-entry';
      return;
    }
    if (f instanceof ApiError && f.problem.status === 429) {
      lookupError.value = f.problem.detail ?? t('page.cliPair.error.tooManyAttempts');
      stage.value = 'code-entry';
      return;
    }
    genericError.value = f;
    stage.value = 'error';
  } finally {
    actionBusy.value = null;
  }
}

function onSessionRefreshed(session: Session): void {
  member.value = session.member;
  sessionState.value = session.state;
  sessionReason.value = session.reason;
  if (session.state === 'active') {
    void afterLogin();
  }
}

async function afterLogin(): Promise<void> {
  const queryCode = codeFromQuery();
  if (queryCode) {
    await doLookup(queryCode);
  } else {
    stage.value = 'code-entry';
  }
}

onMounted(async () => {
  try {
    const session = await fetchSession();
    member.value = session.member;
    sessionState.value = session.state;
    sessionReason.value = session.reason;
    if (session.state === 'no-session') {
      redirectToLogin(codeFromQuery());
      return;
    }
    if (session.state === 'awaiting-activation') {
      stage.value = 'blocked';
      return;
    }
    await afterLogin();
  } catch (f) {
    genericError.value = f;
    stage.value = 'error';
  }
});

// The name comes from the device-flow client itself, so the line says so and
// quotes it: unattributed, it reads as a sentence of ours above the approve
// button.
const clientLine = computed(() => {
  const claimed = authorization.value?.clientName;
  return claimed
    ? t('page.cliPair.confirm.client', { name: claimed })
    : t('page.cliPair.unknownClient');
});
const accountLabel = computed(() => {
  const current = member.value;
  if (!current) return '';
  return current.name ? `${current.name} (${current.email})` : current.email;
});
</script>

<template>
  <nldd-simple-section>
    <nldd-activity-indicator
      v-if="stage === 'loading'"
      :text="t('page.cliPair.loading')"
    ></nldd-activity-indicator>

    <Landing
      v-else-if="stage === 'blocked'"
      :state="sessionState"
      :reason="sessionReason"
      @refreshed="onSessionRefreshed"
    />

    <ErrorBanner v-else-if="stage === 'error'" :error="genericError" />

    <template v-else-if="stage === 'code-entry'">
      <nldd-title :size="1"><h1>{{ t('page.cliPair.title') }}</h1></nldd-title>
      <nldd-rich-text>
        <p>{{ t('page.cliPair.codeEntry.intro') }}</p>
      </nldd-rich-text>
      <nldd-spacer size="16"></nldd-spacer>
      <nldd-form data-testid="code-formulier" @submit.prevent="submitManualCode">
        <nldd-form-field :label="t('page.cliPair.codeEntry.label')">
          <nldd-text-field
            name="code"
            width="14rem"
            :placeholder="t('page.cliPair.codeEntry.placeholder')"
            required
            :value="manualCode"
            :invalid="(manualTouched && manualCode.trim() === '') || lookupError !== null || undefined"
            :unmet="lookupError !== null ? 'code-server' : undefined"
            data-testid="code-invoer"
            @input="
              manualCode = ($event as CustomEvent<{ value?: string }>).detail?.value ?? ($event.target as HTMLInputElement).value
            "
          ></nldd-text-field>
          <nldd-validation-list>
            <nldd-validation-item id="code-vereist" required>
              {{ t('page.cliPair.codeEntry.required') }}
            </nldd-validation-item>
            <nldd-validation-item id="code-server">
              {{ lookupError }}
            </nldd-validation-item>
          </nldd-validation-list>
        </nldd-form-field>
        <nldd-form-actions>
          <nldd-button
            variant="primary"
            type="submit"
            :text="t('page.cliPair.codeEntry.submit')"
            :loading="lookupBusy || undefined"
            data-testid="code-opzoeken"
          ></nldd-button>
        </nldd-form-actions>
      </nldd-form>
    </template>

    <template v-else-if="stage === 'lookup' && authorization">
      <nldd-title :size="1"><h1>{{ t('page.cliPair.title') }}</h1></nldd-title>
      <nldd-rich-text>
        <p>{{ t('page.cliPair.confirm.question') }}</p>
      </nldd-rich-text>
      <nldd-spacer size="8"></nldd-spacer>
      <nldd-code-viewer variant="simple" no-copy data-testid="code-weergave">{{
        authorization.userCode
      }}</nldd-code-viewer>
      <nldd-spacer size="16"></nldd-spacer>
      <nldd-banner
        v-if="accountLabel"
        variant="accent"
        :text="t('page.cliPair.confirm.account', { account: accountLabel })"
        :supporting-text="t('page.cliPair.confirm.accountHint')"
        data-testid="code-account"
      ></nldd-banner>
      <nldd-spacer size="16"></nldd-spacer>
      <nldd-rich-text>
        <p data-testid="code-programma">{{ clientLine }}</p>
        <p data-testid="code-tijdstip">
          {{ t('page.cliPair.confirm.requested', { time: formatTimestamp(authorization.createdAt) }) }}
        </p>
        <p v-if="authorization.ipTruncated" data-testid="code-netwerk">
          {{ t('page.cliPair.confirm.network', { network: authorization.ipTruncated }) }}
        </p>
      </nldd-rich-text>
      <nldd-spacer size="16"></nldd-spacer>
      <template v-if="authorization.sameNetwork === false">
        <nldd-banner
          variant="critical"
          :text="t('page.cliPair.confirm.otherNetwork.title')"
          :supporting-text="t('page.cliPair.confirm.otherNetwork.detail')"
          data-testid="code-ander-netwerk"
        ></nldd-banner>
        <nldd-spacer size="16"></nldd-spacer>
      </template>
      <nldd-banner
        variant="warning"
        :text="t('page.cliPair.confirm.warning.title')"
        :supporting-text="t('page.cliPair.confirm.warning.detail')"
        data-testid="code-waarschuwing"
      ></nldd-banner>
      <nldd-spacer size="16"></nldd-spacer>
      <nldd-button-group orientation="horizontal">
        <nldd-button
          variant="primary"
          :text="t('page.cliPair.confirm.approve')"
          :loading="actionBusy === 'approve' || undefined"
          :disabled="actionBusy === 'deny' || undefined"
          data-testid="code-koppelen"
          @click="respond('approve')"
        ></nldd-button>
        <nldd-button
          variant="secondary"
          :text="t('page.cliPair.confirm.deny')"
          :loading="actionBusy === 'deny' || undefined"
          :disabled="actionBusy === 'approve' || undefined"
          data-testid="code-weigeren"
          @click="respond('deny')"
        ></nldd-button>
      </nldd-button-group>
    </template>

    <template v-else-if="stage === 'approved'">
      <nldd-banner
        variant="success"
        :text="t('page.cliPair.approved.title')"
        :supporting-text="t('page.cliPair.approved.detail')"
        data-testid="code-gekoppeld"
      ></nldd-banner>
    </template>

    <template v-else-if="stage === 'denied'">
      <nldd-banner
        variant="neutral"
        :text="t('page.cliPair.denied.title')"
        :supporting-text="t('page.cliPair.denied.detail')"
        data-testid="code-geweigerd"
      ></nldd-banner>
    </template>
  </nldd-simple-section>
</template>
