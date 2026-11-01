/**
 * Shared helpers for the tab tests: draining promises after async loads, and
 * events the way the nldd components fire them in the browser.
 */
import { flushPromises } from '@vue/test-utils';
import { computed, ref } from 'vue';

import * as plak from '@/api/plak';
import { SITE_GROUP, type SiteGroup } from '@/composables/siteGroup';
import { groupDetailOf, type MockBackend } from '../../../tests/mock-api';

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
  const types = entries.length > 0 ? ['Files'] : [];
  return { types, items, files: files } as unknown as DataTransfer;
}

/** Fires a drag event carrying a `dataTransfer` of its own. */
export function fireDrop(element: Element, name: string, transfer?: DataTransfer): Event {
  const event = new Event(name, { bubbles: true, cancelable: true });
  Object.defineProperty(event, 'dataTransfer', { value: transfer ?? null });
  element.dispatchEvent(event);
  return event;
}

/**
 * A drop the way a browser really delivers it: the items and files of a
 * DataTransfer only answer during the event itself, so a read that starts any
 * later finds nothing.
 */
export function fireDropThatExpires(element: Element, tree: DropTree): DataTransfer {
  const transfer = makeTransfer(tree);
  const { items, files } = transfer;
  let live = true;
  Object.defineProperty(transfer, 'items', { get: () => (live ? items : []) });
  Object.defineProperty(transfer, 'files', { get: () => (live ? files : []) });
  fireDrop(element, 'drop', transfer);
  live = false;
  return transfer;
}

/**
 * Enough of the `DataTransfer` constructor for handing a file to a field, for
 * `vi.stubGlobal`. Like a browser, it hands back a File of its own, with the same
 * name and size: code that compares by identity is caught out here too.
 */
export class FakeDataTransfer {
  private held: File[] = [];
  items = { add: (file: File) => this.held.push(new File([file], file.name, { type: file.type })) };
  get files(): File[] {
    return this.held;
  }
}

/**
 * The inside of a real nldd-file-field, which jsdom never gets to render: an
 * input in the shadow root that takes a FileList, and answers a change on it with
 * a change event of its own on the field, synchronously.
 */
export function giveFieldAnInput(field: Element): HTMLInputElement {
  const input = document.createElement('input');
  input.type = 'file';
  let held: File[] = [];
  Object.defineProperty(input, 'files', {
    get: () => held,
    set: (files: File[]) => {
      held = Array.from(files);
    },
  });
  Object.defineProperty(input, 'value', {
    set: () => {
      held = [];
    },
  });
  input.addEventListener('change', (event) => {
    event.stopPropagation();
    field.dispatchEvent(
      new CustomEvent('change', { detail: { files: held }, bubbles: true, composed: true }),
    );
  });
  field.attachShadow({ mode: 'open' }).append(input);
  return input;
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
 * What a site page hands its tabs, as `global.provide` for a tab mounted on
 * its own. Read from the backend at call time, so a test that changes the data
 * first gets the changed site.
 */
export function provideSiteGroup(
  backend: MockBackend,
  group = 'team-aurora',
  site = 'website',
): Record<symbol, SiteGroup> {
  const groupRow = backend.data.groups.find((g) => g.slug === group)!;
  const detail = ref(groupDetailOf(backend.data, groupRow));
  return {
    [SITE_GROUP as symbol]: {
      detail,
      site: computed(() => detail.value.sites.find((p) => p.slug === site)!),
    },
  };
}

/** The same, read through whatever `fetch` is stubbed in at that moment. */
export async function provideSiteGroupFromApi(
  group = 'team-aurora',
  site = 'website',
): Promise<Record<symbol, SiteGroup>> {
  const detail = ref(await plak.group(group));
  return {
    [SITE_GROUP as symbol]: {
      detail,
      site: computed(() => detail.value.sites.find((p) => p.slug === site)!),
    },
  };
}
