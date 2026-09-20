/**
 * The suggestions under the "iemand toevoegen" field of a group or a site:
 * type a name, get the people whose name or e-mail matches.
 *
 * Suggestions are help, never the route itself. Whatever goes wrong here, the
 * field stays a field you can type an address into, so every failure ends in
 * an empty list rather than in a message.
 */
import { onScopeDispose, ref, type Ref } from 'vue';

import type { MemberSuggestion } from '@/api/types';

/** Fewest characters that go to the server; the backend refuses less with 422. */
export const SEARCH_MIN_LENGTH = 2;

/**
 * Loose shape check for "iemand toevoegen": not a validator for what the
 * backend eventually accepts as an e-mail address, just enough to tell a
 * typed address apart from a typed name before submitting. A name picked
 * from the suggestions never reaches this check: the combo box then holds
 * the suggestion's e-mail address, which always matches.
 */
export function looksLikeEmail(value: string): boolean {
  return /^\S+@\S+\.\S+$/.test(value.trim());
}

/** Milliseconds of quiet typing before the query goes out. */
export const SEARCH_DEBOUNCE_MS = 250;

export interface MemberSearch {
  suggestions: Ref<MemberSuggestion[]>;
  /** Look up what is typed now; debounced, and silent below the minimum. */
  query: (text: string) => void;
  /** Drop the list and whatever is still on its way, after adding someone. */
  clear: () => void;
}

export function useMemberSearch(
  search: (query: string) => Promise<MemberSuggestion[]>,
): MemberSearch {
  const suggestions = ref<MemberSuggestion[]>([]);
  let timer: ReturnType<typeof setTimeout> | undefined;
  // Only the newest query may write the list: an earlier answer arriving late
  // would otherwise put the suggestions for a query that is no longer typed
  // back on screen.
  let newest = 0;

  function clear(): void {
    clearTimeout(timer);
    timer = undefined;
    newest += 1;
    suggestions.value = [];
  }

  function query(text: string): void {
    const trimmed = text.trim();
    clearTimeout(timer);
    timer = undefined;
    if (trimmed.length < SEARCH_MIN_LENGTH) {
      clear();
      return;
    }
    timer = setTimeout(() => {
      timer = undefined;
      newest += 1;
      const ticket = newest;
      void search(trimmed)
        .then((rows) => {
          if (ticket === newest) suggestions.value = rows;
        })
        .catch(() => {
          if (ticket === newest) suggestions.value = [];
        });
    }, SEARCH_DEBOUNCE_MS);
  }

  onScopeDispose(() => clearTimeout(timer));

  return { suggestions, query, clear };
}
