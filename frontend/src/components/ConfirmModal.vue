<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue';

import { t } from '@/i18n';

interface DialogElement extends HTMLElement {
  show?: () => void;
  hide?: () => void;
}

const props = withDefaults(
  defineProps<{
    open: boolean;
    title: string;
    text: string;
    confirmLabel: string;
    /** The safe way out: sits at the top and gets the primary variant. */
    keepLabel?: string;
    busy?: boolean;
  }>(),
  { busy: false },
);

// Not a withDefaults default: that is evaluated once, which would freeze the
// label in the language of the first render.
const keepText = computed(() => props.keepLabel ?? t('admin.confirm.keep'));

const emit = defineEmits<{
  confirm: [];
  close: [];
}>();

const dialog = ref<DialogElement | null>(null);

// Mirror the imperative API rather than mounting/unmounting, so the animation
// plays (nldd skill); the optional call keeps jsdom (no shadow DOM, no
// show/hide) working in tests.
watch(
  () => props.open,
  async (open) => {
    if (!open) {
      dialog.value?.hide?.();
      return;
    }
    await nextTick();
    dialog.value?.show?.();
  },
  { immediate: true },
);

function confirm(): void {
  // Only confirm while the dialog is genuinely open: that makes the
  // confirmation step enforceable in tests too (which have no real modal).
  if (props.open && !props.busy) {
    emit('confirm');
  }
}
</script>

<template>
  <!-- Overlays belong on the document root (NLDD), see PublishSheet. -->
  <Teleport to="body">
    <nldd-modal-dialog
      ref="dialog"
      variant="alert"
      :text="title"
      :supporting-text="text"
      :accessible-label="title"
      @close="emit('close')"
    >
      <!-- Between the sentence and the buttons: whatever a confirmation needs
           beyond a sentence, such as the choice to take more along. -->
      <slot></slot>
      <!-- Behoud sits at the top and is primary: the autopilot click should
           be the safe way out, not the irreversible action. -->
      <nldd-button
        slot="actions"
        variant="primary"
        :text="keepText"
        data-testid="bevestig-annuleren"
        @click="emit('close')"
      ></nldd-button>
      <nldd-button
        slot="actions"
        variant="destructive"
        :text="confirmLabel"
        :loading="busy || undefined"
        data-testid="bevestig-doorgaan"
        @click="confirm"
      ></nldd-button>
    </nldd-modal-dialog>
  </Teleport>
</template>
