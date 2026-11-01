<script setup lang="ts">
/**
 * A new version of an existing site: the upload block as a form of its own, with
 * its submit beside the folder button and the block itself as the drop target.
 */
import { onBeforeUnmount, onMounted, ref } from 'vue';

import { readDrop } from './packing';
import UploadBlock from './UploadBlock.vue';
import { useUploadIntake } from './uploadIntake';
import { t } from '@/i18n';

defineProps<{ busy?: boolean }>();

const emit = defineEmits<{
  file: [file: File];
}>();

const block = ref<InstanceType<typeof UploadBlock>>();
const chosen = ref<File | null>(null);

const dropActive = ref(false);
// dragenter and dragleave fire on every child too, so count instead of toggle.
let dropDepth = 0;

function take(file: File): void {
  chosen.value = file;
  // nldd-form only clears its `invalid` marking on an input event from the
  // control; nldd-file-field fires that before the choice is processed and
  // after that only `change`, so without this the field stays red and
  // aria-invalid after a failed submit even though a file is there.
  block.value?.field?.removeAttribute('invalid');
}

const { step, progress, ready, error, receive, onFolderChoice, noteChoice } = useUploadIntake(
  (file) => {
    block.value?.handOver(file);
    take(file);
  },
  () => {
    chosen.value = null;
    block.value?.clearField();
  },
);

function onChoice(event: Event): void {
  const detail = (event as CustomEvent<{ files?: File[] }>).detail;
  const files = detail?.files ?? (event.target as HTMLInputElement | null)?.files;
  const file = files?.[0] ?? null;
  noteChoice(file);
  if (file) {
    take(file);
  } else {
    chosen.value = null;
  }
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
  // Dragged text is not an upload, so it must not cost the file that is chosen.
  if (!transfer?.types.includes('Files')) return;
  await receive(() => readDrop(transfer));
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
  if (!chosen.value || step.value !== '') {
    return;
  }
  emit('file', chosen.value);
}
</script>

<template>
  <nldd-form data-testid="upload-form" @submit.prevent="publish">
    <UploadBlock
      ref="block"
      :required="chosen === null"
      :file-name="chosen?.name ?? ''"
      :active="dropActive"
      :step="step"
      :progress="progress"
      :ready="ready"
      :error="error"
      @choice="onChoice"
      @folder="onFolderChoice"
      @dragenter.prevent="onDragenter"
      @dragover.capture.prevent.stop="onDragover"
      @dragleave="onDragleave"
      @drop.capture.prevent.stop="onDrop"
    >
      <template #actions>
        <nldd-button
          appearance="primary"
          type="submit"
          :text="t('publish.upload.submit')"
          :loading="busy || step !== '' || undefined"
          data-testid="upload-publish"
        ></nldd-button>
      </template>
    </UploadBlock>
  </nldd-form>
</template>
