import { describe, expect, it, vi } from 'vitest';

import { useConfirm } from './confirm';

describe('useConfirm', () => {
  it('is closed until something is asked, and closes again on cancel', () => {
    const { open, target, ask, cancel } = useConfirm<string>(() => Promise.resolve());
    expect(open.value).toBe(false);

    ask('ada');
    expect(open.value).toBe(true);
    expect(target.value).toBe('ada');

    cancel();
    expect(open.value).toBe(false);
    expect(target.value).toBeNull();
  });

  it('runs the action on the item, busy meanwhile, and closes afterwards', async () => {
    let finish!: () => void;
    const action = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          finish = resolve;
        }),
    );
    const { open, busy, ask, confirm } = useConfirm<string>(action);
    ask('ada');

    const running = confirm();
    expect(busy.value).toBe(true);
    expect(open.value).toBe(true);
    finish();
    await running;

    expect(action).toHaveBeenCalledWith('ada');
    expect(busy.value).toBe(false);
    expect(open.value).toBe(false);
  });

  it('closes and stops being busy even when the action throws', async () => {
    const { open, busy, ask, confirm } = useConfirm<true>(() => Promise.reject(new Error('mis')));
    ask(true);

    await expect(confirm()).rejects.toThrow('mis');

    expect(busy.value).toBe(false);
    expect(open.value).toBe(false);
  });
});
