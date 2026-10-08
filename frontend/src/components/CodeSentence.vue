<script setup lang="ts">
/**
 * A sentence of the catalogue with code in it. The words stay one catalogue
 * entry with a placeholder where the code goes: `{address}` turns into a
 * `<code>` holding the string under that name in `codes`, wherever the
 * language puts it. A name that also has an entry in `links` becomes a link
 * to that address instead, opened in a new tab.
 */
import { computed } from 'vue';

const props = defineProps<{
  text: string;
  codes: Record<string, string>;
  links?: Record<string, string>;
}>();

// Split at the placeholders; the names they hold end up at the odd places.
const pieces = computed(() => props.text.split(/\{(\w+)\}/));
</script>

<template>
  <template v-for="(piece, index) in pieces" :key="index">
    <nldd-link v-if="index % 2 === 1 && links?.[piece]" :href="links[piece]" target="_blank" translate="no">{{
      codes[piece]
    }}</nldd-link>
    <code v-else-if="index % 2 === 1" translate="no">{{ codes[piece] }}</code>
    <template v-else>{{ piece }}</template>
  </template>
</template>
