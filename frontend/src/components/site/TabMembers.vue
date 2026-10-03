<script setup lang="ts">
/**
 * Leden tab of the site page: who can reach this site and with which
 * role. The list and the form live in SiteMembersManager, which stays testable
 * on its own; this tab binds the API calls to this one site and keeps the list.
 *
 * The group name comes from the group itself, because the collapsed block
 * names the group people reach this site through.
 */
import { onMounted, ref, watch } from 'vue';

import * as plak from '@/api/plak';
import type { MemberSuggestion, Role, SiteMember } from '@/api/types';
import { t } from '@/i18n';
import ErrorBanner from '@/components/ErrorBanner.vue';
import SiteMembersManager from '@/components/SiteMembersManager.vue';

// See TabOverview.vue for why inheritAttrs is off on every tab.
defineOptions({ inheritAttrs: false });

const props = defineProps<{ group: string; site: string }>();

const loading = ref(true);
const error = ref<unknown>(null);
const members = ref<SiteMember[]>([]);
const groupName = ref('');

async function load(): Promise<void> {
  loading.value = true;
  error.value = null;
  try {
    const [rows, detail] = await Promise.all([
      plak.siteMembers(props.group, props.site),
      plak.group(props.group),
    ]);
    members.value = rows;
    groupName.value = detail.group.name;
  } catch (f) {
    error.value = f;
  } finally {
    loading.value = false;
  }
}

onMounted(load);
watch(() => [props.group, props.site], load);

function add(identifier: string, role: Role): Promise<SiteMember> {
  return plak.addSiteMember(props.group, props.site, identifier, role);
}

function remove(memberId: string): Promise<void> {
  return plak.removeSiteMember(props.group, props.site, memberId);
}

function setRole(memberId: string, role: Role): Promise<SiteMember> {
  return plak.setSiteRole(props.group, props.site, memberId, role);
}

function search(query: string): Promise<MemberSuggestion[]> {
  return plak.searchSiteMembers(props.group, props.site, query);
}

/** The server answers with the whole row, so a change lands as a replacement. */
function upsert(member: SiteMember): void {
  const index = members.value.findIndex((row) => row.identifier === member.identifier);
  if (index === -1) {
    members.value = [...members.value, member];
  } else {
    members.value.splice(index, 1, member);
  }
}

function onRemoved(identifier: string): void {
  members.value = members.value.flatMap((row) => {
    if (row.identifier !== identifier) return [row];
    // Losing the site role is not losing the site: a group member keeps
    // reaching it through the group, and moves to the block above.
    return row.groupRole === null ? [] : [{ ...row, siteRole: null, effectiveRole: row.groupRole }];
  });
}
</script>

<template>
  <ErrorBanner v-if="error" :error="error" />

  <!-- The tab's structure is there straight away; the indicator only covers it
       once loading takes longer than a second. -->
  <nldd-activity-indicator
    v-else
    :text="t('site.members.loading')"
    :complete="!loading || undefined"
  >
    <section aria-labelledby="heading-site-members">
      <nldd-container layout="stack" gap="8">
        <nldd-title :size="4">
          <h2 id="heading-site-members">{{ t('site.members.heading') }}</h2>
          <span slot="subtitle">{{ t('site.members.intro') }}</span>
        </nldd-title>

        <SiteMembersManager
          :members="members"
          :group="group"
          :group-name="groupName"
          :add="add"
          :remove="remove"
          :set-role="setRole"
          :search="search"
          @added="upsert"
          @role-changed="upsert"
          @removed="onRemoved"
        />
      </nldd-container>
    </section>
  </nldd-activity-indicator>
</template>
