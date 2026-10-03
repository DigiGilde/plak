<script setup lang="ts">
/**
 * Device-flow approval for `plak login` (the OAuth device flow). The
 * CLI opens the browser here, with the user code from the terminal in the
 * query string; a human confirms the code matches before approving, so a
 * phished link or a shared code cannot silently grant access.
 *
 * Route: /cli-link (NOT under /-/: the CLI opens this path directly, see
 * router.ts). Public (meta.public), because the session state decides what to
 * show, the same split Landing.vue and Start.vue use.
 */
import { onMounted, ref } from 'vue';
import { useRoute } from 'vue-router';

import { approveDeviceAuthorization, denyDeviceAuthorization, lookupDeviceAuthorization } from '@/api/plak';
import { ApiError } from '@/api/client';
import type { DeviceAuthorization } from '@/api/types';
import ErrorBanner from '@/components/ErrorBanner.vue';
import Landing from './Landing.vue';
import { fetchSession, type Session } from '@/composables/currentMember';
import { formatTimestamp } from '@/format';
import { t } from '@/i18n';
import { PLAK_LOGIN_DOCS_URL } from '@/urls';

/**
 * What the page shows. The lookup stage carries the authorization it shows,
 * so the template never meets a lookup without one.
 */
type View =
  | { stage: 'loading' }
  | { stage: 'blocked'; reason: string }
  | { stage: 'error'; error: unknown }
  | { stage: 'login' }
  | { stage: 'code-entry' }
  | { stage: 'lookup'; authorization: DeviceAuthorization }
  | { stage: 'approved' }
  | { stage: 'denied' };

const route = useRoute();

const view = ref<View>({ stage: 'loading' });
const accountLabel = ref('');

const manualCode = ref('');
const manualTouched = ref(false);
const lookupError = ref<string | null>(null);
const lookupBusy = ref(false);

const actionBusy = ref<'approve' | 'deny' | null>(null);

/**
 * Never a script navigation to the login: the backend cannot tell one apart
 * from a login the member started, so a link that lands here could otherwise
 * walk the browser through a silent SSO round trip into a fresh session. The
 * member follows the login link themselves, and the code is not carried
 * through it: afterwards they type the one their own terminal shows.
 * tests/login-navigation.test.ts keeps it that way.
 */
const loginHref = `/-/login?returnTo=${encodeURIComponent('/cli-link')}`;

/** Uppercase, no spaces or hyphens; the backend accepts either form, this UI too. */
function normalizeCode(raw: string): string {
  const stripped = raw.toUpperCase().replace(/[\s-]/g, '');
  return stripped.length > 4 ? `${stripped.slice(0, 4)}-${stripped.slice(4, 8)}` : stripped;
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
    view.value = { stage: 'lookup', authorization: await lookupDeviceAuthorization(normalized) };
  } catch (f) {
    if (f instanceof ApiError && f.problem.code === 'SESSION_NOT_FRESH') {
      view.value = { stage: 'login' };
      return;
    }
    if (f instanceof ApiError && f.problem.code === 'USER_CODE_UNKNOWN') {
      lookupError.value = t('page.cliPair.error.unknownCode');
      view.value = { stage: 'code-entry' };
      return;
    }
    if (f instanceof ApiError && f.problem.status === 429) {
      lookupError.value = f.problem.detail ?? t('page.cliPair.error.tooManyAttempts');
      view.value = { stage: 'code-entry' };
      return;
    }
    view.value = { stage: 'error', error: f };
  } finally {
    lookupBusy.value = false;
  }
}

function submitManualCode(): void {
  manualTouched.value = true;
  if (manualCode.value.trim() === '') return;
  void doLookup(manualCode.value);
}

async function respond(action: 'approve' | 'deny', code: string): Promise<void> {
  actionBusy.value = action;
  try {
    if (action === 'approve') {
      await approveDeviceAuthorization(code);
      view.value = { stage: 'approved' };
    } else {
      await denyDeviceAuthorization(code);
      view.value = { stage: 'denied' };
    }
  } catch (f) {
    if (f instanceof ApiError && f.problem.code === 'SESSION_NOT_FRESH') {
      view.value = { stage: 'login' };
      return;
    }
    if (f instanceof ApiError && f.problem.code === 'USER_CODE_UNKNOWN') {
      lookupError.value = t('page.cliPair.error.unknownCode');
      view.value = { stage: 'code-entry' };
      return;
    }
    if (f instanceof ApiError && f.problem.status === 429) {
      lookupError.value = f.problem.detail ?? t('page.cliPair.error.tooManyAttempts');
      view.value = { stage: 'code-entry' };
      return;
    }
    view.value = { stage: 'error', error: f };
  } finally {
    actionBusy.value = null;
  }
}

/** A member on the session means the account is active; see fetchSession. */
async function applySession(session: Session): Promise<void> {
  const current = session.member;
  if (current) {
    accountLabel.value = current.name ? `${current.name} (${current.email})` : current.email;
    const queryCode = codeFromQuery();
    if (queryCode) {
      await doLookup(queryCode);
    } else {
      view.value = { stage: 'code-entry' };
    }
    return;
  }
  view.value =
    session.state === 'awaiting-activation'
      ? { stage: 'blocked', reason: session.reason }
      : { stage: 'login' };
}

onMounted(async () => {
  try {
    await applySession(await fetchSession());
  } catch (f) {
    view.value = { stage: 'error', error: f };
  }
});

// The name comes from the device-flow client itself, so the line says so and
// quotes it: unattributed, it reads as a sentence of ours above the approve
// button.
function clientLine(authorization: DeviceAuthorization): string {
  const claimed = authorization.clientName;
  return claimed
    ? t('page.cliPair.confirm.client', { name: claimed })
    : t('page.cliPair.unknownClient');
}
</script>

<template>
  <nldd-simple-section class="reading-width">
    <nldd-activity-indicator
      v-if="view.stage === 'loading'"
      :text="t('page.cliPair.loading')"
    ></nldd-activity-indicator>

    <Landing
      v-else-if="view.stage === 'blocked'"
      state="awaiting-activation"
      :reason="view.reason"
      @refreshed="applySession"
    />

    <ErrorBanner v-else-if="view.stage === 'error'" :error="view.error" />

    <template v-else-if="view.stage === 'login'">
      <nldd-title :size="1"><h1>{{ t('page.cliPair.title') }}</h1></nldd-title>
      <nldd-rich-text>
        <p>{{ t('page.cliPair.login.intro') }}</p>
        <p>{{ t('page.cliPair.login.afterwards') }}</p>
      </nldd-rich-text>
      <nldd-spacer size="24"></nldd-spacer>
      <nldd-button
        variant="primary"
        :href="loginHref"
        :text="t('page.cliPair.login.action')"
        data-testid="code-sign-in"
      ></nldd-button>
    </template>

    <template v-else-if="view.stage === 'code-entry'">
      <nldd-title :size="1"><h1>{{ t('page.cliPair.title') }}</h1></nldd-title>
      <nldd-rich-text>
        <p>{{ t('page.cliPair.codeEntry.intro') }}</p>
        <p>
          {{ t('page.cliPair.codeEntry.install.before')
          }}<nldd-link :href="PLAK_LOGIN_DOCS_URL" target="_blank" data-testid="code-cli-install"
            >{{ t('page.cliPair.codeEntry.install.link') }}</nldd-link
          >
        </p>
      </nldd-rich-text>
      <nldd-spacer size="16"></nldd-spacer>
      <nldd-form data-testid="code-form" @submit.prevent="submitManualCode">
        <nldd-form-field :label="t('page.cliPair.codeEntry.label')">
          <nldd-text-field
            name="code"
            width="14rem"
            :placeholder="t('page.cliPair.codeEntry.placeholder')"
            required
            :value="manualCode"
            :invalid="(manualTouched && manualCode.trim() === '') || lookupError !== null || undefined"
            :unmet="lookupError !== null ? 'code-server' : undefined"
            data-testid="code-input"
            @input="
              manualCode = ($event as CustomEvent<{ value?: string }>).detail?.value ?? ($event.target as HTMLInputElement).value
            "
          ></nldd-text-field>
          <nldd-validation-list>
            <nldd-validation-item id="code-required" required>
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
            data-testid="code-lookup"
          ></nldd-button>
        </nldd-form-actions>
      </nldd-form>
    </template>

    <template v-else-if="view.stage === 'lookup'">
      <nldd-title :size="1"><h1>{{ t('page.cliPair.title') }}</h1></nldd-title>
      <nldd-rich-text>
        <p>{{ t('page.cliPair.confirm.question') }}</p>
      </nldd-rich-text>
      <nldd-spacer size="8"></nldd-spacer>
      <nldd-code-viewer variant="simple" no-copy data-testid="code-display">{{
        view.authorization.userCode
      }}</nldd-code-viewer>
      <nldd-spacer size="16"></nldd-spacer>
      <nldd-banner
        variant="accent"
        :text="t('page.cliPair.confirm.account', { account: accountLabel })"
        :supporting-text="t('page.cliPair.confirm.accountHint')"
        data-testid="code-account"
      ></nldd-banner>
      <nldd-spacer size="16"></nldd-spacer>
      <nldd-rich-text>
        <p data-testid="code-program">{{ clientLine(view.authorization) }}</p>
        <p data-testid="code-time">
          {{ t('page.cliPair.confirm.requested', { time: formatTimestamp(view.authorization.createdAt) }) }}
        </p>
        <p v-if="view.authorization.ipTruncated" data-testid="code-network">
          {{ t('page.cliPair.confirm.network', { network: view.authorization.ipTruncated }) }}
        </p>
      </nldd-rich-text>
      <nldd-spacer size="16"></nldd-spacer>
      <template v-if="view.authorization.sameNetwork === false">
        <nldd-banner
          variant="critical"
          :text="t('page.cliPair.confirm.otherNetwork.title')"
          :supporting-text="t('page.cliPair.confirm.otherNetwork.detail')"
          data-testid="code-other-network"
        ></nldd-banner>
        <nldd-spacer size="16"></nldd-spacer>
      </template>
      <nldd-banner
        variant="warning"
        :text="t('page.cliPair.confirm.warning.title')"
        :supporting-text="t('page.cliPair.confirm.warning.detail')"
        data-testid="code-warning"
      ></nldd-banner>
      <nldd-spacer size="16"></nldd-spacer>
      <nldd-button-group orientation="horizontal">
        <nldd-button
          variant="primary"
          :text="t('page.cliPair.confirm.approve')"
          :loading="actionBusy === 'approve' || undefined"
          :disabled="actionBusy === 'deny' || undefined"
          data-testid="code-link"
          @click="respond('approve', view.authorization.userCode)"
        ></nldd-button>
        <nldd-button
          variant="secondary"
          :text="t('page.cliPair.confirm.deny')"
          :loading="actionBusy === 'deny' || undefined"
          :disabled="actionBusy === 'approve' || undefined"
          data-testid="code-deny"
          @click="respond('deny', view.authorization.userCode)"
        ></nldd-button>
      </nldd-button-group>
    </template>

    <template v-else-if="view.stage === 'approved'">
      <nldd-banner
        variant="success"
        :text="t('page.cliPair.approved.title')"
        :supporting-text="t('page.cliPair.approved.detail')"
        data-testid="code-linked"
      ></nldd-banner>
    </template>

    <!-- The one stage left: denied. -->
    <template v-else>
      <nldd-banner
        variant="neutral"
        :text="t('page.cliPair.denied.title')"
        :supporting-text="t('page.cliPair.denied.detail')"
        data-testid="code-denied"
      ></nldd-banner>
    </template>
  </nldd-simple-section>
</template>
