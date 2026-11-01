import { effectScope, nextTick, ref } from 'vue';
import { describe, expect, it, vi } from 'vitest';

import { makeTransfer } from '@/components/site/testHelpers';
import { usePageDrop, useDropState, useWindowDropGuard } from './fileDrop';

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
    event.stopPropagation = vi.fn();

    state.onDragOver(event);

    expect(preventDefault).toHaveBeenCalledTimes(1);
  });

  it('keeps a file drag from reaching a file field inside the target', () => {
    const state = useDropState();
    const event = dragEvent(fakeDataTransfer(['Files']));
    event.preventDefault = vi.fn();
    const stopPropagation = (event.stopPropagation = vi.fn());

    state.onDragOver(event);

    expect(stopPropagation).toHaveBeenCalledTimes(1);
  });

  it('does not intercept dragover for a drag without files', () => {
    const state = useDropState();
    const event = dragEvent(fakeDataTransfer([]));
    const preventDefault = (event.preventDefault = vi.fn());
    const stopPropagation = (event.stopPropagation = vi.fn());

    state.onDragOver(event);

    expect(preventDefault).not.toHaveBeenCalled();
    expect(stopPropagation).not.toHaveBeenCalled();
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

describe('usePageDrop', () => {
  function pageDrop(enabledAtStart = true) {
    const enabled = ref(enabledAtStart);
    const open = ref(false);
    const scope = effectScope();
    const drop = scope.run(() => usePageDrop(enabled, open))!;
    return { enabled, open, scope, drop };
  }

  function dropEvent(transfer: DataTransfer | null): DragEvent {
    const event = new Event('drop', { bubbles: true, cancelable: true });
    Object.defineProperty(event, 'dataTransfer', { value: transfer });
    return event as DragEvent;
  }

  it('opens the sheet with the read of what was dropped', async () => {
    const { open, scope, drop } = pageDrop();
    const site = new File(['pk'], 'site.zip');
    const event = dropEvent(makeTransfer({ 'site.zip': site }));

    drop.onPageDrop(event);

    expect(event.defaultPrevented).toBe(true);
    expect(open.value).toBe(true);
    await expect(drop.droppedRead.value).resolves.toEqual({ kind: 'file', file: site });
    scope.stop();
  });

  it('opens the sheet for a refusal too: that is where it gets explained', async () => {
    const { open, scope, drop } = pageDrop();

    drop.onPageDrop(dropEvent(makeTransfer({ 'foto.png': new File(['x'], 'foto.png') })));

    expect(open.value).toBe(true);
    await expect(drop.droppedRead.value).resolves.toMatchObject({ kind: 'refusal' });
    scope.stop();
  });

  it('calls webkitGetAsEntry during the event, not after it', () => {
    const { scope, drop } = pageDrop();
    const transfer = makeTransfer({ public: { 'index.html': new File(['x'], 'index.html') } });
    const asked = vi.fn(() => null);
    Object.defineProperty(transfer, 'items', { value: [{ webkitGetAsEntry: asked }] });

    drop.onPageDrop(dropEvent(transfer));

    expect(asked).toHaveBeenCalledOnce();
    scope.stop();
  });

  it('leaves a drop without files alone, such as dragged text', () => {
    const { open, scope, drop } = pageDrop();

    drop.onPageDrop(dropEvent(makeTransfer({})));
    drop.onPageDrop(dropEvent(null));

    expect(open.value).toBe(false);
    expect(drop.droppedRead.value).toBeNull();
    scope.stop();
  });

  it('does nothing while publishing here is not allowed', () => {
    const { open, scope, drop } = pageDrop(false);
    const event = dropEvent(makeTransfer({ 'site.zip': new File(['pk'], 'site.zip') }));

    drop.onPageDrop(event);

    expect(event.defaultPrevented).toBe(false);
    expect(open.value).toBe(false);
    expect(drop.droppedRead.value).toBeNull();
    scope.stop();
  });

  it('forgets the read when the sheet closes, so the button does not carry it along', async () => {
    const { open, scope, drop } = pageDrop();
    drop.onPageDrop(dropEvent(makeTransfer({ 'site.zip': new File(['pk'], 'site.zip') })));
    await nextTick();
    expect(drop.droppedRead.value).not.toBeNull();

    open.value = false;
    await nextTick();

    expect(drop.droppedRead.value).toBeNull();
    scope.stop();
  });
});
