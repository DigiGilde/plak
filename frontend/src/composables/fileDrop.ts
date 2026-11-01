/**
 * Drag-and-drop plumbing shared between PublishSheet and the pages that open
 * it from a drop (Overview, Group): the dragenter/dragleave counting that
 * keeps a drag crossing into a nested element from flickering the drop state
 * off, and the window-level guard against the browser's own "open this file"
 * default.
 */
import { type Ref, ref, shallowRef, watch } from 'vue';

import { type DropResult, readDrop } from '@/components/site/packing';

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
    // Bound in the capture phase: an nldd-file-field inside the target judges
    // a file drag by its own rules, and must not get it as well.
    event.stopPropagation();
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
 * Dropping something straight onto a page: the drag state of the page, the
 * window guard, and what a drop does. The read starts here, and the sheet it
 * opens (`open`) carries on from `droppedRead`, because a DataTransfer only
 * answers during the drop event itself and the sheet opens after it. All of it
 * is gated by `enabled`, the same condition as the button that opens the sheet,
 * and the read is forgotten when the sheet closes so that opening it again
 * through the button does not carry it along.
 */
export function usePageDrop(enabled: Ref<boolean>, open: Ref<boolean>) {
  const { isOver, onDragEnter, onDragOver, onDragLeave, reset } = useDropState();
  const droppedRead = shallowRef<Promise<DropResult> | null>(null);

  useWindowDropGuard(enabled);

  function onPageDrop(event: DragEvent): void {
    if (!enabled.value) return;
    event.preventDefault();
    reset();
    const transfer = event.dataTransfer;
    if (!transfer?.types.includes('Files')) return;
    droppedRead.value = readDrop(transfer);
    open.value = true;
  }

  watch(open, (isOpen) => {
    if (!isOpen) droppedRead.value = null;
  });

  return {
    dropTargetActive: isOver,
    droppedRead,
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
