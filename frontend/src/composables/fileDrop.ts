/**
 * Drag-and-drop plumbing shared between PublishSheet and the pages that open
 * it from a drop (Overview, Group): the file types the upload API accepts
 * (`docs/publishing.md`, mirrored in PublishSheet's `nldd-file-field
 * accept`), the dragenter/dragleave counting that keeps a drag crossing into
 * a nested element from flickering the drop state off, and the window-level
 * guard against the browser's own "open this file" default.
 */
import { type Ref, ref, watch } from 'vue';

import { t } from '@/i18n';

const ACCEPTED_EXTENSIONS = ['.tar.gz', '.tgz', '.zip', '.html'] as const;

/**
 * A function, not a constant: a constant is built once, when the module is
 * imported, and would freeze the language of whoever loaded the page first.
 */
function dropFileHint(): string {
  return t('publish.drop.oneFile');
}

function hasAcceptedExtension(name: string): boolean {
  const lower = name.toLowerCase();
  return ACCEPTED_EXTENSIONS.some((extension) => lower.endsWith(extension));
}

export interface DropResult {
  /** Set when the drop carried exactly one file of an accepted type. */
  file: File | null;
  /** Dutch, ready to show, when the drop was rejected. */
  error: string | null;
}

/**
 * Validates what a drop offered, mirroring `nldd-file-field
 * accept=".zip,.tar.gz,.tgz,.html"` in PublishSheet.vue: a single file, not a
 * directory, matching one of the accepted extensions. Both `file` and `error`
 * come back null for a drop that carried nothing file-shaped (e.g. dragged
 * text), so callers can ignore it silently rather than complain.
 */
export function resolveDroppedFile(dataTransfer: DataTransfer | null): DropResult {
  const files = dataTransfer?.files;
  if (!files || files.length === 0) return { file: null, error: null };

  const items = dataTransfer?.items;
  if (items) {
    for (const item of items) {
      const entry = item.kind === 'file' ? item.webkitGetAsEntry?.() : null;
      if (entry?.isDirectory) return { file: null, error: t('publish.drop.noFolder', { hint: dropFileHint() }) };
    }
  }

  if (files.length > 1) return { file: null, error: dropFileHint() };

  const file = files[0];
  if (!hasAcceptedExtension(file.name)) return { file: null, error: dropFileHint() };

  return { file, error: null };
}

/**
 * Drag-over state for one drop target, meant to be wired to a single element
 * (`onDragEnter`/`onDragOver`/`onDragLeave` bound directly on it, not on
 * separate nested children): the browser fires `dragenter` on a nested child
 * before `dragleave` on the parent, so a plain counter incremented on every
 * enter and decremented on every leave never dips to zero while the pointer
 * is still somewhere inside.
 */
export function useDropState() {
  const isOver = ref(false);
  let depth = 0;

  function isFileDrag(event: DragEvent): boolean {
    return event.dataTransfer?.types.includes('Files') ?? false;
  }

  function onDragEnter(event: DragEvent): void {
    if (!isFileDrag(event)) return;
    depth += 1;
    isOver.value = true;
  }

  function onDragOver(event: DragEvent): void {
    if (!isFileDrag(event)) return;
    // Required for `drop` to fire at all; also doubles as the browser's own
    // "open this file" prevention while over the target itself.
    event.preventDefault();
  }

  function onDragLeave(event: DragEvent): void {
    if (!isFileDrag(event)) return;
    depth = Math.max(0, depth - 1);
    if (depth === 0) isOver.value = false;
  }

  function reset(): void {
    depth = 0;
    isOver.value = false;
  }

  return { isOver, onDragEnter, onDragOver, onDragLeave, reset };
}

/**
 * Stops the browser from navigating to or opening a file dropped anywhere on
 * the window while `active` is true, so a drop that misses every drop target
 * cannot blow away the SPA. Toggles with `active` and always cleans up its
 * own listeners, so a page that loses eligibility (or unmounts) never leaves
 * one behind.
 */
export function useWindowDropGuard(active: Ref<boolean>): void {
  function preventDefault(event: DragEvent): void {
    if (event.dataTransfer?.types.includes('Files')) event.preventDefault();
  }

  watch(
    active,
    (isActive, _previous, onCleanup) => {
      if (!isActive) return;
      window.addEventListener('dragover', preventDefault);
      window.addEventListener('drop', preventDefault);
      onCleanup(() => {
        window.removeEventListener('dragover', preventDefault);
        window.removeEventListener('drop', preventDefault);
      });
    },
    { immediate: true },
  );
}

/**
 * Dropping a file straight onto a page: the drag state of the page, the
 * window guard, and what a drop does. A valid file lands in `droppedFile` and
 * opens the sheet (`open`); a rejected one lands in `dropError`. All of it is
 * gated by `enabled`, the same condition as the button that opens the sheet,
 * and the file is forgotten when the sheet closes so that opening it again
 * through the button does not carry it along.
 */
export function usePageDrop(enabled: Ref<boolean>, open: Ref<boolean>) {
  const { isOver, onDragEnter, onDragOver, onDragLeave, reset } = useDropState();
  const droppedFile = ref<File | null>(null);
  const dropError = ref<string | null>(null);

  useWindowDropGuard(enabled);

  function onPageDrop(event: DragEvent): void {
    if (!enabled.value) return;
    event.preventDefault();
    reset();
    const { file, error: rejection } = resolveDroppedFile(event.dataTransfer);
    if (rejection) {
      dropError.value = rejection;
      return;
    }
    if (!file) return;
    dropError.value = null;
    droppedFile.value = file;
    open.value = true;
  }

  watch(open, (isOpen) => {
    if (!isOpen) droppedFile.value = null;
  });

  return {
    dropTargetActive: isOver,
    droppedFile,
    dropError,
    onPageDragEnter: (event: DragEvent) => {
      if (enabled.value) onDragEnter(event);
    },
    onPageDragOver: (event: DragEvent) => {
      if (enabled.value) onDragOver(event);
    },
    onPageDragLeave: (event: DragEvent) => {
      if (enabled.value) onDragLeave(event);
    },
    onPageDrop,
  };
}
