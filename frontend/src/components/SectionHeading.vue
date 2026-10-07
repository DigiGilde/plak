<script setup lang="ts">
/**
 * A heading with an optional line of supporting text below it, in one place:
 * the slot the design system gives that line is named here and nowhere else.
 * A supporting text that is more than a string (a `<code>` in it) goes in the
 * `intro` slot.
 */
withDefaults(
  defineProps<{
    text: string;
    id?: string;
    /** The size it looks; the level below is what it is. */
    size?: 1 | 2 | 3 | 4 | 5 | 6;
    level?: 1 | 2;
    intro?: string;
  }>(),
  { id: undefined, size: 4, level: 2, intro: undefined },
);
</script>

<template>
  <nldd-title :size="size">
    <component :is="`h${level}`" :id="id">{{ text }}</component>
    <span v-if="intro" slot="supporting-text">{{ intro }}</span>
    <span v-else-if="$slots.intro" slot="supporting-text"><slot name="intro" /></span>
  </nldd-title>
</template>
