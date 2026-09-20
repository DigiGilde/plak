<script setup lang="ts">
// Decides what '/' shows: the site overview after login, the landing page for
// a visitor who is not logged in. The landing page on the content host's root
// is a backend platform page (platform/pages.py); this is the beheer-side
// equivalent of it.
import { onMounted, ref } from 'vue';

import { fetchSession, type Session, type SessionState } from '../composables/currentMember';
import Landing from './Landing.vue';
import Overview from './Overview.vue';
import { t } from '@/i18n';

const loading = ref(true);
const state = ref<SessionState>('no-session');
const reason = ref('');

function takeOver(session: Session): void {
  state.value = session.state;
  reason.value = session.reason;
}

onMounted(async () => {
  try {
    takeOver(await fetchSession());
  } catch {
    // Unexpected error (not 401 or 403) while resolving the session: fall
    // back to the landing page instead of loading forever.
    takeOver({ state: 'no-session', member: null, reason: '' });
  } finally {
    loading.value = false;
  }
});
</script>

<template>
  <nldd-activity-indicator
    v-if="loading"
    show-text
    :text="t('page.start.loading')"
  ></nldd-activity-indicator>
  <Overview v-else-if="state === 'active'" />
  <Landing v-else :state="state" :reason="reason" @refreshed="takeOver" />
</template>
