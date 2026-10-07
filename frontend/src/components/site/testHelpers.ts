/**
 * Shared helpers for the tab tests: draining promises after async loads, and
 * events the way the nldd components fire them in the browser.
 */
import { flushPromises } from '@vue/test-utils';
import { vi } from 'vitest';

/** Drains the microtask queue a few times (nested awaits inside load()). */
export async function untilIdle(): Promise<void> {
  for (let i = 0; i < 6; i += 1) {
    await flushPromises();
  }
}

/**
 * Fires a custom event the way an nldd input component does (value in
 * `event.detail`), because in jsdom the nldd elements are inert.
 */
export function fireDetailEvent(
  element: Element,
  name: string,
  detail: Record<string, unknown>,
): void {
  element.dispatchEvent(new CustomEvent(name, { detail, bubbles: false }));
}

/**
 * Drains the macrotask queue as well. FileReader and CompressionStream do not
 * report back in a microtask, so `untilIdle` alone is too early for anything that
 * packs a drop.
 */
export async function untilQuiet(rounds = 20): Promise<void> {
  for (let i = 0; i < rounds; i += 1) {
    await new Promise((ready) => setTimeout(ready, 0));
    await flushPromises();
  }
}

/** A tree as it gets dropped: a folder is an object, a file is a File. */
export interface DropTree {
  [name: string]: DropTree | File;
}

function makeEntry(name: string, content: DropTree | File): FileSystemEntry {
  if (content instanceof File) {
    return {
      name: name,
      isFile: true,
      isDirectory: false,
      file: (resolve: (file: File) => void) => resolve(content),
    } as unknown as FileSystemEntry;
  }
  const children = Object.entries(content).map(([childName, child]) => makeEntry(childName, child));
  return {
    name: name,
    isFile: false,
    isDirectory: true,
    createReader: () => {
      // Like a real reader: one batch per call, an empty one to close.
      let position = 0;
      return {
        readEntries: (resolve: (batch: FileSystemEntry[]) => void) => {
          const batch = children.slice(position, position + 2);
          position += batch.length;
          resolve(batch);
        },
      };
    },
  } as unknown as FileSystemEntry;
}

/**
 * A `DataTransfer` as a drop brings it along. jsdom has no `DataTransfer` and no
 * `DragEvent`, so this is the smallest surface the code uses. Without `entries`
 * there is no `webkitGetAsEntry`, as in a browser that cannot do it: then there
 * are only individual files.
 */
export function makeTransfer(tree: DropTree, options: { entries?: boolean } = {}): DataTransfer {
  const entries = Object.entries(tree).map(([name, content]) => makeEntry(name, content));
  const files = Object.values(tree).filter((content): content is File => content instanceof File);
  const items =
    options.entries === false
      ? entries.map(() => ({}))
      : entries.map((entry) => ({ webkitGetAsEntry: () => entry }));
  return { items, files: files } as unknown as DataTransfer;
}

/** Fires a drag event carrying a `dataTransfer` of its own. */
export function fireDrop(element: Element, name: string, transfer?: DataTransfer): Event {
  const event = new Event(name, { bubbles: true, cancelable: true });
  Object.defineProperty(event, 'dataTransfer', { value: transfer ?? null });
  element.dispatchEvent(event);
  return event;
}

/** A problem+json server error as a fetch stub, for the error states. */
export function serverErrorFetch(): typeof fetch {
  return () =>
    Promise.resolve(
      new Response(
        JSON.stringify({ type: 'about:blank', title: 'Serverfout', status: 500 }),
        { status: 500, headers: { 'content-type': 'application/problem+json' } },
      ),
    );
}

/**
 * A refusal with the fields of your choice (`title`, `detail`, `code`) as a
 * fetch stub: for a test that needs the server to say a particular thing.
 */
export function problemFetch(status: number, body: Record<string, unknown>): typeof fetch {
  return () =>
    Promise.resolve(
      new Response(JSON.stringify({ type: 'about:blank', status, ...body }), {
        status,
        headers: { 'content-type': 'application/problem+json' },
      }),
    );
}

/**
 * Takes over `requestAnimationFrame`: a frame comes when `run` says so, not
 * after sixteen milliseconds of real time.
 */
export function stubFrames(): { run: () => void } {
  const queue: FrameRequestCallback[] = [];
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => queue.push(callback));
  return {
    run: () => {
      for (const callback of queue.splice(0)) callback(0);
    },
  };
}

/**
 * A tab bar without a browser, as jsdom has no layout: the tabs side by side,
 * each `tabWidth` wide, in a scroll box that shows `barWidth` of them. Where a
 * tab sits on screen follows the scroll position of the box.
 */
export function fakeTabBarLayout(
  scroller: HTMLElement,
  { barWidth, tabWidth }: { barWidth: number; tabWidth: number },
): void {
  const rect = (left: number, width: number): DOMRect =>
    ({ left, right: left + width, width, x: left, top: 0, bottom: 0, y: 0, height: 0 }) as DOMRect;
  Object.defineProperty(scroller, 'clientWidth', { value: barWidth, configurable: true });
  vi.spyOn(scroller, 'getBoundingClientRect').mockImplementation(() => rect(0, barWidth));
  scroller.querySelectorAll('nldd-tab-bar-item').forEach((tab, index) => {
    vi.spyOn(tab, 'getBoundingClientRect').mockImplementation(() =>
      rect(index * tabWidth - scroller.scrollLeft, tabWidth),
    );
  });
}
