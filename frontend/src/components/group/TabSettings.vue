<script setup lang="ts">
/**
 * Instellingen tab of the group page: the access new sites in this group start
 * with, base and extras together.
 *
 * The choice sits inline on the tab rather than in a sheet: the tab is the
 * place for this setting, and a sheet over it would show the same options a
 * second time. The rows are optimistic (the choice is there straight away, the
 * API confirms behind it, on failure it rolls back with a notification), just
 * like the Toegang tab of a site.
 */
import { ref, watch } from 'vue';

import { ApiError } from '@/api/client';
import * as api from '@/api/plak';
import type { Access, AccessBase, Group } from '@/api/types';
import { ACCESS_BASE_VALUES } from '@/api/types';
import Notices from '@/components/site/Notices.vue';
import { accessBaseHint, accessBaseLabel, accessSummary, inviteesLabel, keysLabel } from '@/format';
import { t } from '@/i18n';

// The tab has several roots (notifications beside the content) and receives
// the props of every tab; none of them should fall through to the markup.
defineOptions({ inheritAttrs: false });

const props = defineProps<{ group: string; access: Access }>();

const emit = defineEmits<{
  groupChanged: [Group];
}>();

const notices = ref<InstanceType<typeof Notices> | null>(null);
const chosen = ref<Access>(props.access);

watch(
  () => props.access,
  (value) => {
    chosen.value = value;
  },
);

function errorText(f: unknown, fallback: string): string {
  return f instanceof ApiError ? (f.problem.detail ?? f.problem.title) : fallback;
}

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

  <section aria-labelledby="kop-standaardtoegang">
    <nldd-container layout="stack" gap="8">
      <nldd-title :size="4">
        <h2 id="kop-standaardtoegang">{{ t('group.settings.heading') }}</h2>
        <span slot="subtitle">{{ t('group.settings.intro') }}</span>
      </nldd-title>

      <nldd-list
        type="radiogroup"
        variant="box-base"
        :accessible-label="t('group.settings.list.label')"
        data-testid="standaardtoegang-groep"
      >
        <nldd-list-item
          v-for="w in ACCESS_BASE_VALUES"
          :key="w"
          radio
          size="md"
          :checked="w === chosen.base || undefined"
          :data-testid="`standaardtoegang-${w}`"
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
        data-testid="standaardtoegang-sleutels"
        @change="toggle('keys', $event)"
      ></nldd-switch-field>
      <nldd-switch-field
        :label="inviteesLabel()"
        :checked="chosen.invitees || undefined"
        data-testid="standaardtoegang-genodigden"
        @change="toggle('invitees', $event)"
      ></nldd-switch-field>

      <nldd-inline-dialog
        icon="eye"
        :text="accessSummary(chosen)"
        data-testid="standaardtoegang-samenvatting"
      ></nldd-inline-dialog>
    </nldd-container>
  </section>
</template>
