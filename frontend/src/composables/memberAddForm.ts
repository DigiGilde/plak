import { computed, ref } from 'vue';

import type { MemberSuggestion, Role } from '@/api/types';
import { SEARCH_MIN_LENGTH, useMemberSearch } from '@/composables/memberSearch';
import { useNotices, type Notice } from '@/composables/notices';
import { useMenuEmptyState } from '@/composables/suggestionField';
import { t, type MessageKey } from '@/i18n';

export interface MemberAddFormOptions<Member> {
  search: (query: string) => Promise<MemberSuggestion[]>;
  /** What the open menu says while it has nothing to show, one text per situation. */
  emptyText: { tooShort: MessageKey; searching: MessageKey; none: MessageKey };
  /** The notification title when adding fails; gets `{name}`. */
  addFailed: MessageKey;
  add: (identifier: string, role: Role) => Promise<Member>;
  onAdded: (member: Member) => void;
  /** Whether the address already has a row, so a provisional one would clash with it. */
  isListed: (identifier: string) => boolean;
  /** Runs a row ahead of the answer, and takes it away again afterwards. */
  addProvisional: (identifier: string, role: Role) => void;
  dropProvisional: (identifier: string) => void;
  /**
   * The list shown before anything is typed, instead of the search answers;
   * a picked address can be named from it too.
   */
  starting?: () => MemberSuggestion[];
}

/**
 * The add-a-member form of the group and the site page: the combo box that
 * searches, the role next to it, and the optimistic submit. What differs
 * between the two (the rows, the wording, the starting list) comes in through
 * the options.
 */
export function useMemberAddForm<Member>(options: MemberAddFormOptions<Member>) {
  /** What gets submitted: an e-mail address, typed or picked from the list. */
  const newIdentifier = ref('');
  /**
   * What the field shows, which is the name once a suggestion is picked. Bound
   * alongside the value so the combo box never derives a label of its own: it
   * would rewrite what someone is still typing the moment it happens to match
   * an address in the list.
   */
  const newLabel = ref('');
  /** Lezer by default: starting narrow makes promoting a deliberate step. */
  const newRole = ref<Role>('reader');
  const emptyField = ref(false);
  const identifierField = ref<HTMLElement | null>(null);

  const {
    suggestions,
    searching,
    query: searchFor,
    clear: clearSuggestions,
  } = useMemberSearch(options.search);

  /**
   * On what is on screen, not on what is picked: a pick leaves the field
   * holding a name, and the typing that led to it is what the answers belong to.
   */
  const shownSuggestions = computed(() =>
    options.starting && newLabel.value.trim() === '' ? options.starting() : suggestions.value,
  );

  /** Whether what stands in the field is too short to search on. */
  const tooShort = computed(() => newLabel.value.trim().length < SEARCH_MIN_LENGTH);

  /**
   * The menu opens on the first keystroke, long before an answer is in, so its
   * empty state has to tell three situations apart rather than call all three
   * "niemand gevonden".
   */
  const emptyText = computed(() => {
    if (tooShort.value) return t(options.emptyText.tooShort);
    return t(searching.value ? options.emptyText.searching : options.emptyText.none);
  });

  // The rows arrive after the menu has already decided that it is empty.
  useMenuEmptyState(identifierField, () => shownSuggestions.value);

  const { notices, notify, dismissNotice } = useNotices();

  function onIdentifierInput(event: CustomEvent<{ value?: string }>): void {
    // The component reports what stands in the input as `detail.value`. The
    // native input event of its own inner field is composed and bubbles out
    // here as well, carrying no detail; that one is the same keystroke twice.
    const typed = event.detail?.value;
    if (typeof typed !== 'string') return;
    // Only a pick is an identifier: typing on takes back the one before it.
    newIdentifier.value = '';
    newLabel.value = typed;
    emptyField.value = false;
    searchFor(typed);
  }

  /**
   * The name behind a picked address, looked up in every list rather than in
   * the one on screen: filling the field is itself what can swap one list for
   * another, so by now the list it came out of may be the other one.
   */
  function nameOf(identifier: string): string {
    const lists = [...suggestions.value, ...(options.starting?.() ?? [])];
    return lists.find((person) => person.identifier === identifier)?.name ?? '';
  }

  /**
   * A suggestion carries the name as its label and the address as its value, so
   * picking one submits the address while the field keeps showing the name.
   */
  function onIdentifierChange(event: CustomEvent<{ value?: string }>): void {
    // Same double event as on input: the inner field's own change bubbles out
    // here too, without a detail, and would wipe the pick it follows.
    const identifier = event.detail?.value;
    if (typeof identifier !== 'string') return;
    newIdentifier.value = identifier;
    newLabel.value = nameOf(identifier) || identifier;
    emptyField.value = false;
  }

  function reopen(notice: Notice): void {
    newIdentifier.value = notice.retry;
    newLabel.value = notice.retry;
    emptyField.value = false;
    dismissNotice(notice.id);
  }

  async function onAdd(): Promise<void> {
    const identifier = newIdentifier.value.trim();
    emptyField.value = identifier === '';
    if (emptyField.value) return;
    // The field only submits what was picked, so the label beside the address
    // is the name of whoever was picked. Read before the field is emptied.
    /* v8 ignore start -- newLabel is always set together with newIdentifier (by
     * onIdentifierChange or reopen()), to the picked name or the identifier
     * itself, so it is never blank once emptyField has let this line run. */
    const name = newLabel.value.trim() || identifier;
    /* v8 ignore stop */
    const role = newRole.value;

    newIdentifier.value = '';
    newLabel.value = '';
    clearSuggestions();
    // Someone already on the list gets no provisional row: a second row would
    // get the same :key, and the server's answer says what is the matter.
    const newRow = !options.isListed(identifier);
    if (newRow) options.addProvisional(identifier, role);
    try {
      options.onAdded(await options.add(identifier, role));
    } catch (error) {
      notify(t(options.addFailed, { name }), error, identifier);
    } finally {
      if (newRow) options.dropProvisional(identifier);
    }
  }

  return {
    newIdentifier,
    newLabel,
    newRole,
    emptyField,
    identifierField,
    suggestions,
    shownSuggestions,
    tooShort,
    emptyText,
    notices,
    notify,
    dismissNotice,
    onIdentifierInput,
    onIdentifierChange,
    reopen,
    onAdd,
  };
}
