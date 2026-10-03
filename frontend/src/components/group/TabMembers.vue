<script setup lang="ts">
/**
 * Leden tab of the group page. Adding, removing and changing a role happen in the list
 * itself (GroupMembersManager): one tab is already the place for this task, so a
 * side panel over it would show the same content twice.
 *
 * The API calls are bound to the group here and handed on as callbacks, so
 * GroupMembersManager stays testable on its own; the group page keeps the member
 * list and listens to the emits.
 */
import * as api from '@/api/plak';
import type { GroupMember, MemberSuggestion, Role } from '@/api/types';
import GroupMembersManager from '@/components/GroupMembersManager.vue';
import { t } from '@/i18n';

// The tabs receive the props of every tab; none of them should fall through
// to the markup.
defineOptions({ inheritAttrs: false });

const props = defineProps<{ group: string; members: GroupMember[] }>();

const emit = defineEmits<{
  memberAdded: [GroupMember];
  memberRemoved: [string];
  memberRoleChanged: [GroupMember];
}>();

function add(identifier: string, role: Role): Promise<GroupMember> {
  return api.addGroupMember(props.group, identifier, role);
}

function remove(memberId: string, siteRoles: 'keep' | 'remove'): Promise<void> {
  return api.removeGroupMember(props.group, memberId, siteRoles);
}

function setRole(memberId: string, role: Role): Promise<GroupMember> {
  return api.setGroupRole(props.group, memberId, role);
}

function search(query: string): Promise<MemberSuggestion[]> {
  return api.searchGroupMembers(props.group, query);
}
</script>

<template>
  <section aria-labelledby="heading-members">
    <nldd-container layout="stack" gap="8">
      <nldd-title :size="4">
        <h2 id="heading-members">{{ t('group.members.heading') }}</h2>
        <span slot="subtitle">{{ t('group.members.intro') }}</span>
      </nldd-title>

      <GroupMembersManager
        :members="members"
        :add="add"
        :remove="remove"
        :set-role="setRole"
        :search="search"
        @added="emit('memberAdded', $event)"
        @removed="emit('memberRemoved', $event)"
        @role-changed="emit('memberRoleChanged', $event)"
      />
    </nldd-container>
  </section>
</template>
