<script setup lang="ts">
import SectionHeading from '@/components/SectionHeading.vue';

/**
 * The header of a page: the h1 with the page's actions beside it.
 *
 * The actions are not in the `end` slot of nldd-title: that slot neither
 * shrinks along nor lets the button drop, which breaks the title letter by
 * letter at 320 px and pushes the button outside the viewport (measured: h1
 * 0 px wide, scrollWidth 352 at clientWidth 320). A wrap container lets the
 * actions drop a line as soon as they no longer fit beside the title; the
 * `.page-header` rule in global.css lets the title shrink to that point.
 */
withDefaults(defineProps<{ text: string; size?: 1 | 2 | 3 }>(), { size: 1 });
</script>

<template>
  <nldd-container layout="wrap" gap="16" vertical-alignment="center" class="page-header">
    <SectionHeading :level="1" :size="size" :text="text" />
    <slot />
  </nldd-container>
</template>
