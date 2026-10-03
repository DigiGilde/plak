<script setup lang="ts">
/**
 * The member's own account page: who Plak thinks you are, and the one setting
 * that is yours alone, the interface language.
 *
 * Route: /-/profile. The linked sessions keep a page of their own
 * (/-/sessions): the CLI's own documentation points at that address, it is a
 * list with a destructive action per row, and folding it in here would bury
 * the one choice this page exists for. This page links to it instead.
 *
 * The choice is saved on the account, so it holds on every device. The screen
 * switches straight away and rolls back when the call fails, the way the rest
 * of the admin updates optimistically.
 */
import { computed, onMounted, ref } from 'vue';
import { useRoute } from 'vue-router';

import { ApiError } from '@/api/client';
import { setMyLanguage } from '@/api/plak';
import type { MemberLanguage } from '@/api/types';
import Notices from '@/components/site/Notices.vue';
import { setBreadcrumbs } from '@/composables/breadcrumbs';
import { currentMemberState, fetchCurrentMember } from '@/composables/currentMember';
import {
  browserPreference,
  languageChoice,
  setLocale,
  t,
  type LanguageChoice,
} from '@/i18n';
import { PLAK_LOGIN_DOCS_URL } from '@/urls';

const route = useRoute();
const { member } = currentMemberState();

const notices = ref<InstanceType<typeof Notices> | null>(null);
const saving = ref(false);

/** null first: "follow my browser" is where everyone starts. */
const CHOICES: LanguageChoice[] = [null, 'nl', 'en'];

const name = computed(() => member.value?.name?.trim() || t('profile.account.unknown'));
const email = computed(() => member.value?.email ?? '');
const platformRole = computed(() =>
  member.value?.platformRole === 'admin'
    ? t('profile.account.role.admin')
    : t('profile.account.role.member'),
);

function choiceLabel(choice: LanguageChoice): string {
  if (choice === null) return t('profile.language.auto');
  return t(`language.${choice}`);
}

function choiceHint(choice: LanguageChoice): string {
  if (choice === null) {
    return t('profile.language.auto.hint', { language: t(`language.${browserPreference()}`) });
  }
  return t(`profile.language.${choice}.hint`);
}

function savedDetail(choice: LanguageChoice): string {
  if (choice === null) return t('profile.language.saved.detail.auto');
  return choice === 'en'
    ? t('profile.language.saved.detail.en')
    : t('profile.language.saved.detail');
}

function testid(choice: LanguageChoice): string {
  return `language-${choice ?? 'auto'}`;
}

async function choose(choice: LanguageChoice): Promise<void> {
  const previous = languageChoice.value;
  if (choice === previous || saving.value) return;
  saving.value = true;
  // Switch first: the whole point of this control is seeing the language
  // change. The rollback below puts it back if the account refuses.
  setLocale(choice);
  try {
    await setMyLanguage(choice as MemberLanguage | null);
    // The cached session still carries the old value; a later read of it
    // would otherwise resolve the language back to what it was.
    await fetchCurrentMember(true);
    notices.value?.notify('success', t('profile.language.saved'), savedDetail(choice));
  } catch (error) {
    setLocale(previous);
    notices.value?.notify(
      'critical',
      t('profile.language.failed'),
      error instanceof ApiError
        ? (error.problem.detail ?? error.problem.title)
        : t('profile.language.failed.detail'),
    );
  } finally {
    saving.value = false;
  }
}

onMounted(() => {
  setBreadcrumbs(route.path, [{ text: t('nav.overview'), href: '/' }, { text: t('profile.title') }]);
  void fetchCurrentMember().catch(() => null);
});
</script>

<template>
  <nldd-simple-section class="reading-width">
    <Notices ref="notices" />

    <nldd-title :size="1">
      <h1>{{ t('profile.title') }}</h1>
      <span slot="subtitle">{{ t('profile.subtitle') }}</span>
    </nldd-title>

    <nldd-spacer size="24"></nldd-spacer>

    <nldd-container layout="stack" gap="24">
      <section aria-labelledby="heading-account">
        <nldd-container layout="stack" gap="8">
          <nldd-title :size="4">
            <h2 id="heading-account">{{ t('profile.account.heading') }}</h2>
            <span slot="subtitle">{{ t('profile.account.hint') }}</span>
          </nldd-title>
          <nldd-list variant="box-tinted" :accessible-label="t('profile.account.heading')">
            <nldd-list-item size="md" data-testid="profile-name">
              <nldd-text-cell
                :text="`**${t('profile.account.name')}**`"
              ></nldd-text-cell>
              <nldd-text-cell :text="name"></nldd-text-cell>
            </nldd-list-item>
            <nldd-list-item size="md" data-testid="profile-email">
              <nldd-text-cell
                :text="`**${t('profile.account.email')}**`"
              ></nldd-text-cell>
              <nldd-text-cell :text="email"></nldd-text-cell>
            </nldd-list-item>
            <nldd-list-item size="md" data-testid="profile-role">
              <nldd-text-cell
                :text="`**${t('profile.account.role')}**`"
              ></nldd-text-cell>
              <nldd-text-cell :text="platformRole"></nldd-text-cell>
            </nldd-list-item>
          </nldd-list>
        </nldd-container>
      </section>

      <section aria-labelledby="heading-language">
        <nldd-container layout="stack" gap="8">
          <nldd-title :size="4">
            <h2 id="heading-language">{{ t('profile.language.heading') }}</h2>
            <span slot="subtitle">{{ t('profile.language.subtitle') }}</span>
          </nldd-title>
          <nldd-list
            type="radiogroup"
            variant="box-base"
            :accessible-label="t('profile.language.heading')"
            data-testid="language-choice"
          >
            <nldd-list-item
              v-for="choice in CHOICES"
              :key="choice ?? 'auto'"
              radio
              size="md"
              :checked="choice === languageChoice || undefined"
              :data-testid="testid(choice)"
              @change="choose(choice)"
            >
              <!-- The row is the radio itself; the button only draws the shape
                   (decorative), otherwise there is a control inside a control. -->
              <nldd-cell width="fit-content">
                <nldd-radio-button
                  decorative
                  :checked="choice === languageChoice || undefined"
                ></nldd-radio-button>
              </nldd-cell>
              <nldd-spacer-cell size="12"></nldd-spacer-cell>
              <nldd-title-cell
                :size="6"
                :text="choiceLabel(choice)"
                :supporting-text="choiceHint(choice)"
              ></nldd-title-cell>
            </nldd-list-item>
          </nldd-list>
        </nldd-container>
      </section>

      <section aria-labelledby="heading-sessions">
        <nldd-container layout="stack" gap="8">
          <nldd-title :size="4">
            <h2 id="heading-sessions">{{ t('profile.sessions.heading') }}</h2>
            <span slot="subtitle">{{ t('profile.sessions.body') }}</span>
          </nldd-title>
          <nldd-rich-text>
            <p>
              {{ t('profile.sessions.install.before')
              }}<nldd-link :href="PLAK_LOGIN_DOCS_URL" target="_blank" data-testid="profile-cli-install"
                >{{ t('profile.sessions.install.link') }}</nldd-link
              >
            </p>
          </nldd-rich-text>
          <nldd-button
            variant="secondary"
            start-icon="link"
            :text="t('profile.sessions.link')"
            href="/-/sessions"
            data-testid="profile-sessions"
          ></nldd-button>
        </nldd-container>
      </section>
    </nldd-container>
  </nldd-simple-section>
</template>
