import { effectScope, ref } from 'vue';
import { describe, expect, it, vi } from 'vitest';

import { resolveDroppedFile, useDropState, useWindowDropGuard } from './fileDrop';

function fakeDataTransfer(types: string[]): DataTransfer {
  return { types } as unknown as DataTransfer;
}

function dragEvent(dt: DataTransfer | undefined): DragEvent {
  return { dataTransfer: dt } as unknown as DragEvent;
}

function windowDragEvent(type: string, dt: DataTransfer): Event {
  const event = new Event(type, { bubbles: true, cancelable: true });
  Object.defineProperty(event, 'dataTransfer', { value: dt });
  return event;
}

describe('resolveDroppedFile', () => {
  it('ignores a drop that carries no files at all', () => {
    expect(resolveDroppedFile({ files: [] } as unknown as DataTransfer)).toEqual({
      file: null,
      error: null,
    });
    expect(resolveDroppedFile(null)).toEqual({ file: null, error: null });
  });

  it('rejects a dropped folder, mirroring the accept attribute', () => {
    const file = new File(['<h1>hoi</h1>'], 'map', { type: '' });
    const dt = {
      files: [file],
      items: [{ kind: 'file', webkitGetAsEntry: () => ({ isDirectory: true }) }],
    } as unknown as DataTransfer;

    const result = resolveDroppedFile(dt);

    expect(result.file).toBeNull();
    expect(result.error).toBeTruthy();
  });

  it('skips a non-file item (e.g. dragged text alongside the file) when checking for a folder', () => {
    const file = new File(['hoi'], 'site.zip', { type: 'application/zip' });
    const dt = {
      files: [file],
      items: [{ kind: 'string' }, { kind: 'file', webkitGetAsEntry: () => ({ isDirectory: false }) }],
    } as unknown as DataTransfer;

    const result = resolveDroppedFile(dt);

    expect(result.file).toBe(file);
    expect(result.error).toBeNull();
  });

  it('accepts a single file of an accepted type', () => {
    const file = new File(['hoi'], 'site.zip', { type: 'application/zip' });
    const dt = { files: [file] } as unknown as DataTransfer;

    const result = resolveDroppedFile(dt);

    expect(result.file).toBe(file);
    expect(result.error).toBeNull();
  });

  it('accepts every extension the accept attribute lists', () => {
    for (const name of ['site.tar.gz', 'site.tgz', 'site.html']) {
      const file = new File(['hoi'], name);
      const result = resolveDroppedFile({ files: [file] } as unknown as DataTransfer);

      expect(result.file, name).toBe(file);
    }
  });

  it('rejects more than one file, even of an accepted type', () => {
    const dt = {
      files: [new File(['a'], 'a.zip'), new File(['b'], 'b.zip')],
    } as unknown as DataTransfer;

    const result = resolveDroppedFile(dt);

    expect(result.file).toBeNull();
    expect(result.error).toBeTruthy();
  });

  it('rejects a file of an unsupported type', () => {
    const file = new File(['hoi'], 'site.pdf', { type: 'application/pdf' });
    const dt = { files: [file] } as unknown as DataTransfer;

    const result = resolveDroppedFile(dt);

    expect(result.file).toBeNull();
    expect(result.error).toBeTruthy();
  });
});

describe('useDropState', () => {
  it('ignores a drag that carries no files', () => {
    const state = useDropState();

    state.onDragEnter(dragEvent(fakeDataTransfer([])));
    expect(state.isOver.value).toBe(false);
  });

  it('leaves the browser default prevented while a file drag is over the target', () => {
    const state = useDropState();
    const event = dragEvent(fakeDataTransfer(['Files']));
    const preventDefault = (event.preventDefault = vi.fn());

    state.onDragOver(event);

    expect(preventDefault).toHaveBeenCalledTimes(1);
  });

  it('does not intercept dragover for a drag without files', () => {
    const state = useDropState();
    const event = dragEvent(fakeDataTransfer([]));
    const preventDefault = (event.preventDefault = vi.fn());

    state.onDragOver(event);

    expect(preventDefault).not.toHaveBeenCalled();
  });

  it('ignores an event without a dataTransfer at all', () => {
    const state = useDropState();

    state.onDragEnter(dragEvent(undefined));

    expect(state.isOver.value).toBe(false);
  });

  it('only clears the drag state once every nested dragenter has a matching dragleave', () => {
    const state = useDropState();
    const dt = fakeDataTransfer(['Files']);

    state.onDragEnter(dragEvent(dt));
    // A dragenter on a child fires before the dragleave of its parent, so the
    // depth counter must dip to zero only once, not on the first leave.
    state.onDragEnter(dragEvent(dt));
    state.onDragLeave(dragEvent(dt));
    expect(state.isOver.value).toBe(true);

    state.onDragLeave(dragEvent(dt));
    expect(state.isOver.value).toBe(false);
  });

  it('ignores a dragleave for a drag without files', () => {
    const state = useDropState();
    state.onDragEnter(dragEvent(fakeDataTransfer(['Files'])));

    state.onDragLeave(dragEvent(fakeDataTransfer([])));

    // A non-file dragleave must not touch the depth counter of a real drag.
    expect(state.isOver.value).toBe(true);
  });

  it('resets the drag state and its depth counter', () => {
    const state = useDropState();
    const dt = fakeDataTransfer(['Files']);
    state.onDragEnter(dragEvent(dt));
    state.onDragEnter(dragEvent(dt));

    state.reset();

    expect(state.isOver.value).toBe(false);
    // With the counter itself reset, a single leave must not dip below zero
    // and misreport the state as still active.
    state.onDragEnter(dragEvent(dt));
    state.onDragLeave(dragEvent(dt));
    expect(state.isOver.value).toBe(false);
  });
});

describe('useWindowDropGuard', () => {
  it('prevents the browser default for a file drag on the window while active', () => {
    const active = ref(true);
    const scope = effectScope();
    scope.run(() => useWindowDropGuard(active));

    const event = windowDragEvent('dragover', fakeDataTransfer(['Files']));
    window.dispatchEvent(event);

    expect(event.defaultPrevented).toBe(true);
    scope.stop();
  });

  it('leaves a drag without files alone', () => {
    const active = ref(true);
    const scope = effectScope();
    scope.run(() => useWindowDropGuard(active));

    const event = windowDragEvent('drop', fakeDataTransfer([]));
    window.dispatchEvent(event);

    expect(event.defaultPrevented).toBe(false);
    scope.stop();
  });

  it('registers no listener while inactive, and removes it once deactivated', async () => {
    const active = ref(false);
    const scope = effectScope();
    scope.run(() => useWindowDropGuard(active));

    const inactiveEvent = windowDragEvent('drop', fakeDataTransfer(['Files']));
    window.dispatchEvent(inactiveEvent);
    expect(inactiveEvent.defaultPrevented).toBe(false);

    active.value = true;
    await Promise.resolve();
    active.value = false;
    await Promise.resolve();

    const afterEvent = windowDragEvent('drop', fakeDataTransfer(['Files']));
    window.dispatchEvent(afterEvent);
    expect(afterEvent.defaultPrevented).toBe(false);

    scope.stop();
  });
});
