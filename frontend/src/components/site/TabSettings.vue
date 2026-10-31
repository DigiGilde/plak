<script setup lang="ts">
/**
 * Instellingen tab of the site page: the title and the address of the site,
 * and for a site admin the danger zone. Anyone with a role on the site may
 * open the tab; each section says who can change what it holds when that is
 * not you.
 */
import { nextTick, onMounted, ref, watch } from 'vue';

import * as plak from '@/api/plak';
import { ApiError } from '@/api/client';
import type { Me, Site } from '@/api/types';
import AddressSection from '@/components/AddressSection.vue';
import ConfirmModal from '@/components/ConfirmModal.vue';
import ErrorBanner from '@/components/ErrorBanner.vue';
import Notices from '@/components/site/Notices.vue';
import { useAutosavedText } from '@/composables/autosavedText';
import { useConfirm } from '@/composables/confirm';
import { fetchCurrentMember } from '@/composables/currentMember';
import { isGroupAdmin, isGroupMember, isSiteAdmin } from '@/composables/roles';
import { type MessageKey, t } from '@/i18n';

// See TabOverview.vue for why inheritAttrs is off on every tab.
defineOptions({ inheritAttrs: false });

const props = defineProps<{ group: string; site: string; contentBase: string }>();

const emit = defineEmits<{
  removed: [];
  changed: [];
  renamed: [Site];
}>();

const loading = ref(true);
const error = ref<unknown>(null);
/** The site, once the data has arrived once; from then on the sections stay mounted. */
const siteInfo = ref<Site | null>(null);
const notices = ref<InstanceType<typeof Notices> | null>(null);

const savedTitle = ref('');
const titleNotice = ref('');

const TITLE_REFUSALS = new Map<string, MessageKey>([
  ['FIELD_EMPTY', 'site.settings.title.required'],
  ['FIELD_TOO_LONG', 'site.settings.title.tooLong'],
  ['FIELD_CONTROL_CHARACTERS', 'site.settings.title.controlCharacters'],
]);

const title = useAutosavedText({
  saved: savedTitle,
  target: () => ({ group: props.group, site: props.site }),
  save: async ({ group, site }, text) => (await plak.setSiteTitle(group, site, text)).title,
  // The cap of the API (TEXT_MAX_LENGTH in api/admin.py).
  maxLength: 200,
  required: 'site.settings.title.required',
  tooLong: 'site.settings.title.tooLong',
  refusals: TITLE_REFUSALS,
  onSaved: () => {
    emit('changed');
    void say(t('site.settings.title.saved'));
  },
  onFailed: (f) =>
    notices.value?.notify(
      'critical',
      t('site.settings.title.saveFailed'),
      errorText(f, t('site.settings.title.saveFailed.detail')),
    ),
});
const titleInput = title.input;
const titleVerdict = title.verdict;
const titleSaved = title.justSaved;

// Decided when the data arrives, not worked out again from the address: the
// roles in `/me` name a site by its address, and while that changes under the
// tab it would take the form away until the data is fetched again.
const canAdmin = ref(false);
// Changing the address needs a role in the group as well: an admin of this site
// alone sees why not.
const canChangeAddress = ref(false);
const siteOnlyAdmin = ref(false);
const redirectDays = ref(0);
/** The other sites in the group, and whether an address one of them gave up may be taken. */
const siblings = ref<Site[]>([]);
const canRelease = ref(false);

async function load(): Promise<void> {
  loading.value = true;
  error.value = null;
  try {
    // The session is not a precondition: without it the sections only lose
    // what needs a role, and that is what an unknown role gets anyway.
    const [detail, loggedIn] = await Promise.all([
      plak.group(props.group),
      fetchCurrentMember().catch(() => null),
    ]);
    const found = detail.sites.find((p) => p.slug === props.site);
    if (!found) {
      error.value = new ApiError({
        type: 'about:blank',
        title: t('site.notFound.title'),
        status: 404,
        detail: t('site.notFound.detail', { site: props.site, group: props.group }),
      });
      return;
    }
    const member = loggedIn as Me | null;
    const admin = isSiteAdmin(member, props.group, props.site);
    const inGroup = isGroupMember(member, props.group);
    canAdmin.value = admin;
    canChangeAddress.value = admin && inGroup;
    siteOnlyAdmin.value = admin && !inGroup;
    canRelease.value = isGroupAdmin(member, props.group);
    redirectDays.value = member?.slugRedirectDays ?? 0;
    siblings.value = detail.sites.filter((p) => p.id !== found.id);
    title.reset(found.title);
    siteInfo.value = found;
  } catch (f) {
    error.value = f;
  } finally {
    loading.value = false;
  }
}

onMounted(load);
watch(
  () => [props.group, props.site],
  () => {
    // Out first, to the site it was typed on: the tab is reused for the next.
    void title.flush();
    // The address was changed from here and the route has followed. Loading
    // would switch the indicator on, which makes the tab inert for as long as
    // it lasts: the button loses the focus and the status line is not heard.
    if (
      siteInfo.value &&
      `${siteInfo.value.groupSlug}/${siteInfo.value.slug}` === `${props.group}/${props.site}`
    ) {
      return;
    }
    void load();
  },
);

function errorText(f: unknown, fallback: string): string {
  return f instanceof ApiError ? (f.problem.detail ?? f.problem.title) : fallback;
}

function inputValue(event: Event): string {
  return (
    (event as CustomEvent<{ value?: string }>).detail?.value ?? (event.target as HTMLInputElement).value
  );
}

function editTitle(event: Event): void {
  title.edit(inputValue(event));
  titleNotice.value = '';
}

/** Emptied first: a live region only speaks when its words change. */
async function say(text: string): Promise<void> {
  titleNotice.value = '';
  await nextTick();
  titleNotice.value = text;
}

function onRenamed(updated: Site): void {
  siteInfo.value = updated;
  // Taken from another site, the address no longer redirects there.
  siblings.value = siblings.value.map((p) => ({
    ...p,
    previousSlugs: p.previousSlugs.filter((previous) => previous.slug !== updated.slug),
  }));
  emit('renamed', updated);
}

async function deleteSite(): Promise<void> {
  try {
    await plak.deleteSite(props.group, props.site);
    emit('removed');
  } catch (f) {
    notices.value?.notify(
      'critical',
      t('site.settings.delete.failed'),
      errorText(f, t('site.settings.delete.failed.detail')),
    );
  }
}

const {
  open: deleteOpen,
  busy: deleteBusy,
  ask: askDelete,
  cancel: cancelDelete,
  confirm: confirmDelete,
} = useConfirm<true>(deleteSite);
</script>

<template>
  <Notices ref="notices" />

  <ErrorBanner v-if="error" :error="error" />

  <!-- The indicator stays mounted and switches on complete, because every
       remount restarts the second it waits before showing itself. -->
  <nldd-activity-indicator
    v-else
    :text="t('site.settings.loading')"
    :complete="!loading || undefined"
  >
    <nldd-container v-if="siteInfo" layout="stack" gap="24">
      <section aria-labelledby="heading-site-title">
        <nldd-container layout="stack" gap="8">
          <nldd-title :size="4">
            <h2 id="heading-site-title">{{ t('site.settings.title.heading') }}</h2>
          </nldd-title>

          <template v-if="canAdmin">
            <nldd-form data-testid="site-title-form" @submit.prevent="title.flush">
              <nldd-form-field :label="t('site.settings.title.label')">
                <nldd-text-field
                  name="site-title"
                  required
                  autocomplete="off"
                  enter-key="done"
                  :value="titleInput"
                  :invalid="titleVerdict !== null || undefined"
                  :valid="titleSaved || undefined"
                  :unmet="titleVerdict !== null ? 'site-title-server' : undefined"
                  data-testid="site-title"
                  @input="editTitle"
                  @change="title.flush"
                ></nldd-text-field>
                <nldd-validation-list>
                  <nldd-validation-item id="site-title-server">
                    {{ titleVerdict }}
                  </nldd-validation-item>
                </nldd-validation-list>
              </nldd-form-field>
            </nldd-form>

            <!-- The field keeps the focus or has lost it already: this is what a
                 screen reader hears of the verdict. -->
            <div
              v-if="titleVerdict"
              role="alert"
              class="visually-hidden"
              data-testid="site-title-alert"
            >
              {{ titleVerdict }}
            </div>

            <!-- The check mark in the field is what a sighted member sees of a
                 save; this is what a screen reader hears of it. -->
            <div role="status" class="visually-hidden" data-testid="site-title-notice">{{ titleNotice }}</div>
          </template>

          <nldd-rich-text v-else>
            <p data-testid="site-title-text">{{ savedTitle }}</p>
            <p>{{ t('site.settings.title.readOnly') }}</p>
          </nldd-rich-text>
        </nldd-container>
      </section>

      <AddressSection
        kind="site"
        :group="group"
        :site="siteInfo.slug"
        :previous-slugs="siteInfo.previousSlugs"
        :content-base="contentBase"
        :redirect-days="redirectDays"
        :can-change="canChangeAddress"
        :site-only-admin="siteOnlyAdmin"
        :is-public="siteInfo.access.base === 'public'"
        :site-id="siteInfo.id"
        :siblings="siblings"
        :can-release="canRelease"
        @renamed="onRenamed($event as Site)"
      />

      <section v-if="canAdmin" aria-labelledby="heading-danger-zone">
        <nldd-box background="critical">
          <nldd-container layout="stack" gap="8" padding="16">
            <nldd-title :size="4"><h2 id="heading-danger-zone">{{ t('site.settings.danger.heading') }}</h2></nldd-title>
            <nldd-container layout="stack" gap="16">
              <nldd-rich-text>
                <p>{{ t('site.settings.danger.body') }}</p>
              </nldd-rich-text>
              <nldd-button-group orientation="horizontal">
                <nldd-button
                  variant="destructive"
                  :text="t('site.settings.danger.action')"
                  data-testid="delete-site"
                  @click="askDelete(true)"
                ></nldd-button>
              </nldd-button-group>
            </nldd-container>
          </nldd-container>
        </nldd-box>
        <ConfirmModal
          :open="deleteOpen"
          :title="t('site.settings.danger.confirm.title', { group: props.group, site: props.site })"
          :text="t('site.settings.danger.confirm.text')"
          :keep-label="t('site.settings.danger.confirm.keep')"
          :confirm-label="t('site.settings.danger.confirm.confirm')"
          :confirm-phrase="`${props.group}/${props.site}`"
          :busy="deleteBusy"
          @confirm="confirmDelete"
          @close="cancelDelete"
        />
      </section>
    </nldd-container>
  </nldd-activity-indicator>
</template>
