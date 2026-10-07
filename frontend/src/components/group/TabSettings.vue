<script setup lang="ts">
/**
 * Instellingen tab of the group page: the access new sites in this group start
 * with, base and extras together, and for a group admin the danger zone.
 *
 * The choice sits inline on the tab rather than in a sheet: the tab is the
 * place for this setting, and a sheet over it would show the same options a
 * second time. The rows are optimistic (the choice is there straight away, the
 * API confirms behind it, on failure it rolls back with a notification), just
 * like the Toegang tab of a site.
 */
import { computed, ref, watch } from 'vue';

import { errorText } from '@/api/client';
import * as api from '@/api/plak';
import type { Access, AccessBase, Group, Site } from '@/api/types';
import { ACCESS_BASE_VALUES } from '@/api/types';
import ConfirmModal from '@/components/ConfirmModal.vue';
import Notices from '@/components/site/Notices.vue';
import { accessBaseHint, accessBaseLabel, accessSummary, inviteesLabel, keysLabel } from '@/format';
import { t } from '@/i18n';

// The tab has several roots (notifications beside the content) and receives
// the props of every tab; none of them should fall through to the markup.
defineOptions({ inheritAttrs: false });

const props = defineProps<{ group: string; access: Access; sites: Site[]; canDelete: boolean }>();

const emit = defineEmits<{
  groupChanged: [Group];
  removed: [];
}>();

const notices = ref<InstanceType<typeof Notices> | null>(null);
const chosen = ref<Access>(props.access);

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
    // notification there would be unreachable (see site/TabOverview).
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

  <section aria-labelledby="heading-default-access">
    <nldd-container layout="stack" gap="8">
      <nldd-title :size="4">
        <h2 id="heading-default-access">{{ t('group.settings.heading') }}</h2>
        <span slot="subtitle">{{ t('group.settings.intro') }}</span>
      </nldd-title>

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

      <nldd-inline-dialog
        icon="eye"
        :text="accessSummary(chosen)"
        data-testid="default-access-summary"
      ></nldd-inline-dialog>
    </nldd-container>
  </section>

  <template v-if="canDelete">
    <nldd-spacer size="24"></nldd-spacer>
    <section aria-labelledby="heading-danger-zone-group">
      <nldd-box background="critical">
        <nldd-container layout="stack" gap="8" padding="16">
          <nldd-title :size="4">
            <h2 id="heading-danger-zone-group">{{ t('group.settings.danger.heading') }}</h2>
          </nldd-title>
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
