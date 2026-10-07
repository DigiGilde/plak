import { computed, ref, type Ref } from 'vue';

/**
 * The state behind a ConfirmModal: what is being asked about, whether the
 * action is running, and the three handlers the modal needs.
 *
 * `ask(item)` opens it, `cancel()` closes it, and `confirm()` runs `action`
 * on the item and closes the modal when that is done, whatever the outcome.
 * The action reports its own failure: a modal sits in the top layer and
 * renders the page below it inert, so a failure is notified from `action`
 * and the modal then closes in the same tick, leaving the notification
 * reachable. For a modal about nothing in particular, ask with `true`.
 */
export function useConfirm<T>(action: (item: T) => Promise<void>) {
  const target = ref(null) as Ref<T | null>;
  const busy = ref(false);
  const open = computed(() => target.value !== null);

  function ask(item: T): void {
    target.value = item;
  }

  function cancel(): void {
    target.value = null;
  }

  async function confirm(): Promise<void> {
    const item = target.value;
    /* v8 ignore start -- the modal only confirms while it is open, so there is an item. */
    if (item === null) return;
    /* v8 ignore stop */
    busy.value = true;
    try {
      await action(item);
    } finally {
      busy.value = false;
      target.value = null;
    }
  }

  return { target, open, busy, ask, cancel, confirm };
}
