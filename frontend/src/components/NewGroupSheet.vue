<script setup lang="ts">
/**
 * Form for "Nieuwe groep": an nldd-sheet with name and slug, the same slug
 * rules as for a site (composables/slug.ts mirrors constants.py). Only a
 * platform admin may do this; the toolbar accordingly shows the menu item to
 * that role only, and the backend refuses it again.
 */
import { nextTick, ref, watch } from 'vue';

import { ApiError } from '@/api/client';
import type { Group } from '@/api/types';
import ErrorBanner from '@/components/ErrorBanner.vue';
import { SLUG_MATCH, SLUG_PATTERN, SLUG_RE, slugify, slugifyTyped } from '@/composables/slug';
import { t } from '@/i18n';

const props = defineProps<{
  open: boolean;
  create: (name: string, slug: string) => Promise<Group>;
}>();

const emit = defineEmits<{
  'update:open': [boolean];
  created: [Group];
}>();

const sheetEl = ref<HTMLElement & { show: () => void; hide: () => void }>();
const name = ref('');
const slug = ref('');
const slugEdited = ref(false);
const busy = ref(false);
const error = ref<unknown>(null);

const nameEmpty = ref(false);
const slugInvalid = ref(false);
const slugServerError = ref<string | null>(null);

function onNameInput(): void {
  if (!slugEdited.value) {
    slug.value = slugify(name.value);
  }
}

/** Normalised on the spot, so a space or a capital cannot survive until the
 * submit that then refuses it. */
function onSlugInput(value: string): void {
  slug.value = slugifyTyped(value);
  slugEdited.value = true;
}

function resetForm(): void {
  name.value = '';
  slug.value = '';
  slugEdited.value = false;
  nameEmpty.value = false;
  slugInvalid.value = false;
  slugServerError.value = null;
  error.value = null;
  busy.value = false;
}

async function onSubmit(): Promise<void> {
  if (busy.value) return;
  error.value = null;
  slugServerError.value = null;
  nameEmpty.value = name.value.trim() === '';
  slugInvalid.value = !SLUG_RE.test(slug.value);
  if (nameEmpty.value || slugInvalid.value) return;
  busy.value = true;
  try {
    const group = await props.create(name.value, slug.value);
    emit('created', group);
    resetForm();
    emit('update:open', false);
  } catch (e) {
    if (e instanceof ApiError && (e.problem.status === 422 || e.problem.status === 409)) {
      slugServerError.value = e.problem.detail ?? e.problem.title;
    } else {
      error.value = e;
    }
  } finally {
    busy.value = false;
  }
}

function onCancel(): void {
  emit('update:open', false);
}

function onClose(): void {
  resetForm();
  emit('update:open', false);
}

watch(
  () => props.open,
  async (open) => {
    if (!open) {
      sheetEl.value?.hide?.();
      return;
    }
    await nextTick();
    sheetEl.value?.show?.();
  },
  { immediate: true },
);
</script>

<template>
  <!-- Overlays belong on the document root (NLDD), see PublishSheet. -->
  <Teleport to="body">
    <nldd-sheet
      ref="sheetEl"
      placement="right"
      :accessible-label="t('group.new.title')"
      @close="onClose"
    >
      <!-- nldd-page brings the scroller the sheet expects; see PublishSheet. -->
      <nldd-page>
        <nldd-container padding="24" gap="16">
          <nldd-title :size="4"><h2>{{ t('group.new.title') }}</h2></nldd-title>

          <ErrorBanner v-if="error" :error="error" />

          <nldd-form @submit.prevent="onSubmit">
            <nldd-form-field :label="t('group.new.name.label')">
              <nldd-text-field
                name="name"
                :value="name"
                required
                :invalid="nameEmpty || undefined"
                @input="(e: CustomEvent) => { name = (e.detail?.value ?? (e.target as HTMLInputElement).value); onNameInput(); }"
              ></nldd-text-field>
              <nldd-validation-list>
                <nldd-validation-item id="group-name-required" required>
                  {{ t('group.new.name.required') }}
                </nldd-validation-item>
              </nldd-validation-list>
            </nldd-form-field>

            <nldd-form-field :label="t('group.new.slug.label')">
              <nldd-text-field
                name="slug"
                :value="slug"
                required
                :pattern="SLUG_PATTERN"
                :invalid="slugInvalid || slugServerError !== null || undefined"
                :unmet="slugServerError !== null ? 'group-slug-server' : undefined"
                @input="(e: CustomEvent) => onSlugInput(e.detail?.value ?? (e.target as HTMLInputElement).value)"
              ></nldd-text-field>
              <!-- The list normally reads the value off the field on every
                   input event; here the slug is also derived from the name, and
                   it does not see that change. `value` hands it the value the app
                   keeps. -->
              <nldd-validation-list :value="slug">
                <nldd-validation-item id="group-slug-required" required>
                  {{ t('group.new.slug.required') }}
                </nldd-validation-item>
                <nldd-validation-item id="group-slug-format" hint :match="SLUG_MATCH">
                  {{ t('group.new.slug.pattern') }}
                </nldd-validation-item>
                <nldd-validation-item id="group-slug-server">
                  {{ slugServerError }}
                </nldd-validation-item>
              </nldd-validation-list>
            </nldd-form-field>

            <nldd-form-actions>
              <nldd-button-group>
                <nldd-button
                  variant="secondary"
                  :text="t('group.new.cancel')"
                  type="button"
                  @click="onCancel"
                ></nldd-button>
                <nldd-button
                  :text="t('group.action.newGroup')"
                  variant="primary"
                  type="submit"
                ></nldd-button>
              </nldd-button-group>
            </nldd-form-actions>
          </nldd-form>
        </nldd-container>
      </nldd-page>
    </nldd-sheet>
  </Teleport>
</template>
