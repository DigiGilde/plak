<script setup lang="ts">
/**
 * Instellingen tab of the site page: the title of the site, and for a site
 * admin the danger zone. Anyone with a role on the site may open the tab; each
 * section says who can change what it holds when that is not you.
 */
import { computed, nextTick, onMounted, ref, watch } from 'vue';

import * as plak from '@/api/plak';
import { ApiError } from '@/api/client';
import type { Me } from '@/api/types';
import ConfirmModal from '@/components/ConfirmModal.vue';
import ErrorBanner from '@/components/ErrorBanner.vue';
import Notices from '@/components/site/Notices.vue';
import { fetchCurrentMember } from '@/composables/currentMember';
import { isSiteAdmin } from '@/composables/roles';
import { type MessageKey, t } from '@/i18n';

// See TabOverview.vue for why inheritAttrs is off on every tab.
defineOptions({ inheritAttrs: false });

const props = defineProps<{ group: string; site: string }>();

const emit = defineEmits<{
  removed: [];
  changed: [];
}>();

const loading = ref(true);
const error = ref<unknown>(null);
/** Whether the data has arrived once; from then on the sections stay mounted. */
const loaded = ref(false);
const me = ref<Me | null>(null);
const notices = ref<InstanceType<typeof Notices> | null>(null);

const savedTitle = ref('');
const titleInput = ref('');
const titleError = ref<string | null>(null);
const titleBusy = ref(false);
const titleNotice = ref('');
const titleField = ref<HTMLElement | null>(null);

const deleteOpen = ref(false);
const deleteBusy = ref(false);

const canAdmin = computed(() => isSiteAdmin(me.value, props.group, props.site));

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
    me.value = loggedIn as Me | null;
    savedTitle.value = found.title;
    titleInput.value = found.title;
    titleError.value = null;
    loaded.value = true;
  } catch (f) {
    error.value = f;
  } finally {
    loading.value = false;
  }
}

onMounted(load);
watch(() => [props.group, props.site], load);

function errorText(f: unknown, fallback: string): string {
  return f instanceof ApiError ? (f.problem.detail ?? f.problem.title) : fallback;
}

function inputValue(event: Event): string {
  return (
    (event as CustomEvent<{ value?: string }>).detail?.value ?? (event.target as HTMLInputElement).value
  );
}

const TITLE_REFUSALS = new Map<string, MessageKey>([
  ['FIELD_EMPTY', 'site.settings.title.required'],
  ['FIELD_TOO_LONG', 'site.settings.title.tooLong'],
  ['FIELD_CONTROL_CHARACTERS', 'site.settings.title.controlCharacters'],
]);

function editTitle(event: Event): void {
  titleInput.value = inputValue(event);
  titleError.value = null;
  titleNotice.value = '';
}

/** Emptied first: a live region only speaks when its words change. */
async function say(text: string): Promise<void> {
  titleNotice.value = '';
  await nextTick();
  titleNotice.value = text;
}

async function saveTitle(): Promise<void> {
  if (titleBusy.value) return;
  titleNotice.value = '';
  const title = titleInput.value.trim();
  if (title === savedTitle.value) {
    await say(t('site.settings.title.unchanged'));
    return;
  }
  titleBusy.value = true;
  titleError.value = null;
  try {
    const updated = await plak.setSiteTitle(props.group, props.site, title);
    savedTitle.value = updated.title;
    titleInput.value = updated.title;
    emit('changed');
    await say(t('site.settings.title.saved', { title: updated.title }));
  } catch (f) {
    const refusal = f instanceof ApiError ? TITLE_REFUSALS.get(f.problem.code ?? '') : undefined;
    if (refusal) {
      titleError.value = t(refusal);
      // After the render, so the field is already described by its verdict
      // when the focus lands on it.
      await nextTick();
      titleField.value?.focus();
    } else {
      notices.value?.notify(
        'critical',
        t('site.settings.title.saveFailed'),
        errorText(f, t('site.settings.title.saveFailed.detail')),
      );
    }
  } finally {
    titleBusy.value = false;
  }
}

async function deleteSite(): Promise<void> {
  deleteBusy.value = true;
  try {
    await plak.deleteSite(props.group, props.site);
    deleteOpen.value = false;
    emit('removed');
  } catch (f) {
    // The modal sits in the top layer and renders the page below it inert; a
    // notification there would be unreachable. So close first, then notify.
    deleteOpen.value = false;
    notices.value?.notify(
      'critical',
      t('site.settings.delete.failed'),
      errorText(f, t('site.settings.delete.failed.detail')),
    );
  } finally {
    deleteBusy.value = false;
  }
}
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
    <nldd-container v-if="loaded" layout="stack" gap="24">
      <section aria-labelledby="heading-site-title">
        <nldd-container layout="stack" gap="8">
          <nldd-title :size="4">
            <h2 id="heading-site-title">{{ t('site.settings.title.heading') }}</h2>
          </nldd-title>

          <template v-if="canAdmin">
            <nldd-form data-testid="site-title-form" @submit.prevent="saveTitle">
              <nldd-form-field :label="t('site.settings.title.label')">
                <nldd-text-field
                  ref="titleField"
                  name="site-title"
                  required
                  autocomplete="off"
                  :value="titleInput"
                  :invalid="titleError !== null || undefined"
                  :unmet="titleError !== null ? 'site-title-server' : undefined"
                  data-testid="site-title"
                  @input="editTitle"
                ></nldd-text-field>
                <nldd-validation-list>
                  <nldd-validation-item id="site-title-required" required>
                    {{ t('site.settings.title.required') }}
                  </nldd-validation-item>
                  <nldd-validation-item id="site-title-length" hint>
                    {{ t('site.settings.title.tooLong') }}
                  </nldd-validation-item>
                  <nldd-validation-item id="site-title-server">
                    {{ titleError }}
                  </nldd-validation-item>
                </nldd-validation-list>
              </nldd-form-field>
              <nldd-form-actions>
                <nldd-button
                  variant="primary"
                  type="submit"
                  :text="t('site.settings.title.save')"
                  :loading="titleBusy || undefined"
                  data-testid="site-title-save"
                ></nldd-button>
              </nldd-form-actions>
            </nldd-form>

            <!-- After Enter the field has the focus already, so focusing it says
                 nothing: this is what a screen reader hears of the verdict. -->
            <div
              v-if="titleError"
              role="alert"
              class="visually-hidden"
              data-testid="site-title-alert"
            >
              {{ titleError }}
            </div>

            <nldd-text size="sm" role="status" data-testid="site-title-notice">{{ titleNotice }}</nldd-text>
          </template>

          <nldd-rich-text v-else>
            <p data-testid="site-title-text">{{ savedTitle }}</p>
            <p>{{ t('site.settings.title.readOnly') }}</p>
          </nldd-rich-text>
        </nldd-container>
      </section>

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
                  @click="deleteOpen = true"
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
          @confirm="deleteSite"
          @close="deleteOpen = false"
        />
      </section>
    </nldd-container>
  </nldd-activity-indicator>
</template>
