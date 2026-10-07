/**
 * Saves a one-line text field without a button: when the field loses the
 * focus or Enter is pressed (`flush`), after a pause in typing, and when the
 * page or tab is left. Not on every keystroke: every save of a group name or a
 * site title writes the previous text into the audit log, where it stays.
 *
 * The text is checked the way the API checks it (`_validate_text` in
 * api/admin.py): trimmed, not empty, at most `maxLength` code points. A text
 * that is too long is judged while typing; an empty one only when a save is
 * due, so clearing the field to type anew is not met with a verdict at once.
 * Neither is sent. A text equal to the saved one is not sent either.
 *
 * The target (which group or site) is taken when the edit is made, not when
 * the save runs: a route change from one site to the next reuses the tab, and
 * a pending save must still go to the site it was typed on.
 *
 * One save at a time: an edit that comes in while one is on its way is saved
 * after it, from the text as it is then.
 */
import { computed, inject, onBeforeUnmount, onMounted, ref, type Ref } from 'vue';
import { matchedRouteKey, onBeforeRouteLeave, onBeforeRouteUpdate } from 'vue-router';

import { ApiError } from '@/api/client';
import { type MessageKey, t } from '@/i18n';

/** How long typing has to stop before the text is saved by itself. */
export const AUTOSAVE_PAUSE_MS = 3000;

export interface AutosavedTextOptions<T> {
  /** The text as the server holds it; the composable updates it after a save. */
  saved: Ref<string>;
  /** What the text belongs to, read at every edit. */
  target: () => T;
  /** Saves `text` for `target` and returns the text the server stored. */
  save: (target: T, text: string) => Promise<string>;
  maxLength: number;
  required: MessageKey;
  tooLong: MessageKey;
  /** Refusal codes of the API, each with the words the field shows for it. */
  refusals: ReadonlyMap<string, MessageKey>;
  /** After a save, with the stored text. */
  onSaved: (text: string) => void;
  /** A failure that is not a refusal of the text itself. */
  onFailed: (failure: unknown) => void;
}

export function useAutosavedText<T>(options: AutosavedTextOptions<T>) {
  const input = ref(options.saved.value);
  const refusal = ref<string | null>(null);
  const busy = ref(false);
  /** The field shows the text the server holds, saved just now: a check mark. */
  const justSaved = ref(false);

  const tooLong = computed(() => [...input.value.trim()].length > options.maxLength);
  /** Said at the field: too long while typing, else why a save did not go. */
  const verdict = computed(() => (tooLong.value ? t(options.tooLong) : refusal.value));

  let timer: ReturnType<typeof setTimeout> | undefined;
  let target: T = options.target();
  let again = false;
  // Bumped by reset: a save that returns after it belongs to what was shown
  // before, and leaves the field alone.
  let generation = 0;

  function stopTimer(): void {
    clearTimeout(timer);
    timer = undefined;
  }

  /** Shows the server's text, as after loading, and drops what was pending. */
  function reset(text: string): void {
    stopTimer();
    generation += 1;
    options.saved.value = text;
    input.value = text;
    refusal.value = null;
    justSaved.value = false;
  }

  function edit(text: string): void {
    input.value = text;
    refusal.value = null;
    justSaved.value = false;
    target = options.target();
    stopTimer();
    timer = setTimeout(() => void flush(), AUTOSAVE_PAUSE_MS);
  }

  let running: Promise<void> | null = null;

  /** Saves now; while a save is on its way, waits for it and what follows. */
  function flush(): Promise<void> {
    stopTimer();
    if (running) {
      again = true;
      return running;
    }
    running = saveNow().finally(() => {
      running = null;
    });
    return running;
  }

  async function saveNow(): Promise<void> {
    const text = input.value.trim();
    if (tooLong.value || text === options.saved.value) return;
    if (text === '') {
      refusal.value = t(options.required);
      return;
    }
    busy.value = true;
    const started = generation;
    try {
      const stored = await options.save(target, text);
      if (started !== generation) return;
      options.saved.value = stored;
      // Only when nothing was typed meanwhile: that stays, and saves next.
      if (input.value.trim() === text) {
        input.value = stored;
        justSaved.value = true;
      }
      options.onSaved(stored);
    } catch (failure) {
      if (started !== generation) return;
      const key = failure instanceof ApiError ? options.refusals.get(failure.problem.code ?? '') : undefined;
      if (key) refusal.value = t(key);
      else options.onFailed(failure);
    } finally {
      busy.value = false;
    }
    const more = again && input.value.trim() !== text;
    again = false;
    if (more) await saveNow();
  }

  // Leaving the tab for another route, or this route for another site or
  // group, waits for the save: the page around it hears of a save only while
  // the tab is still there to tell it.
  if (inject(matchedRouteKey, null)) {
    onBeforeRouteLeave(() => flush());
    onBeforeRouteUpdate(() => flush());
  }

  // Closing or reloading the page: a pending save goes out now. The browser
  // may cut it off, which is why the pause above is short.
  const leave = (): void => void flush();
  onMounted(() => window.addEventListener('pagehide', leave));
  onBeforeUnmount(() => {
    window.removeEventListener('pagehide', leave);
    if (timer !== undefined) void flush();
  });

  return { input, verdict, busy, justSaved, edit, flush, reset };
}
