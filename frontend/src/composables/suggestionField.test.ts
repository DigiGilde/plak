import { afterEach, describe, expect, it, vi } from 'vitest';
import { effectScope, nextTick, ref } from 'vue';

import {
  keepEverySuggestion,
  openSuggestionMenu,
  useMenuEmptyState,
  useStartingList,
} from './suggestionField';

/** jsdom has no Popover API, so the type says what the element really has. */
type Menu = HTMLElement & { showPopover?: () => void; filter?: (query: string) => void };

let field: HTMLElement | null = null;
let stop: (() => void) | null = null;

/**
 * A stand-in for the combo box: jsdom knows neither the custom elements nor
 * the Popover API, so the menu carries a `showPopover` of its own.
 */
function comboBox(withMenu = true): { box: HTMLElement; opened: () => number } {
  const box = document.createElement('div');
  box.tabIndex = 0;
  const menu = document.createElement('nldd-menu') as Menu;
  const open = vi.fn();
  if (withMenu) {
    menu.showPopover = open;
    box.append(menu);
  }
  document.body.append(box);
  box.focus();
  field = box;
  return { box, opened: () => open.mock.calls.length };
}

afterEach(() => {
  stop?.();
  stop = null;
  field?.remove();
  field = null;
});

describe('keepEverySuggestion', () => {
  it('keeps every row, because the server already left the rest out', () => {
    expect(keepEverySuggestion()).toBe(true);
  });
});

describe('openSuggestionMenu', () => {
  it('opens the slotted menu of the field it is handed', () => {
    const { box, opened } = comboBox();

    openSuggestionMenu(box);

    expect(opened()).toBe(1);
  });

  it('shrugs off a field that is not there, or holds no menu that opens', () => {
    openSuggestionMenu(null);
    const { box, opened } = comboBox(false);

    openSuggestionMenu(box);

    expect(opened()).toBe(0);
  });
});

describe('useMenuEmptyState', () => {
  /** A combo box whose menu records the queries it was recounted with. */
  function withEmptyState(withMenu = true) {
    const box = document.createElement('div');
    const filter = vi.fn();
    if (withMenu) {
      const menu = document.createElement('nldd-menu') as Menu;
      menu.filter = filter;
      box.append(menu);
    }
    document.body.append(box);
    field = box;
    const rows = ref<string[]>([]);
    const scope = effectScope();
    scope.run(() => useMenuEmptyState(ref(box), () => rows.value));
    stop = () => scope.stop();
    return { rows, filter };
  }

  it('recounts on an empty query, which narrows nothing away', async () => {
    const { rows, filter } = withEmptyState();

    rows.value = ['tim@example.org'];
    await nextTick();

    expect(filter).toHaveBeenCalledWith('');
  });

  it('recounts an answer of the same size as the one before it', async () => {
    const { rows, filter } = withEmptyState();

    rows.value = ['tim@example.org'];
    await nextTick();
    rows.value = ['wim@example.org'];
    await nextTick();

    expect(filter).toHaveBeenCalledTimes(2);
  });

  it('shrugs off a field that holds no menu to recount', async () => {
    const { rows, filter } = withEmptyState(false);

    rows.value = ['tim@example.org'];
    await nextTick();

    expect(filter).not.toHaveBeenCalled();
  });
});

describe('useStartingList', () => {
  /** A combo box with the composable attached and its scope kept alive. */
  function withStartingList(hasRows = () => true) {
    const { box, opened } = comboBox();
    const scope = effectScope();
    scope.run(() => useStartingList(ref(box), hasRows));
    stop = () => scope.stop();
    return { box, opened };
  }

  /** What a click inside the shadow DOM of the picker button looks like. */
  function clickPicker(box: HTMLElement): void {
    const picker = document.createElement('div');
    picker.className = 'combo-box__picker-button';
    box.append(picker);
    picker.dispatchEvent(new MouseEvent('click', { bubbles: true, composed: true }));
  }

  it('opens the list for someone arriving on the keyboard', () => {
    const { box, opened } = withStartingList();

    box.dispatchEvent(new FocusEvent('focusin'));

    expect(opened()).toBe(1);
  });

  it('waits for the click of a mouse, whose pointerup dismisses an open menu', () => {
    const { box, opened } = withStartingList();

    box.dispatchEvent(new Event('pointerdown'));
    box.dispatchEvent(new FocusEvent('focusin'));
    expect(opened()).toBe(0);

    box.dispatchEvent(new MouseEvent('click'));
    expect(opened()).toBe(1);
  });

  it('keeps the keyboard working after a click that never came', () => {
    const { box, opened } = withStartingList();

    box.dispatchEvent(new Event('pointerdown'));
    box.dispatchEvent(new FocusEvent('focusout'));
    box.dispatchEvent(new FocusEvent('focusin'));

    expect(opened()).toBe(1);
  });

  it('leaves the menu closed after a row in it was picked', () => {
    const { box, opened } = withStartingList();
    const row = box.querySelector('nldd-menu')!.appendChild(document.createElement('nldd-menu-item'));

    // A pick: the component closes the menu itself and the click bubbles on to
    // the field, which used to read it as an arrival and open it again.
    box.dispatchEvent(new Event('pointerdown'));
    row.dispatchEvent(new MouseEvent('click', { bubbles: true, composed: true }));

    expect(opened()).toBe(0);
  });

  it('leaves the menu closed when the picked row hands the focus back', () => {
    const { box, opened } = withStartingList();
    const row = box.querySelector('nldd-menu')!.appendChild(document.createElement('nldd-menu-item'));

    box.dispatchEvent(new FocusEvent('focusin', { relatedTarget: row }));

    expect(opened()).toBe(0);
  });

  it("leaves the field's own picker button to do its own toggling", () => {
    const { box, opened } = withStartingList();

    clickPicker(box);

    expect(opened()).toBe(0);
  });

  it('opens nothing while there is nothing to show', () => {
    const { box, opened } = withStartingList(() => false);

    box.dispatchEvent(new MouseEvent('click'));

    expect(opened()).toBe(0);
  });

  it('lets go of the field once the scope is gone', () => {
    const { box, opened } = withStartingList();

    stop?.();
    stop = null;
    box.dispatchEvent(new MouseEvent('click'));

    expect(opened()).toBe(0);
  });
});
