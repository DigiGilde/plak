<script setup lang="ts">
import { computed, nextTick, ref, useId, watch } from 'vue';

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
    /**
     * Text the member has to type before the confirmation goes through, for
     * an action that throws away more than one thing. A click on autopilot
     * gets past any dialog; typing what you are about to delete does not.
     */
    confirmPhrase?: string;
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

const typed = ref('');
const phraseInvalid = ref(false);
const phraseUnmetId = `bevestig-zin-${useId()}`;

function phraseMatches(): boolean {
  return props.confirmPhrase === undefined || typed.value.trim() === props.confirmPhrase;
}

function onPhraseInput(event: Event): void {
  typed.value = (event as CustomEvent<{ value?: string }>).detail?.value ?? (event.target as HTMLInputElement).value;
  // Judged only on confirm, but the red goes as soon as the value is right.
  if (phraseInvalid.value && phraseMatches()) phraseInvalid.value = false;
}

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
    typed.value = '';
    phraseInvalid.value = false;
    await nextTick();
    dialog.value?.show?.();
  },
  { immediate: true },
);

function confirm(): void {
  // Only confirm while the dialog is genuinely open: that makes the
  // confirmation step enforceable in tests too (which have no real modal).
  if (!props.open || props.busy) return;
  if (!phraseMatches()) {
    phraseInvalid.value = true;
    return;
  }
  emit('confirm');
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
      <!-- One stack for the slot and the field: the dialog puts no gap of its
           own between slotted children. Only here, because any slotted
           element, even an empty one, turns a centered message left-aligned. -->
      <nldd-container
        v-if="confirmPhrase !== undefined"
        layout="stack"
        gap="16"
        data-testid="bevestig-inhoud"
      >
        <slot></slot>
        <nldd-form-field :label="t('admin.confirm.phrase.label', { phrase: confirmPhrase })">
          <nldd-text-field
            :value="typed"
            autocomplete="off"
            no-spellcheck
            :invalid="phraseInvalid || undefined"
            :unmet="phraseInvalid ? phraseUnmetId : undefined"
            data-testid="bevestig-zin"
            @input="onPhraseInput"
            @keydown.enter.prevent="confirm"
          ></nldd-text-field>
          <nldd-validation-list>
            <nldd-validation-item :id="phraseUnmetId">
              {{ t('admin.confirm.phrase.mismatch', { phrase: confirmPhrase }) }}
            </nldd-validation-item>
          </nldd-validation-list>
        </nldd-form-field>
      </nldd-container>
      <slot v-else></slot>
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
