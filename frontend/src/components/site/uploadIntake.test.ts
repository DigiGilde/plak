import { describe, expect, it, vi } from 'vitest';
import { watch } from 'vue';

import { type DropResult } from './packing';
import { untilQuiet } from './testHelpers';
import { useUploadIntake } from './uploadIntake';

function page(): File {
  return new File(['<h1>hoi</h1>'], 'index.html');
}

function bundle(name: string | null = 'public'): DropResult {
  return { kind: 'bundle', name, files: [{ path: 'index.html', file: page() }] };
}

/** A read that stays open until the test says what came out of it. */
function pendingRead() {
  let finish!: (result: DropResult) => void;
  const read = new Promise<DropResult>((resolve) => {
    finish = resolve;
  });
  return { read: () => read, finish };
}

describe('useUploadIntake', () => {
  it('calls the read before it returns, while a drop event is still valid', () => {
    const intake = useUploadIntake(vi.fn(), vi.fn());
    const read = vi.fn(() => bundle());

    void intake.receive(read);

    expect(read).toHaveBeenCalledOnce();
  });

  it('goes from reading through packing to ready', async () => {
    const adopt = vi.fn();
    const intake = useUploadIntake(adopt, vi.fn());
    const steps: string[] = [];
    const counts: unknown[] = [];
    watch(intake.step, (step) => steps.push(step), { flush: 'sync' });
    watch(intake.progress, (progress) => counts.push(progress), { flush: 'sync' });

    await intake.receive(() => bundle());

    expect(steps).toEqual(['reading', 'packing', '']);
    expect(counts).toEqual([{ done: 0, total: 1 }, { done: 1, total: 1 }, null]);
    expect((adopt.mock.calls[0]![0] as File).name).toBe('public.tar.gz');
    expect(adopt.mock.calls[0]![1]).toBe(true);
    expect(intake.ready.value).toContain('public');
    expect(intake.error.value).toBeNull();
  });

  it('says a pack of loose contents was named by nobody', async () => {
    const adopt = vi.fn();
    const intake = useUploadIntake(adopt, vi.fn());

    await intake.receive(() => bundle(null));

    expect((adopt.mock.calls[0]![0] as File).name).toBe('site.tar.gz');
    expect(adopt.mock.calls[0]![1]).toBe(false);
    expect(intake.ready.value).toMatch(/^1 bestand ingepakt, /);
  });

  it('keeps its own description when adopting makes the field report a choice', async () => {
    // PublishSheet: handing the archive to the field fires its change event
    // synchronously, which arrives here as a plain choice of that file.
    const intake = useUploadIntake((file) => intake.noteChoice(file), vi.fn());

    await intake.receive(() => bundle());

    expect(intake.ready.value).toContain('Map "public" ingepakt');
  });

  it('forwards a single usable file unchanged', async () => {
    const adopt = vi.fn();
    const intake = useUploadIntake(adopt, vi.fn());
    const dropped = new File(['pk'], 'site.zip');

    await intake.receive(() => ({ kind: 'file', file: dropped }));

    expect(adopt).toHaveBeenCalledWith(dropped, true);
    expect(intake.ready.value).toContain('site.zip');
  });

  it('passes on the reason of a read that refuses, and adopts nothing', async () => {
    const adopt = vi.fn();
    const intake = useUploadIntake(adopt, vi.fn());

    await intake.receive(() => ({ kind: 'refusal', reason: 'Nee.' }));

    expect(intake.error.value).toBe('Nee.');
    expect(adopt).not.toHaveBeenCalled();
    expect(intake.step.value).toBe('');
  });

  it('refuses a bundle the checks object to, without packing it', async () => {
    const adopt = vi.fn();
    const intake = useUploadIntake(adopt, vi.fn());

    await intake.receive(() => ({
      kind: 'bundle',
      name: 'public',
      files: [{ path: 'lees-mij.txt', file: page() }],
    }));

    expect(intake.error.value).toContain('index.html');
    expect(adopt).not.toHaveBeenCalled();
  });

  it('says so when the browser cannot pack', async () => {
    vi.stubGlobal('CompressionStream', undefined);
    const adopt = vi.fn();
    const intake = useUploadIntake(adopt, vi.fn());

    await intake.receive(() => bundle());
    vi.unstubAllGlobals();

    expect(intake.error.value).toContain('Deze browser kan een map niet zelf inpakken');
    expect(adopt).not.toHaveBeenCalled();
  });

  it.each<[string, () => DropResult | Promise<DropResult>]>([
    ['throws', () => { throw new Error('stuk'); }],
    ['rejects', () => Promise.reject(new Error('stuk'))],
  ])('reports a read that %s', async (_how, read) => {
    const adopt = vi.fn();
    const intake = useUploadIntake(adopt, vi.fn());

    await intake.receive(read);

    expect(intake.error.value).toContain('Inpakken is niet gelukt');
    expect(intake.step.value).toBe('');
    expect(adopt).not.toHaveBeenCalled();
  });

  describe('what is handed over last is the new state', () => {
    it('forgets the earlier choice before the read is even called', () => {
      const order: string[] = [];
      const intake = useUploadIntake(vi.fn(), () => order.push('discard'));

      void intake.receive(() => {
        order.push('read');
        return bundle();
      });

      expect(order).toEqual(['discard', 'read']);
    });

    it('stops saying the earlier file is ready as soon as a new read starts', async () => {
      const intake = useUploadIntake(vi.fn(), vi.fn());
      await intake.receive(() => ({ kind: 'file', file: page() }));
      expect(intake.ready.value).not.toBe('');

      void intake.receive(pendingRead().read);

      expect(intake.ready.value).toBe('');
    });

    it.each<[string, () => DropResult | Promise<DropResult>]>([
      ['a refusal', () => ({ kind: 'refusal', reason: 'Nee.' })],
      ['an objection', () => ({ kind: 'bundle', name: 'x', files: [] })],
      ['a read that fails', () => Promise.reject(new Error('stuk'))],
    ])('says only what is wrong after %s, with the earlier file gone', async (_how, read) => {
      const adopt = vi.fn();
      const discard = vi.fn();
      const intake = useUploadIntake(adopt, discard);
      await intake.receive(() => ({ kind: 'file', file: page() }));
      adopt.mockClear();
      discard.mockClear();

      await intake.receive(read);

      expect(discard).toHaveBeenCalledOnce();
      expect(adopt).not.toHaveBeenCalled();
      expect(intake.ready.value).toBe('');
      expect(intake.error.value).not.toBeNull();
    });

    it('does not let the field reporting that it is empty overrule the new read', async () => {
      // PublishSheet and UploadZone: emptying the field fires its change event
      // synchronously, which arrives here as a choice of nothing.
      const adopt = vi.fn();
      const intake = useUploadIntake(adopt, () => intake.noteChoice(null));

      await intake.receive(() => bundle());

      expect((adopt.mock.calls[0]![0] as File).name).toBe('public.tar.gz');
      expect(intake.ready.value).toContain('public');
    });
  });

  it('clears an earlier refusal on the next read', async () => {
    const intake = useUploadIntake(vi.fn(), vi.fn());
    await intake.receive(() => ({ kind: 'refusal', reason: 'Nee.' }));

    const { read } = pendingRead();
    void intake.receive(read);

    expect(intake.error.value).toBeNull();
  });

  describe('when something newer arrives', () => {
    it('lets a second read win, however late the first one finishes', async () => {
      const adopt = vi.fn();
      const intake = useUploadIntake(adopt, vi.fn());
      const first = pendingRead();
      const second = pendingRead();

      const firstDone = intake.receive(first.read);
      const secondDone = intake.receive(second.read);

      const winner = new File(['pk'], 'tweede.zip');
      second.finish({ kind: 'file', file: winner });
      await secondDone;
      first.finish({ kind: 'refusal', reason: 'Te laat.' });
      await firstDone;

      expect(adopt).toHaveBeenCalledTimes(1);
      expect(adopt).toHaveBeenCalledWith(winner, true);
      expect(intake.error.value).toBeNull();
      expect(intake.ready.value).toContain('tweede.zip');
    });

    it('does not let the first read end the progress of the second', async () => {
      const intake = useUploadIntake(vi.fn(), vi.fn());
      const first = pendingRead();
      const second = pendingRead();

      const firstDone = intake.receive(first.read);
      void intake.receive(second.read);
      first.finish({ kind: 'file', file: page() });
      await firstDone;

      expect(intake.step.value).toBe('reading');
    });

    it('keeps the progress of an overtaken pack off the screen', async () => {
      const intake = useUploadIntake(vi.fn(), vi.fn());
      const first = pendingRead();
      const second = pendingRead();

      const firstDone = intake.receive(first.read);
      void intake.receive(second.read);
      first.finish(bundle());
      await firstDone;

      expect(intake.step.value).toBe('reading');
      expect(intake.progress.value).toBeNull();
    });

    it('lets a choice in the field overrule what is still being read', async () => {
      const adopt = vi.fn();
      const intake = useUploadIntake(adopt, vi.fn());
      const { read, finish } = pendingRead();

      const done = intake.receive(read);
      const picked = new File(['pk'], 'gekozen.zip');
      intake.noteChoice(picked);
      expect(intake.step.value).toBe('');
      finish(bundle());
      await done;
      await untilQuiet(2);

      expect(adopt).not.toHaveBeenCalled();
      expect(intake.ready.value).toContain('gekozen.zip');
    });

    it('forgets everything when the field is emptied', async () => {
      const adopt = vi.fn();
      const intake = useUploadIntake(adopt, vi.fn());
      await intake.receive(() => ({ kind: 'file', file: page() }));
      expect(intake.ready.value).not.toBe('');

      const { read, finish } = pendingRead();
      const done = intake.receive(read);
      intake.noteChoice(null);
      finish({ kind: 'refusal', reason: 'Te laat.' });
      await done;

      expect(intake.ready.value).toBe('');
      expect(intake.error.value).toBeNull();
      expect(intake.step.value).toBe('');
      expect(intake.progress.value).toBeNull();
    });
  });

  describe('a folder from the file dialog', () => {
    function folderEvent(files: File[]): { event: Event; input: HTMLInputElement } {
      const input = document.createElement('input');
      Object.defineProperty(input, 'files', { value: files, configurable: true });
      const event = new Event('change');
      Object.defineProperty(event, 'target', { value: input });
      return { event, input };
    }

    it('packs what was chosen', async () => {
      const adopt = vi.fn();
      const intake = useUploadIntake(adopt, vi.fn());
      const index = page();
      Object.defineProperty(index, 'webkitRelativePath', { value: 'public/index.html' });

      await intake.onFolderChoice(folderEvent([index]).event);

      expect((adopt.mock.calls[0]![0] as File).name).toBe('public.tar.gz');
    });

    it('empties the input, so that choosing the same folder again is a change again', async () => {
      const intake = useUploadIntake(vi.fn(), vi.fn());
      const { event, input } = folderEvent([]);
      const emptied = vi.spyOn(input, 'value', 'set');

      await intake.onFolderChoice(event);

      expect(emptied).toHaveBeenCalledWith('');
    });
  });
});
