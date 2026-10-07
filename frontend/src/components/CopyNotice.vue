<script setup lang="ts">
/**
 * What happened to a copy, in words, beside the buttons that copied. A status
 * line and not a toast: it belongs to those buttons and has to be announced
 * when it appears after the copy too. Empty until something is copied.
 */
defineProps<{ text: string }>();
</script>

<template>
  <nldd-text size="sm" role="status">{{ text }}</nldd-text>
</template>
