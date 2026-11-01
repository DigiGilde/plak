<script setup lang="ts">
/**
 * What it looks like to hand Plak a site, the same for a new site and for a new
 * version of an existing one. Only the look: reading and packing are in
 * `uploadIntake`, and what counts as a drop target is up to whoever uses this.
 *
 * The field itself stays next to the drop zone: a file dialog only opens from a
 * real gesture on a real input, and WCAG 2.2 (2.5.7 Dragging Movements) requires
 * a path without a dragging movement.
 *
 * It renders no form of its own, because the sheet has to keep this inside its
 * one form. The `actions` slot lands in the button group beside the folder
 * button, and the `chosen` slot right under the field.
 */
import { ref } from 'vue';

import UploadFeedback from './UploadFeedback.vue';
import type { IntakeStep } from './uploadIntake';
import { t } from '@/i18n';

defineProps<{
  required: boolean;
  invalid?: boolean;
  /** The name of the chosen file, empty while there is none. */
  fileName: string;
  /** Something is being dragged over the drop target. */
  active?: boolean;
  step: IntakeStep;
  progress: { done: number; total: number } | null;
  ready: string;
  error: string | null;
}>();

const emit = defineEmits<{
  choice: [event: Event];
  folder: [event: Event];
}>();

const field = ref<HTMLElement>();
const folderInput = ref<HTMLInputElement>();

/** The field's native input, which lives in its shadow root. Reached
 * defensively: it is a private detail of the component, so everything that
 * depends on it falls back silently. */
function fileInput(): HTMLInputElement | null {
  return (
    (field.value?.shadowRoot?.querySelector('input[type=file]') as HTMLInputElement | null) ??
    (field.value?.querySelector('input[type=file]') as HTMLInputElement | null)
  );
}

/**
 * Hands a file to the field's own input, so the field shows the name and its
 * required rule is satisfied. A file input rejects a plain assignment but
 * accepts a FileList built with DataTransfer. Says whether that worked.
 *
 * The change event is composed on purpose: an uncomposed one would stay inside
 * the shadow root, where nldd-file-field listens for it. The field answers it
 * synchronously with a change event of its own, which arrives as a `choice`.
 */
function handOver(file: File): boolean {
  const input = fileInput();
  if (!input || typeof DataTransfer === 'undefined') return false;
  try {
    const data = new DataTransfer();
    data.items.add(file);
    input.files = data.files;
    if (input.files?.length !== 1) return false;
    input.dispatchEvent(new Event('change', { bubbles: true, composed: true }));
  } catch {
    return false;
  }
  return true;
}

/**
 * Empties the field. nldd-file-field owns a FileList of its own and offers no
 * public clear, so the input it wraps is emptied instead and told about it; the
 * component follows that the way it follows a real choice, with a `choice`.
 */
function clearField(): void {
  const input = fileInput();
  if (!input?.files?.length) return;
  input.value = '';
  input.dispatchEvent(new Event('change', { bubbles: true, composed: true }));
  input.dispatchEvent(new Event('input', { bubbles: true, composed: true }));
}

defineExpose({ field, handOver, clearField });
</script>

<template>
  <div class="dropzone" :class="{ 'dropzone--active': active }" data-testid="dropzone">
    <nldd-form-field
      :label="t('publish.upload.field.label')"
      :supporting-label="t('publish.upload.field.hint')"
    >
      <nldd-file-field
        ref="field"
        name="file"
        accept=".zip,.tar.gz,.tgz,.html"
        data-takes-folders
        :required="required || undefined"
        :invalid="invalid || undefined"
        data-testid="upload-input"
        @change="emit('choice', $event)"
      ></nldd-file-field>
      <slot name="chosen"></slot>
      <!-- nldd-validation-list reads `control.value` and nldd-file-field has
           none: without this value the required rule fails even with a file
           present, and setCustomValidity keeps the field unsubmittable. -->
      <nldd-validation-list :value="fileName">
        <nldd-validation-item id="upload-file-required" required>
          {{ t('publish.upload.field.required') }}
        </nldd-validation-item>
      </nldd-validation-list>
      <nldd-form-field-help-text>
        {{ t('publish.upload.field.help') }}
      </nldd-form-field-help-text>
    </nldd-form-field>

    <UploadFeedback :step="step" :progress="progress" :ready="ready" :error="error" />

    <nldd-form-actions>
      <nldd-button-group>
        <nldd-button
          appearance="secondary"
          type="button"
          :text="t('publish.upload.folder')"
          data-testid="upload-folder"
          @click="folderInput?.click()"
        ></nldd-button>
        <slot name="actions"></slot>
      </nldd-button-group>
    </nldd-form-actions>

    <!-- nldd-file-field wraps its own input and offers no directory mode; this
         one is opened by the button above and never seen. -->
    <input
      ref="folderInput"
      type="file"
      webkitdirectory
      hidden
      data-testid="upload-folder-input"
      @change="emit('folder', $event)"
    />
  </div>
</template>

<style scoped>
/*
 * Hand-written CSS because the design system has no drop zone. The dashed
 * border says "something can go in here" without imitating a control.
 */
.dropzone {
  border: var(--primitives-border-width-regular) dashed var(--semantics-dividers-color);
  border-radius: var(--primitives-corner-radius-lg);
  padding: var(--primitives-space-16);
}

.dropzone--active {
  border-color: var(--semantics-content-accent-color);
  background: var(--semantics-categories-accent-tinted-background-color);
}

/*
 * The rhythm of nldd-form (dist/css/form.css) only reaches direct children of
 * its form, and these sit one level deeper. A margin rather than a gap, so that
 * it collapses with the one nldd-form-actions brings along.
 */
.dropzone > :deep(*:has(~ *:not([hidden]):not([type='hidden']))) {
  margin-bottom: var(--semantics-forms-fields-gap);
}
</style>
