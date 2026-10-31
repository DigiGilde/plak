import { onMounted, ref, watch, type WatchSource } from 'vue';

/**
 * The load scaffold of a page or tab: a `loading` flag, the `error` of the
 * last attempt, a first load on mount and a new one whenever `deps` change.
 *
 * `fetch` only fetches and `apply` stores the result, so an answer that comes
 * back after a newer request was started is dropped instead of overwriting
 * the newer one. `reload({ quiet: true })` refreshes without touching
 * `loading` or `error` and swallows a failure: for refreshing behind content
 * that must not be torn down. A quiet reload gives way to a loud one started
 * after it, never the other way round. `enabled` skips loading while the
 * input is not there yet.
 */
export function useLoader<T>(
  fetch: () => Promise<T>,
  apply: (data: T) => void,
  deps: WatchSource<unknown> | null = null,
  enabled: () => boolean = () => true,
) {
  const loading = ref(true);
  const error = ref<unknown>(null);
  let latest = 0;

  async function reload(options: { quiet?: boolean } = {}): Promise<void> {
    if (!enabled()) return;
    const quiet = options.quiet === true;
    if (!quiet) {
      latest += 1;
      loading.value = true;
      error.value = null;
    }
    const run = latest;
    try {
      const data = await fetch();
      if (run === latest) apply(data);
    } catch (f) {
      if (run === latest && !quiet) error.value = f;
    } finally {
      if (run === latest && !quiet) loading.value = false;
    }
  }

  onMounted(() => reload());
  if (deps) watch(deps, () => reload());

  return { loading, error, reload };
}
