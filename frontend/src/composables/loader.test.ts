import { mount } from '@vue/test-utils';
import { describe, expect, it } from 'vitest';
import { defineComponent, h, nextTick, ref } from 'vue';

import { useLoader } from './loader';

/** A request the test settles by hand, to put answers in any order. */
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((ok, fail) => {
    resolve = ok;
    reject = fail;
  });
  return { promise, resolve, reject };
}

function mountLoader(options: {
  fetch: () => Promise<string>;
  deps?: () => unknown;
  enabled?: () => boolean;
}) {
  const stored = ref('');
  let loader!: ReturnType<typeof useLoader<string>>;
  const Probe = defineComponent({
    setup() {
      loader = useLoader(
        options.fetch,
        (data) => {
          stored.value = data;
        },
        options.deps ?? null,
        options.enabled,
      );
      return () => h('div');
    },
  });
  mount(Probe);
  return { loader, stored };
}

async function settle(): Promise<void> {
  for (let i = 0; i < 4; i += 1) await nextTick();
}

describe('useLoader', () => {
  it('loads on mount and hands the answer to apply', async () => {
    const { loader, stored } = mountLoader({ fetch: () => Promise.resolve('first') });
    expect(loader.loading.value).toBe(true);
    await settle();

    expect(stored.value).toBe('first');
    expect(loader.loading.value).toBe(false);
    expect(loader.error.value).toBeNull();
  });

  it('reports a failure and stops loading', async () => {
    const failure = new Error('down');
    const { loader, stored } = mountLoader({ fetch: () => Promise.reject(failure) });
    await settle();

    expect(loader.error.value).toBe(failure);
    expect(loader.loading.value).toBe(false);
    expect(stored.value).toBe('');
  });

  it('clears an earlier error when it loads again', async () => {
    let attempt = 0;
    const { loader, stored } = mountLoader({
      fetch: () => (attempt++ === 0 ? Promise.reject(new Error('down')) : Promise.resolve('ok')),
    });
    await settle();
    expect(loader.error.value).not.toBeNull();

    await loader.reload();

    expect(loader.error.value).toBeNull();
    expect(stored.value).toBe('ok');
  });

  it('ignores an older answer that arrives after a newer one', async () => {
    const answers = [deferred<string>(), deferred<string>()];
    let call = 0;
    const { loader, stored } = mountLoader({ fetch: () => answers[call++]!.promise });

    const second = loader.reload();
    answers[1]!.resolve('newer');
    await second;
    answers[0]!.resolve('older');
    await settle();

    expect(stored.value).toBe('newer');
    expect(loader.loading.value).toBe(false);
  });

  it('ignores the failure of an older request as well', async () => {
    const answers = [deferred<string>(), deferred<string>()];
    let call = 0;
    const { loader, stored } = mountLoader({ fetch: () => answers[call++]!.promise });

    const second = loader.reload();
    answers[1]!.resolve('newer');
    await second;
    answers[0]!.reject(new Error('late'));
    await settle();

    expect(loader.error.value).toBeNull();
    expect(stored.value).toBe('newer');
  });

  it('loads again when the deps change', async () => {
    const key = ref('a');
    let calls = 0;
    const { stored } = mountLoader({
      fetch: () => Promise.resolve(`${key.value}${(calls += 1)}`),
      deps: () => key.value,
    });
    await settle();
    expect(stored.value).toBe('a1');

    key.value = 'b';
    await settle();

    expect(stored.value).toBe('b2');
  });

  it('does not load while it is not enabled', async () => {
    let calls = 0;
    const { loader } = mountLoader({
      fetch: () => Promise.resolve(String((calls += 1))),
      enabled: () => false,
    });
    await settle();

    expect(calls).toBe(0);
    expect(loader.loading.value).toBe(true);
  });

  it('reloads quietly: no loading flag, and a failure leaves what was there', async () => {
    let fail = false;
    const { loader, stored } = mountLoader({
      fetch: () => (fail ? Promise.reject(new Error('down')) : Promise.resolve('fresh')),
    });
    await settle();

    const quiet = loader.reload({ quiet: true });
    expect(loader.loading.value).toBe(false);
    await quiet;

    fail = true;
    await loader.reload({ quiet: true });

    expect(loader.error.value).toBeNull();
    expect(loader.loading.value).toBe(false);
    expect(stored.value).toBe('fresh');
  });

  it('drops a quiet answer when a loud load started after it', async () => {
    const answers = [deferred<string>(), deferred<string>(), deferred<string>()];
    let call = 0;
    const { loader, stored } = mountLoader({ fetch: () => answers[call++]!.promise });
    answers[0]!.resolve('initial');
    await settle();

    const quiet = loader.reload({ quiet: true });
    const loud = loader.reload();
    answers[2]!.resolve('loud');
    await loud;
    answers[1]!.resolve('quiet');
    await quiet;

    expect(stored.value).toBe('loud');
    expect(loader.loading.value).toBe(false);
  });
});
