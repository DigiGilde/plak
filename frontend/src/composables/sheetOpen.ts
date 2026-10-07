import { nextTick, type Ref, watch } from 'vue';

type SheetElement = HTMLElement & { show: () => void; hide: () => void };

/**
 * Keeps an `nldd-sheet` in step with an `open` prop. The sheet is driven
 * through its imperative API rather than mounted and unmounted, so the
 * animation plays (nldd skill); the optional calls keep jsdom (no show/hide)
 * working in tests. `onOpen` runs first, before the sheet shows.
 */
export function useSheetOpen(
  open: () => boolean,
  sheet: Ref<SheetElement | undefined>,
  onOpen?: () => void,
): void {
  watch(
    open,
    async (isOpen) => {
      if (!isOpen) {
        sheet.value?.hide?.();
        return;
      }
      onOpen?.();
      await nextTick();
      sheet.value?.show?.();
    },
    { immediate: true },
  );
}
