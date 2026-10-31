<script setup lang="ts">
/**
 * The upload field with a drop zone around it. The field itself stays: a file
 * dialog only opens from a real gesture on a real input, and WCAG 2.2 (2.5.7
 * Dragging Movements) requires a path without a dragging movement.
 */
import { onBeforeUnmount, onMounted, ref } from 'vue';

import {
  canPack,
  checkBundle,
  formatCount,
  formatSize,
  pack,
  readChosenFolder,
  readDrop,
  type DropResult,
} from './packing';
import { t } from '@/i18n';

defineProps<{ busy?: boolean }>();

const emit = defineEmits<{
  file: [file: File];
}>();

const field = ref<HTMLElement>();
const folderInput = ref<HTMLInputElement>();
const chosen = ref<File | null>(null);
/** What is ready, in the user's own terms; empty until something is there. */
const ready = ref('');
const dropError = ref<string | null>(null);
const step = ref<'' | 'reading' | 'packing'>('');
const progress = ref<{ done: number; total: number } | null>(null);

const dropActive = ref(false);
// dragenter and dragleave fire on every child too, so count instead of toggle.
let dropDepth = 0;

function take(file: File, description: string): void {
  chosen.value = file;
  ready.value = description;
  // nldd-form only clears its `invalid` marking on an input event from the
  // control; nldd-file-field fires that before the choice is processed and
  // after that only `change`, so without this the field stays red and
  // aria-invalid after a failed submit even though a file is there.
  field.value?.removeAttribute('invalid');
}

function onChoice(event: Event): void {
  const detail = (event as CustomEvent<{ files?: File[] }>).detail;
  const files = detail?.files ?? (event.target as HTMLInputElement | null)?.files;
  const file = files?.[0] ?? null;
  dropError.value = null;
  if (!file) {
    chosen.value = null;
    ready.value = '';
    return;
  }
  take(file, t('publish.upload.ready.file', { name: file.name, size: formatSize(file.size) }));
}

function onDragenter(): void {
  dropDepth += 1;
  dropActive.value = true;
}

function onDragover(event: DragEvent): void {
  if (event.dataTransfer) {
    event.dataTransfer.dropEffect = 'copy';
  }
}

function onDragleave(): void {
  dropDepth = Math.max(0, dropDepth - 1);
  dropActive.value = dropDepth > 0;
}

async function onDrop(event: DragEvent): Promise<void> {
  dropDepth = 0;
  dropActive.value = false;
  const transfer = event.dataTransfer;
  if (!transfer) return;
  await receive(() => readDrop(transfer));
}

/** A folder through the file dialog: the path to a whole folder without dragging (WCAG 2.5.7). */
async function onFolderChoice(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement;
  const chosenFiles = Array.from(input.files!);
  // So that choosing the same folder again is a change again.
  input.value = '';
  await receive(() => readChosenFolder(chosenFiles));
}

/** What a drop or a folder choice has in common: check it, pack it, make it the chosen file. */
async function receive(read: () => DropResult | Promise<DropResult>): Promise<void> {
  dropError.value = null;
  step.value = 'reading';
  try {
    const dropped = await read();
    if (dropped.kind === 'refusal') {
      dropError.value = dropped.reason;
      return;
    }
    if (dropped.kind === 'file') {
      take(
        dropped.file,
        t('publish.upload.ready.file', {
          name: dropped.file.name,
          size: formatSize(dropped.file.size),
        }),
      );
      return;
    }

    const objection = checkBundle(dropped.name, dropped.files);
    if (objection) {
      dropError.value = objection;
      return;
    }
    if (!canPack()) {
      dropError.value = t('publish.upload.error.cannotPack');
      return;
    }

    step.value = 'packing';
    progress.value = { done: 0, total: dropped.files.length };
    const archive = await pack(dropped.name, dropped.files, (done, total) => {
      progress.value = { done, total };
    });
    const packed = dropped.files.length;
    take(
      archive,
      t(packed === 1 ? 'publish.upload.ready.bundleOne' : 'publish.upload.ready.bundle', {
        name: dropped.name,
        count: formatCount(packed),
        size: formatSize(archive.size),
      }),
    );
  } catch {
    dropError.value = t('publish.upload.error.packFailed');
  } finally {
    step.value = '';
    progress.value = null;
  }
}

/**
 * A drop that misses the zone falls back on browser behaviour: the file opens
 * in the tab and the admin page is gone. That only happens as long as nobody
 * stops the default, so the document stops it everywhere.
 */
function swallowDrop(event: DragEvent): void {
  event.preventDefault();
}

onMounted(() => {
  document.addEventListener('dragover', swallowDrop);
  document.addEventListener('drop', swallowDrop);
});

onBeforeUnmount(() => {
  document.removeEventListener('dragover', swallowDrop);
  document.removeEventListener('drop', swallowDrop);
});

function publish(): void {
  if (!chosen.value) {
    return;
  }
  emit('file', chosen.value);
}
</script>

<template>
  <div
    class="dropzone"
    :class="{ 'dropzone--active': dropActive }"
    data-testid="dropzone"
    @dragenter.prevent="onDragenter"
    @dragover.prevent="onDragover"
    @dragleave="onDragleave"
    @drop.prevent.stop="onDrop"
  >
    <nldd-form data-testid="upload-form" @submit.prevent="publish">
      <nldd-form-field
        :label="t('publish.upload.field.label')"
        :supporting-label="t('publish.upload.field.hint')"
      >
        <nldd-file-field
          ref="field"
          name="file"
          accept=".zip,.tar.gz,.tgz,.html"
          :required="chosen === null || undefined"
          data-testid="upload-input"
          @change="onChoice"
        ></nldd-file-field>
        <!-- nldd-validation-list reads `control.value` and nldd-file-field has
             none: without this value the required rule fails even with a file
             present, and setCustomValidity keeps the field unsubmittable. -->
        <nldd-validation-list :value="chosen?.name ?? ''">
          <nldd-validation-item id="upload-file-required" required>
            {{ t('publish.upload.field.required') }}
          </nldd-validation-item>
        </nldd-validation-list>
        <nldd-form-field-help-text>
          {{ t('publish.upload.field.help') }}
        </nldd-form-field-help-text>
      </nldd-form-field>

      <nldd-progress-bar
        v-if="step !== ''"
        :text="
          step === 'reading'
            ? t('publish.upload.progress.reading')
            : t('publish.upload.progress.packing')
        "
        :value="progress?.done ?? 0"
        :max="progress?.total || 1"
        :indeterminate="step === 'reading' || undefined"
        data-testid="drag-progress"
      ></nldd-progress-bar>

      <!-- nldd-banner announces itself (role="status"), so the outcome of a
           drop is reported without a separate live region. -->
      <nldd-banner
        v-if="ready && step === ''"
        variant="success"
        size="sm"
        :text="t('publish.upload.ready.title')"
        :supporting-text="ready"
        data-testid="drag-done"
      ></nldd-banner>

      <nldd-banner
        v-if="dropError"
        variant="warning"
        size="sm"
        :text="t('publish.upload.error.title')"
        :supporting-text="dropError"
        data-testid="drag-error"
      ></nldd-banner>

      <nldd-form-actions>
        <nldd-button
          variant="secondary"
          type="button"
          :text="t('publish.upload.folder')"
          data-testid="upload-folder"
          @click="folderInput?.click()"
        ></nldd-button>
        <nldd-button
          variant="primary"
          type="submit"
          :text="t('publish.upload.submit')"
          :loading="busy || undefined"
          data-testid="upload-publish"
        ></nldd-button>
      </nldd-form-actions>
    </nldd-form>
    <!-- nldd-file-field wraps its own input and offers no directory mode; this
         one is opened by the button above and never seen. -->
    <input
      ref="folderInput"
      type="file"
      webkitdirectory
      hidden
      data-testid="upload-folder-input"
      @change="onFolderChoice"
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
</style>
