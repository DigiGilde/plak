<script setup lang="ts">
// What the admin host shows to whoever may not get in. Two of
// the three session states land here: no session, and a session whose member
// was deactivated. Without that distinction a deactivated account would see
// a login button after logging in, and clicking it would return the same
// screen.
import { ref } from 'vue';

import { fetchSession, type Session, type SessionState } from '../composables/currentMember';
import { t } from '@/i18n';

const props = withDefaults(defineProps<{ state?: SessionState; reason?: string }>(), {
  state: 'no-session',
  reason: '',
});

const emit = defineEmits<{ refreshed: [session: Session] }>();

const busy = ref(false);
const notice = ref<'' | 'failed'>('');

async function checkAgain(): Promise<void> {
  busy.value = true;
  notice.value = '';
  try {
    const session = await fetchSession(true);
    // The parent decides what is shown next: an active account goes on to the
    // overview, an expired session back to the login text.
    emit('refreshed', session);
  } catch {
    notice.value = 'failed';
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <nldd-simple-section v-if="props.state === 'awaiting-activation'" class="leesbreedte">
    <nldd-title slot="header" :size="1">
      <h1>{{ t('page.landing.withdrawn.title') }}</h1>
    </nldd-title>
    <nldd-rich-text>
      <p>{{ t('page.landing.withdrawn.intro') }}</p>
      <p v-if="props.reason" data-testid="reden">{{ props.reason }}</p>
      <p>{{ t('page.landing.withdrawn.body') }}</p>
    </nldd-rich-text>
    <nldd-spacer size="24"></nldd-spacer>
    <nldd-button
      variant="primary"
      :text="t('page.landing.withdrawn.check')"
      :loading="busy || undefined"
      data-testid="controleer-opnieuw"
      @click="checkAgain"
    ></nldd-button>
    <template v-if="notice === 'failed'">
      <nldd-spacer size="16"></nldd-spacer>
      <nldd-banner
        variant="warning"
        :text="t('page.landing.withdrawn.failed.title')"
        :supporting-text="t('page.landing.withdrawn.failed.detail')"
        data-testid="melding-mislukt"
      ></nldd-banner>
    </template>
  </nldd-simple-section>

  <nldd-simple-section v-else class="leesbreedte">
    <nldd-title slot="header" :size="1"><h1>Plak</h1></nldd-title>
    <nldd-rich-text>
      <p>{{ t('page.landing.intro') }}</p>
      <p>{{ t('page.landing.loginHint') }}</p>
    </nldd-rich-text>
    <nldd-spacer size="24"></nldd-spacer>
    <nldd-button
      href="/-/login"
      variant="primary"
      :text="t('page.landing.login')"
    ></nldd-button>
    <nldd-spacer size="24"></nldd-spacer>
    <nldd-rich-text>
      <h2>{{ t('page.landing.steps.heading') }}</h2>
      <ol>
        <li>{{ t('page.landing.steps.upload') }}</li>
        <li>{{ t('page.landing.steps.audience') }}</li>
        <li>{{ t('page.landing.steps.share') }}</li>
        <li>{{ t('page.landing.steps.update') }}</li>
      </ol>
      <h2>{{ t('page.landing.site.heading') }}</h2>
      <p>{{ t('page.landing.site.body') }}</p>
    </nldd-rich-text>
  </nldd-simple-section>
</template>
