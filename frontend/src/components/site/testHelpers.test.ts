import { describe, expect, it } from 'vitest';

import { readDrop, type DropResult } from './packing';
import { FakeDataTransfer, fireDropThatExpires, giveFieldAnInput } from './testHelpers';

describe('fireDropThatExpires', () => {
  function site(): File {
    return new File(['pk'], 'site.zip');
  }

  it('answers with items and files during the event, and is empty afterwards', () => {
    const element = document.createElement('div');
    const dropped = site();
    let during: { items: number; files: File[] } | undefined;
    element.addEventListener('drop', (event) => {
      const transfer = (event as DragEvent).dataTransfer!;
      during = { items: transfer.items.length, files: Array.from(transfer.files) };
    });

    const transfer = fireDropThatExpires(element, { 'site.zip': dropped });

    expect(during).toEqual({ items: 1, files: [dropped] });
    expect(transfer.items).toHaveLength(0);
    expect(transfer.files).toHaveLength(0);
  });

  it('gives a read that starts during the event what was dropped', async () => {
    const element = document.createElement('div');
    const dropped = site();
    let read: Promise<DropResult> | undefined;
    element.addEventListener('drop', (event) => {
      read = readDrop((event as DragEvent).dataTransfer!);
    });

    fireDropThatExpires(element, { 'site.zip': dropped });

    await expect(read).resolves.toEqual({ kind: 'file', file: dropped });
  });

  it('leaves a read that starts after the event with nothing', async () => {
    const transfer = fireDropThatExpires(document.createElement('div'), { 'site.zip': site() });

    await expect(readDrop(transfer)).resolves.toMatchObject({ kind: 'refusal' });
  });
});

describe('giveFieldAnInput', () => {
  it('answers a file handed to its input with a change on the field, right away', () => {
    const field = document.createElement('div');
    const input = giveFieldAnInput(field);
    const seen: File[][] = [];
    field.addEventListener('change', (event) => {
      seen.push((event as CustomEvent<{ files: File[] }>).detail.files);
    });
    const data = new FakeDataTransfer();
    const original = new File(['pk'], 'site.zip');

    data.items.add(original);
    input.files = data.files as unknown as FileList;
    input.dispatchEvent(new Event('change', { bubbles: true, composed: true }));

    expect(seen).toHaveLength(1);
    // Its own File, as in a browser: the same name and size, another object.
    expect(seen[0]![0]).not.toBe(original);
    expect(seen[0]![0]!.name).toBe('site.zip');
    expect(seen[0]![0]!.size).toBe(original.size);
  });

  it('holds nothing any more once its value is emptied', () => {
    const input = giveFieldAnInput(document.createElement('div'));
    input.files = [new File(['pk'], 'site.zip')] as unknown as FileList;

    input.value = '';

    expect(input.files).toHaveLength(0);
  });
});
