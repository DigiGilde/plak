<script setup lang="ts">
/**
 * A box that opens and closes, on a native details/summary rather than a
 * toggle of our own: it is keyboard operable by itself, announces its open
 * state, and lets the browser find text inside it with ctrl+F while it is
 * closed. NLDD has no accordion component, so the styling hangs on its tokens.
 *
 * `data-testid` lands on the details element; `summaryTestid` on the summary.
 */
defineProps<{ summary: string; summaryTestid?: string }>();
</script>

<template>
  <details class="disclosure">
    <summary :data-testid="summaryTestid">{{ summary }}</summary>
    <div class="disclosure-content"><slot /></div>
  </details>
</template>

<style scoped>
.disclosure {
  max-width: var(--plak-table-max-width);
  border: var(--primitives-border-width-thin) solid var(--semantics-surfaces-base-border-color);
  border-radius: var(--primitives-corner-radius-lg);
  background: var(--semantics-surfaces-tinted-background-color);
}

/* No display: flex or block here: both drop the native disclosure triangle in
   Chrome and Safari, and there is no component icon to put in its place. */
.disclosure > summary {
  padding: var(--primitives-space-12) var(--primitives-space-16);
  cursor: pointer;
  color: var(--semantics-content-color);
  font: var(--primitives-font-body-md-semi-bold-snug);
}

.disclosure > summary:focus-visible {
  outline: var(--semantics-focus-ring-outline);
  outline-offset: var(--semantics-focus-ring-outline-offset);
  box-shadow: var(--semantics-focus-ring-box-shadow);
  border-radius: var(--primitives-corner-radius-lg);
}

.disclosure-content {
  padding: 0 var(--primitives-space-16) var(--primitives-space-16);
}
</style>
