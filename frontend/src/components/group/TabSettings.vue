<script setup lang="ts">
/**
 * Instellingen tab of the group page: the name of the group, which a group
 * admin can change, the access new sites in this group start with, base and
 * extras together, and for a group admin the danger zone.
 *
 * The choice sits inline on the tab rather than in a sheet: the tab is the
 * place for this setting, and a sheet over it would show the same options a
 * second time. The rows are optimistic (the choice is there straight away, the
 * API confirms behind it, on failure it rolls back with a notification), just
 * like the Toegang tab of a site.
 */
import { computed, nextTick, ref, watch } from 'vue';

import { errorText } from '@/api/client';
import * as api from '@/api/plak';
import type { Access, AccessBase, Group, PreviousSlug, Site } from '@/api/types';
import { ACCESS_BASE_VALUES } from '@/api/types';
import AddressSection from '@/components/AddressSection.vue';
import ConfirmModal from '@/components/ConfirmModal.vue';
import Notices from '@/components/site/Notices.vue';
import { useAutosavedText } from '@/composables/autosavedText';
import {
  accessBaseHint,
  accessBaseLabel,
  accessSummary,
  inviteesLabel,
  keysLabel,
  nobodyCanView,
} from '@/format';
import { type MessageKey, t } from '@/i18n';
import SectionHeading from '@/components/SectionHeading.vue';

// The tab has several roots (notifications beside the content) and receives
// the props of every tab; none of them should fall through to the markup.
defineOptions({ inheritAttrs: false });

const props = defineProps<{
  group: string;
  groupName: string;
  access: Access;
  sites: Site[];
  previousSlugs: PreviousSlug[];
  contentBase: string;
  /** How many days an old address keeps redirecting. */
  redirectDays: number;
  canDelete: boolean;
  canRename: boolean;
  canChangeAddress: boolean;
}>();

const emit = defineEmits<{
  groupChanged: [Group];
  renamed: [Group];
  removed: [];
}>();

const hasPublicSite = computed(() => props.sites.some((site) => site.access.base === 'public'));

const notices = ref<InstanceType<typeof Notices> | null>(null);
const chosen = ref<Access>(props.access);

const savedName = ref(props.groupName);
const nameNotice = ref('');

const NAME_REFUSALS = new Map<string, MessageKey>([
  ['FIELD_EMPTY', 'group.settings.name.required'],
  ['FIELD_TOO_LONG', 'group.settings.name.tooLong'],
  ['FIELD_CONTROL_CHARACTERS', 'group.settings.name.controlCharacters'],
]);

const name = useAutosavedText({
  saved: savedName,
  target: () => props.group,
  save: async (group, text) => {
    const updated = await api.setGroupName(group, text);
    emit('groupChanged', updated);
    return updated.name;
  },
  // The cap of the API (TEXT_MAX_LENGTH in api/admin.py).
  maxLength: 200,
  required: 'group.settings.name.required',
  tooLong: 'group.settings.name.tooLong',
  refusals: NAME_REFUSALS,
  onSaved: () => void say(t('group.settings.name.saved')),
  onFailed: (f) =>
    notices.value?.notify(
      'critical',
      t('group.settings.name.saveFailed'),
      errorText(f, t('group.settings.name.saveFailed.detail')),
    ),
});
const nameInput = name.input;
const nameVerdict = name.verdict;
const nameSaved = name.justSaved;

watch(
  () => props.group,
  () => {
    // Out first, to the group it was typed on: the tab is reused for the next.
    void name.flush();
    name.reset(props.groupName);
  },
);

function inputValue(event: Event): string {
  return (
    (event as CustomEvent<{ value?: string }>).detail?.value ?? (event.target as HTMLInputElement).value
  );
}

function editName(event: Event): void {
  name.edit(inputValue(event));
  nameNotice.value = '';
}

/** Emptied first: a live region only speaks when its words change. */
async function say(text: string): Promise<void> {
  nameNotice.value = '';
  await nextTick();
  nameNotice.value = text;
}

watch(
  () => props.access,
  (value) => {
    chosen.value = value;
  },
);

async function save(next: Access): Promise<void> {
  const previous = chosen.value;
  chosen.value = next;
  try {
    const updated = await api.setGroupDefaultAccess(props.group, next);
    chosen.value = updated.defaultAccess;
    emit('groupChanged', updated);
    notices.value?.notify(
      'success',
      t('group.settings.saved'),
      accessSummary(updated.defaultAccess),
    );
  } catch (f) {
    chosen.value = previous;
    notices.value?.notify(
      'critical',
      t('group.settings.saveFailed'),
      errorText(f, t('group.settings.saveFailed.detail')),
    );
  }
}

const deleteOpen = ref(false);
const deleteBusy = ref(false);

/** Named in the dialog; beyond that a count, so a big group keeps it short. */
const SITES_NAMED = 5;
const namedSites = computed(() => props.sites.slice(0, SITES_NAMED));
const unnamedSites = computed(() => props.sites.length - namedSites.value.length);
const unnamedText = computed(() =>
  unnamedSites.value === 1
    ? t('group.settings.danger.confirm.sites.more.one')
    : t('group.settings.danger.confirm.sites.more.many', { count: unnamedSites.value }),
);

async function deleteGroup(): Promise<void> {
  deleteBusy.value = true;
  try {
    await api.deleteGroup(props.group);
    deleteOpen.value = false;
    emit('removed');
  } catch (f) {
    // Close first: the modal renders the page below it inert, so a
    // notification there would be unreachable (see site/TabSettings).
    deleteOpen.value = false;
    notices.value?.notify(
      'critical',
      t('group.settings.delete.failed'),
      errorText(f, t('group.settings.delete.failed.detail')),
    );
  } finally {
    deleteBusy.value = false;
  }
}

function chooseBase(base: AccessBase): void {
  if (base === chosen.value.base) return;
  void save({ ...chosen.value, base });
}

function toggle(field: 'keys' | 'invitees', event: Event): void {
  const next = Boolean((event as CustomEvent<{ checked?: boolean }>).detail?.checked);
  if (next === chosen.value[field]) return;
  void save({ ...chosen.value, [field]: next });
}
</script>

<template>
  <Notices ref="notices" />

  <section aria-labelledby="heading-group-name">
    <nldd-container layout="stack" gap="8">
      <nldd-title :size="4">
        <h2 id="heading-group-name">{{ t('group.settings.name.heading') }}</h2>
      </nldd-title>

      <template v-if="canRename">
        <nldd-form data-testid="group-name-form" @submit.prevent="name.flush">
          <nldd-form-field :label="t('group.settings.name.label')">
            <nldd-text-field
              name="group-name"
              required
              autocomplete="off"
              enter-key="done"
              :value="nameInput"
              :invalid="nameVerdict !== null || undefined"
              :valid="nameSaved || undefined"
              :unmet="nameVerdict !== null ? 'group-settings-name-server' : undefined"
              data-testid="group-name"
              @input="editName"
              @change="name.flush"
            ></nldd-text-field>
            <nldd-validation-list>
              <nldd-validation-item id="group-settings-name-server">
                {{ nameVerdict }}
              </nldd-validation-item>
            </nldd-validation-list>
          </nldd-form-field>
        </nldd-form>

        <!-- The field keeps the focus or has lost it already: this is what a
             screen reader hears of the verdict. -->
        <div v-if="nameVerdict" role="alert" class="visually-hidden" data-testid="group-name-alert">
          {{ nameVerdict }}
        </div>

        <!-- The check mark in the field is what a sighted member sees of a
             save; this is what a screen reader hears of it. -->
        <div role="status" class="visually-hidden" data-testid="group-name-notice">{{ nameNotice }}</div>
      </template>

      <nldd-rich-text v-else>
        <p data-testid="group-name-text">{{ groupName }}</p>
        <p>{{ t('group.settings.name.readOnly') }}</p>
      </nldd-rich-text>
    </nldd-container>
  </section>

  <nldd-spacer size="24"></nldd-spacer>

  <AddressSection
    kind="group"
    :group="group"
    :previous-slugs="previousSlugs"
    :content-base="contentBase"
    :redirect-days="redirectDays"
    :can-change="canChangeAddress"
    :is-public="hasPublicSite"
    :sites="sites"
    @renamed="emit('renamed', $event as Group)"
  />

  <nldd-spacer size="24"></nldd-spacer>

  <section aria-labelledby="heading-default-access">
    <nldd-container layout="stack" gap="8">
      <SectionHeading id="heading-default-access" :text="t('group.settings.heading')" :intro="t('group.settings.intro')" />

      <nldd-list
        type="radiogroup"
        variant="box-base"
        :accessible-label="t('group.settings.list.label')"
        data-testid="default-access-group"
      >
        <nldd-list-item
          v-for="w in ACCESS_BASE_VALUES"
          :key="w"
          radio
          size="md"
          :checked="w === chosen.base || undefined"
          :data-testid="`default-access-${w}`"
          @change="chooseBase(w)"
        >
          <!-- The row is the radio itself; the button only draws the shape
               (decorative), otherwise there is a control inside a control. -->
          <nldd-cell width="fit-content">
            <nldd-radio-button
              decorative
              :checked="w === chosen.base || undefined"
            ></nldd-radio-button>
          </nldd-cell>
          <nldd-spacer-cell size="12"></nldd-spacer-cell>
          <nldd-title-cell
            :size="6"
            :text="accessBaseLabel(w)"
            :supporting-text="accessBaseHint(w)"
          ></nldd-title-cell>
        </nldd-list-item>
      </nldd-list>

      <nldd-banner
        v-if="nobodyCanView(chosen)"
        variant="warning"
        :text="t('access.nobodyWarning')"
        :supporting-text="t('access.nobodyWarning.detail')"
        data-testid="default-access-nobody-warning"
      ></nldd-banner>

      <nldd-container layout="stack" gap="8">
        <SectionHeading
          id="heading-default-access-exceptions"
          :size="5"
          :level="3"
          :text="t('publish.access.extras.heading')"
          :intro="t('publish.access.extras.intro')"
        />
        <nldd-switch-field
          :label="keysLabel()"
          :checked="chosen.keys || undefined"
          data-testid="default-access-keys"
          @change="toggle('keys', $event)"
        ></nldd-switch-field>
        <nldd-switch-field
          :label="inviteesLabel()"
          :checked="chosen.invitees || undefined"
          data-testid="default-access-invitees"
          @change="toggle('invitees', $event)"
        ></nldd-switch-field>
      </nldd-container>
    </nldd-container>
  </section>

  <template v-if="canDelete">
    <nldd-spacer size="24"></nldd-spacer>
    <section aria-labelledby="heading-danger-zone-group">
      <nldd-box background="critical">
        <nldd-container layout="stack" gap="8" padding="16">
          <SectionHeading id="heading-danger-zone-group" :text="t('group.settings.danger.heading')" />
          <nldd-container layout="stack" gap="16">
            <nldd-rich-text>
              <p>{{ t('group.settings.danger.body') }}</p>
            </nldd-rich-text>
            <nldd-button-group orientation="horizontal">
              <nldd-button
                variant="destructive"
                :text="t('group.settings.danger.action')"
                data-testid="delete-group"
                @click="deleteOpen = true"
              ></nldd-button>
            </nldd-button-group>
          </nldd-container>
        </nldd-container>
      </nldd-box>
      <ConfirmModal
        :open="deleteOpen"
        :title="t('group.settings.danger.confirm.title', { group: props.group })"
        :text="
          sites.length > 0
            ? t('group.settings.danger.confirm.text.withSites')
            : t('group.settings.danger.confirm.text')
        "
        :keep-label="t('group.settings.danger.confirm.keep')"
        :confirm-label="t('group.settings.danger.confirm.confirm')"
        :confirm-phrase="props.group"
        :busy="deleteBusy"
        @confirm="deleteGroup"
        @close="deleteOpen = false"
      >
        <!-- type="form": facts, not actions, as in GroupMembersManager. -->
        <nldd-list
          v-if="sites.length > 0"
          type="form"
          variant="box-tinted"
          :accessible-label="t('group.settings.danger.confirm.sites.list')"
          data-testid="group-sites-list"
        >
          <nldd-list-item v-for="site in namedSites" :key="site.slug">
            <nldd-text-cell
              :text="site.title"
              :supporting-text="`${props.group}/${site.slug}`"
            ></nldd-text-cell>
          </nldd-list-item>
          <nldd-list-item v-if="unnamedSites > 0" data-testid="group-sites-rest">
            <nldd-text-cell size="sm" :text="unnamedText"></nldd-text-cell>
          </nldd-list-item>
        </nldd-list>
      </ConfirmModal>
    </section>
  </template>
</template>
