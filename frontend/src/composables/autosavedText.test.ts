import { flushPromises, mount } from '@vue/test-utils';
import { defineComponent, h, ref } from 'vue';
import { createMemoryHistory, createRouter, RouterView } from 'vue-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError } from '@/api/client';
import type { MessageKey } from '@/i18n';

import { AUTOSAVE_PAUSE_MS, useAutosavedText } from './autosavedText';

type Save = (target: string, text: string) => Promise<string>;

const REFUSALS = new Map<string, MessageKey>([
  ['FIELD_CONTROL_CHARACTERS', 'site.settings.title.controlCharacters'],
]);

function refusal(code: string): ApiError {
  return new ApiError({ type: 'about:blank', title: 'Onverwerkbare invoer', status: 422, code });
}

/** A save that stays on its way until `finish` is called. */
function pendingSave() {
  const finishes: ((text: string) => void)[] = [];
  const save = vi.fn<Save>(
    () =>
      new Promise<string>((resolve) => {
        finishes.push(resolve);
      }),
  );
  return { save, finish: (index: number, text: string) => finishes[index]!(text) };
}

function setup(save: Save, initial = 'Kerncijfers') {
  const saved = ref(initial);
  const target = ref('rijksoverheid/kerncijfers');
  const onSaved = vi.fn();
  const onFailed = vi.fn();
  let field!: ReturnType<typeof useAutosavedText<string>>;
  const wrapper = mount(
    defineComponent({
      setup() {
        field = useAutosavedText({
          saved,
          target: () => target.value,
          save,
          maxLength: 20,
          required: 'site.settings.title.required',
          tooLong: 'site.settings.title.tooLong',
          refusals: REFUSALS,
          onSaved,
          onFailed,
        });
        return () => h('div');
      },
    }),
  );
  return { field, saved, target, onSaved, onFailed, wrapper };
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe('useAutosavedText', () => {
  it('starts from the saved text, with nothing judged', () => {
    const { field } = setup(vi.fn<Save>());

    expect(field.input.value).toBe('Kerncijfers');
    expect(field.verdict.value).toBeNull();
  });

  it('saves after a pause in typing, not before, and once for the last text', async () => {
    const save = vi.fn<Save>(async (_target, text) => text);
    const { field, saved, onSaved } = setup(save);

    field.edit('Kern');
    vi.advanceTimersByTime(AUTOSAVE_PAUSE_MS - 1);
    field.edit('Kerngetal');
    vi.advanceTimersByTime(AUTOSAVE_PAUSE_MS - 1);
    expect(save).not.toHaveBeenCalled();

    vi.advanceTimersByTime(1);
    await flushPromises();

    expect(save).toHaveBeenCalledTimes(1);
    expect(save).toHaveBeenCalledWith('rijksoverheid/kerncijfers', 'Kerngetal');
    expect(saved.value).toBe('Kerngetal');
    expect(onSaved).toHaveBeenCalledWith('Kerngetal');
  });

  it('saves at once on a flush, and the pause does not save it a second time', async () => {
    const save = vi.fn<Save>(async (_target, text) => text);
    const { field } = setup(save);

    field.edit('Kerngetal');
    await field.flush();
    vi.advanceTimersByTime(AUTOSAVE_PAUSE_MS);
    await flushPromises();

    expect(save).toHaveBeenCalledTimes(1);
  });

  it('marks the field saved after a save, until the next edit', async () => {
    const { field } = setup(vi.fn<Save>(async (_target, text) => text));
    expect(field.justSaved.value).toBe(false);

    field.edit('Kerngetal');
    await field.flush();
    expect(field.justSaved.value).toBe(true);

    field.edit('Kerngetallen');
    expect(field.justSaved.value).toBe(false);
  });

  it('does not mark the field saved when more was typed while the save was on its way', async () => {
    const { save, finish } = pendingSave();
    const { field } = setup(save);

    field.edit('Kerngetal');
    const first = field.flush();
    field.edit('Kerngetallen');
    finish(0, 'Kerngetal');
    await first;

    expect(field.justSaved.value).toBe(false);
  });

  it('drops the saved mark on a reset', async () => {
    const { field } = setup(vi.fn<Save>(async (_target, text) => text));

    field.edit('Kerngetal');
    await field.flush();
    field.reset('Jaarverslag');

    expect(field.justSaved.value).toBe(false);
  });

  it('shows the text the server stored, trimmed by it', async () => {
    const { field } = setup(vi.fn<Save>(async () => 'Kerngetal'));

    field.edit('  Kerngetal ');
    await field.flush();

    expect(field.input.value).toBe('Kerngetal');
  });

  it('sends nothing when the trimmed text is the saved one', async () => {
    const save = vi.fn<Save>();
    const { field, onSaved } = setup(save);

    field.edit('  Kerncijfers ');
    await field.flush();

    expect(save).not.toHaveBeenCalled();
    expect(onSaved).not.toHaveBeenCalled();
    expect(field.verdict.value).toBeNull();
  });

  it('judges an empty text only when a save is due, and sends nothing', async () => {
    const save = vi.fn<Save>();
    const { field } = setup(save);

    field.edit('   ');
    expect(field.verdict.value).toBeNull();
    vi.advanceTimersByTime(AUTOSAVE_PAUSE_MS);
    await flushPromises();

    expect(save).not.toHaveBeenCalled();
    expect(field.verdict.value).toBe('Een titel is nodig');
  });

  it('judges a text that is too long while typing, in code points, and never sends it', async () => {
    const save = vi.fn<Save>();
    const { field } = setup(save);

    // Twenty emoji are forty UTF-16 units but twenty characters, as Python counts.
    field.edit(` ${'🌅'.repeat(20)} `);
    expect(field.verdict.value).toBeNull();

    field.edit('a'.repeat(21));
    expect(field.verdict.value).toBe('Een titel is hoogstens 200 tekens lang');
    await field.flush();
    vi.advanceTimersByTime(AUTOSAVE_PAUSE_MS);
    await flushPromises();

    expect(save).not.toHaveBeenCalled();
  });

  it('shows a refusal of the text at the field and drops it on the next edit', async () => {
    const { field, onFailed } = setup(vi.fn<Save>().mockRejectedValue(refusal('FIELD_CONTROL_CHARACTERS')));

    field.edit('Kern\tcijfers');
    await field.flush();

    expect(field.verdict.value).toBe(
      'Een titel bevat geen onzichtbare tekens of regeleinden, die vaak meekomen met gekopieerde tekst',
    );
    expect(field.input.value).toBe('Kern\tcijfers');
    expect(onFailed).not.toHaveBeenCalled();

    field.edit('Kerncijfers 2026');
    expect(field.verdict.value).toBeNull();
  });

  it('hands any other failure on, and keeps what was typed', async () => {
    const failure = new TypeError('network down');
    const { field, saved, onFailed } = setup(vi.fn<Save>().mockRejectedValue(failure));

    field.edit('Kerngetal');
    await field.flush();

    expect(onFailed).toHaveBeenCalledWith(failure);
    expect(field.verdict.value).toBeNull();
    expect(field.input.value).toBe('Kerngetal');
    expect(saved.value).toBe('Kerncijfers');
  });

  it('hands on a problem without a code', async () => {
    const failure = new ApiError({ type: 'about:blank', title: 'Interne fout', status: 500 });
    const { field, onFailed } = setup(vi.fn<Save>().mockRejectedValue(failure));

    field.edit('Kerngetal');
    await field.flush();

    expect(onFailed).toHaveBeenCalledWith(failure);
    expect(field.verdict.value).toBeNull();
  });

  it('hands on a refusal it has no words for', async () => {
    const failure = refusal('SOMETHING_ELSE');
    const { field, onFailed } = setup(vi.fn<Save>().mockRejectedValue(failure));

    field.edit('Kerngetal');
    await field.flush();

    expect(onFailed).toHaveBeenCalledWith(failure);
  });

  describe('while a save is on its way', () => {
    it('sends nothing more for the same text', async () => {
      const { save, finish } = pendingSave();
      const { field } = setup(save);

      field.edit('Kerngetal');
      const first = field.flush();
      let waited = false;
      const second = field.flush().then(() => {
        waited = true;
      });
      await flushPromises();
      // The second waits for the first, as a route change does.
      expect(waited).toBe(false);
      finish(0, 'Kerngetal');
      await Promise.all([first, second]);

      expect(waited).toBe(true);
      expect(save).toHaveBeenCalledTimes(1);
    });

    it('saves what was typed meanwhile after it, and leaves that text in the field', async () => {
      const { save, finish } = pendingSave();
      const { field, onSaved } = setup(save);

      field.edit('Kerngetal');
      const first = field.flush();
      field.edit('Kerngetallen');
      void field.flush();
      finish(0, 'Kerngetal');
      await flushPromises();

      // The first answer does not overwrite what is being typed.
      expect(field.input.value).toBe('Kerngetallen');
      expect(save).toHaveBeenCalledTimes(2);
      expect(save).toHaveBeenLastCalledWith('rijksoverheid/kerncijfers', 'Kerngetallen');

      finish(1, 'Kerngetallen');
      await first;
      expect(onSaved).toHaveBeenLastCalledWith('Kerngetallen');
    });
  });

  it('saves to the target of the edit, also when the target changed since', async () => {
    const save = vi.fn<Save>(async (_target, text) => text);
    const { field, target } = setup(save);

    field.edit('Kerngetal');
    target.value = 'rijksoverheid/jaarverslag';
    await field.flush();

    expect(save).toHaveBeenCalledWith('rijksoverheid/kerncijfers', 'Kerngetal');
  });

  it('leaves the field alone when a save returns after a reset', async () => {
    const { save, finish } = pendingSave();
    const { field, saved, onSaved } = setup(save);

    field.edit('Kerngetal');
    const first = field.flush();
    field.reset('Jaarverslag');
    finish(0, 'Kerngetal');
    await first;

    expect(saved.value).toBe('Jaarverslag');
    expect(field.input.value).toBe('Jaarverslag');
    expect(onSaved).not.toHaveBeenCalled();
  });

  it('drops a pending save on a reset', async () => {
    const save = vi.fn<Save>();
    const { field } = setup(save);

    field.edit('Kerngetal');
    field.reset('Jaarverslag');
    vi.advanceTimersByTime(AUTOSAVE_PAUSE_MS);
    await flushPromises();

    expect(save).not.toHaveBeenCalled();
    expect(field.input.value).toBe('Jaarverslag');
  });

  it('keeps a refusal that comes back after a reset off the field', async () => {
    let fail: (reason: unknown) => void = () => {};
    const save = vi.fn<Save>(
      () =>
        new Promise<string>((_resolve, reject) => {
          fail = reject;
        }),
    );
    const { field, onFailed } = setup(save);

    field.edit('Kern\tcijfers');
    const first = field.flush();
    field.reset('Jaarverslag');
    fail(refusal('FIELD_CONTROL_CHARACTERS'));
    await first;

    expect(field.verdict.value).toBeNull();
    expect(onFailed).not.toHaveBeenCalled();
  });

  it('saves a pending text when the page is left', async () => {
    const save = vi.fn<Save>(async (_target, text) => text);
    const { field } = setup(save);

    field.edit('Kerngetal');
    window.dispatchEvent(new Event('pagehide'));
    await flushPromises();

    expect(save).toHaveBeenCalledWith('rijksoverheid/kerncijfers', 'Kerngetal');
  });

  it('saves a pending text when the tab goes away, and listens no longer after', async () => {
    const save = vi.fn<Save>(async (_target, text) => text);
    const { field, wrapper } = setup(save);

    field.edit('Kerngetal');
    wrapper.unmount();
    await flushPromises();
    expect(save).toHaveBeenCalledTimes(1);

    window.dispatchEvent(new Event('pagehide'));
    await flushPromises();
    expect(save).toHaveBeenCalledTimes(1);
  });

  it('sends nothing when the tab goes away with nothing pending', async () => {
    const save = vi.fn<Save>();
    const { wrapper } = setup(save);

    wrapper.unmount();
    await flushPromises();

    expect(save).not.toHaveBeenCalled();
  });
});

describe('useAutosavedText under the router', () => {
  async function routed(save: Save) {
    const onSaved = vi.fn();
    const target = ref('rijksoverheid/kerncijfers');
    let field!: ReturnType<typeof useAutosavedText<string>>;
    const Tab = defineComponent({
      setup() {
        field = useAutosavedText({
          saved: ref('Kerncijfers'),
          target: () => target.value,
          save,
          maxLength: 20,
          required: 'site.settings.title.required',
          tooLong: 'site.settings.title.tooLong',
          refusals: REFUSALS,
          onSaved,
          onFailed: vi.fn(),
        });
        return () => h('div');
      },
    });
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/:site/settings', component: Tab },
        { path: '/:site/overview', component: { render: () => h('p') } },
      ],
    });
    await router.push('/kerncijfers/settings');
    mount(RouterView, { global: { plugins: [router] } });
    await router.isReady();
    return { field: () => field, onSaved, router };
  }

  it('lets a route change wait for the save, so the tab is still there to report it', async () => {
    const { save, finish } = pendingSave();
    const { field, onSaved, router } = await routed(save);

    field().edit('Kerngetal');
    let arrived = false;
    const navigation = router.push('/kerncijfers/overview').then(() => {
      arrived = true;
    });
    await flushPromises();
    expect(save).toHaveBeenCalledWith('rijksoverheid/kerncijfers', 'Kerngetal');
    expect(arrived).toBe(false);

    finish(0, 'Kerngetal');
    await navigation;
    expect(onSaved).toHaveBeenCalledWith('Kerngetal');
    expect(router.currentRoute.value.path).toBe('/kerncijfers/overview');
  });

  it('saves before the same tab moves on to another site', async () => {
    const save = vi.fn<Save>(async (_target, text) => text);
    const { field, onSaved, router } = await routed(save);

    field().edit('Kerngetal');
    await router.push('/jaarverslag/settings');

    expect(save).toHaveBeenCalledTimes(1);
    expect(onSaved).toHaveBeenCalledWith('Kerngetal');
  });
});
