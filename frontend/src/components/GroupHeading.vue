<script setup lang="ts">
/**
 * Group heading above one group's site list: the group name links
 * to the group page.
 *
 * The heading carries no action itself: the create action for this one group
 * sits below its table, where the result appears too, and carries text there
 * instead of a plus sign floating to the right of the table.
 */
import type { Group } from '../api/types';

defineProps<{
  group: Group;
}>();
</script>

<template>
  <nldd-title :size="5">
    <h2>
      <nldd-link :href="`/${group.slug}`">{{ group.name }}</nldd-link>
    </h2>
  </nldd-title>
</template>
