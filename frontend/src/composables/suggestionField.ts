/**
 * What the "iemand toevoegen" combo box needs beyond what `nldd-combo-box`
 * does by itself, because its list is answered by the server rather than
 * slotted up front.
 *
 * **The menu filters twice.** It re-filters a list the server already
 * narrowed, against the text exactly as typed rather than the trimmed term the
 * query went out with, so a trailing space hides the very row that was asked
 * for. `keepEverySuggestion` hands that job back to the server, which is the
 * only side that knows what it matched on.
 *
 * **Arriving in the field opens nothing.** The component opens on a keystroke
 * and on its own picker button, and there is no third way in. A list that is
 * there before anything is typed has to be opened by the consumer, which is
 * what `useStartingList` does.
 *
 * **The empty state is only ever recounted while filtering.** A list that Vue
 * puts in afterwards leaves "niemand gevonden" standing under the rows it
 * found, which is what `useMenuEmptyState` recounts.
 */
import { onScopeDispose, watch, type Ref } from 'vue';

/** What `nldd-menu` offers here; the element's own type is not exported. */
type SuggestionMenu = HTMLElement & { filter: (query: string) => void };

/**
 * Between the facts of a suggestion row. They share one text cell, so this
 * string is also the menu item's accessible name: a comma, which every screen
 * reader pauses on, rather than the middle dot the NLDD guidelines name for a
 * visual separator, which is announced or swallowed per the reader's own
 * punctuation setting.
 */
export const NOTE_SEPARATOR = ', ';

/** The menu's `filter-fn`: what is in the list is what the server returned. */
export function keepEverySuggestion(): boolean {
  return true;
}

/** Open the menu slotted into `comboBox`, as far as there is one that opens. */
export function openSuggestionMenu(comboBox: HTMLElement | null): void {
  const menu = comboBox?.querySelector('nldd-menu');
  // Popover support is the component's own precondition for opening at all;
  // where it is missing, nldd-combo-box warns and stays closed either way.
  if (!(menu instanceof HTMLElement) || typeof menu.showPopover !== 'function') return;
  menu.showPopover();
}

function suggestionMenu(comboBox: HTMLElement | null): SuggestionMenu | null {
  const menu = comboBox?.querySelector('nldd-menu');
  return menu instanceof HTMLElement && typeof (menu as SuggestionMenu).filter === 'function'
    ? (menu as SuggestionMenu)
    : null;
}

/**
 * Recount the menu's empty state whenever the list of rows changes.
 *
 * `nldd-menu` works out whether it is empty inside `filter()` and nowhere
 * else, so rows handed to it afterwards do not clear the empty state that was
 * true while it was still waiting. `filter('')` is that recount without a
 * filtering: an empty query shows every item, which is what
 * `keepEverySuggestion` says anyway, so nothing can be narrowed away here.
 *
 * The empty query also clears the `query` the combo box puts on the rows for
 * its predictive bolding, which is what this list wants. That bolding marks
 * everything but the typed text, on the assumption that the typed text is a
 * prefix; the server matches anywhere in a name or an address, so on "de" it
 * would bold all of "Tim " and " Vries" around the two letters in the middle.
 */
export function useMenuEmptyState(
  comboBox: Ref<HTMLElement | null>,
  rows: () => readonly unknown[],
): void {
  // On the list itself rather than on its length: two answers of the same
  // size leave the count alone while the rows underneath them are replaced.
  watch([comboBox, rows], ([box]) => suggestionMenu(box)?.filter(''), {
    // After the rows are in the DOM: the menu counts the elements themselves.
    flush: 'post',
  });
}

/**
 * The class of the wrapper around nldd-combo-box's own picker button, inside
 * its shadow DOM. Reading it is an escape hatch: there is no event and no
 * attribute that tells the two ways of clicking the field apart.
 */
const PICKER_BUTTON = 'combo-box__picker-button';

function hitsPickerButton(event: Event): boolean {
  return event
    .composedPath()
    .some((node) => node instanceof HTMLElement && node.classList.contains(PICKER_BUTTON));
}

function insideMenu(node: EventTarget | null | undefined): boolean {
  return node instanceof Element && node.closest('nldd-menu') !== null;
}

/**
 * Whether this event comes out of the open menu rather than from the field.
 *
 * Picking a row is a click inside the menu, and the menu is slotted into the
 * combo box, so that click bubbles on to the field. The focus the closing menu
 * hands back arrives as a `focusin` whose `relatedTarget` is the row that was
 * picked. Both read as an arrival in the field and would reopen the list the
 * pick just closed.
 */
function fromMenu(event: Event): boolean {
  return (
    event.composedPath().some(insideMenu) || insideMenu((event as FocusEvent).relatedTarget)
  );
}

/**
 * Open `comboBox`'s menu when someone arrives in the field, as long as
 * `hasRows` says there is something to show.
 *
 * Two ways in, and neither event covers both. A mouse click focuses the field
 * at pointerdown, and a popover opened there is light-dismissed again at
 * pointerup, so the mouse is served on `click` and the pointer is tracked to
 * keep `focusin` from opening a menu that is about to be dismissed. The picker
 * button is left alone: it toggles the menu itself, and reopening what it just
 * closed would leave it unable to close anything. So is everything that comes
 * out of the menu, which is a pick and not an arrival.
 */
export function useStartingList(comboBox: Ref<HTMLElement | null>, hasRows: () => boolean): void {
  let fromPointer = false;

  function open(): void {
    if (hasRows()) openSuggestionMenu(comboBox.value);
  }

  const handlers: Record<string, (event: Event) => void> = {
    pointerdown: () => {
      fromPointer = true;
    },
    focusin: (event) => {
      if (!fromPointer && !fromMenu(event)) open();
    },
    focusout: () => {
      fromPointer = false;
    },
    click: (event) => {
      fromPointer = false;
      if (!hitsPickerButton(event) && !fromMenu(event)) open();
    },
  };

  function listen(box: HTMLElement | null, add: boolean): void {
    for (const [name, handler] of Object.entries(handlers)) {
      if (add) box?.addEventListener(name, handler);
      else box?.removeEventListener(name, handler);
    }
  }

  watch(
    comboBox,
    (box, previous) => {
      listen(previous ?? null, false);
      listen(box, true);
    },
    { immediate: true },
  );
  onScopeDispose(() => listen(comboBox.value, false));
}
