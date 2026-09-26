/**
 * The suggestions under the "iemand toevoegen" field of a group or a site:
 * type a name, get the people whose name or e-mail matches.
 *
 * Suggestions are the route: the field only submits what was picked from this
 * list, so an empty list is a dead end. That is why `searching` is part of the
 * answer. The field says which of the three it is (too little typed, an answer
 * on its way, nobody found) instead of calling all three "niemand gevonden".
 */
import { onScopeDispose, ref, type Ref } from 'vue';

import type { MemberSuggestion } from '@/api/types';

/** Fewest characters that go to the server; the backend refuses less with 422. */
export const SEARCH_MIN_LENGTH = 2;

/** Milliseconds of quiet typing before the query goes out. */
export const SEARCH_DEBOUNCE_MS = 250;

export interface MemberSearch {
  suggestions: Ref<MemberSuggestion[]>;
  /** Whether an answer is on its way, the debounce before it included. */
  searching: Ref<boolean>;
  /** Look up what is typed now; debounced, and silent below the minimum. */
  query: (text: string) => void;
  /** Drop the list and whatever is still on its way, after adding someone. */
  clear: () => void;
}

export function useMemberSearch(
  search: (query: string) => Promise<MemberSuggestion[]>,
): MemberSearch {
  const suggestions = ref<MemberSuggestion[]>([]);
  const searching = ref(false);
  let timer: ReturnType<typeof setTimeout> | undefined;
  // Only the newest query may write the list: an earlier answer arriving late
  // would otherwise put the suggestions for a query that is no longer typed
  // back on screen.
  let newest = 0;

  function clear(): void {
    clearTimeout(timer);
    timer = undefined;
    newest += 1;
    searching.value = false;
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
    searching.value = true;
    timer = setTimeout(() => {
      timer = undefined;
      newest += 1;
      const ticket = newest;
      void search(trimmed)
        .then((rows) => {
          if (ticket !== newest) return;
          suggestions.value = rows;
          searching.value = false;
        })
        .catch(() => {
          if (ticket !== newest) return;
          suggestions.value = [];
          searching.value = false;
        });
    }, SEARCH_DEBOUNCE_MS);
  }

  onScopeDispose(() => clearTimeout(timer));

  return { suggestions, searching, query, clear };
}
