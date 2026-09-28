import { effectScope } from 'vue';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { MemberSuggestion } from '@/api/types';

import { SEARCH_DEBOUNCE_MS, SEARCH_MIN_LENGTH, useMemberSearch } from './memberSearch';

function suggestion(email: string): MemberSuggestion {
  return { identifier: email, name: email, email, alreadyMember: false, groupRole: null };
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe('useMemberSearch', () => {
  it('does not search below the minimum length', () => {
    const search = vi.fn();
    const { query, searching, suggestions } = useMemberSearch(search);

    query('a'.repeat(SEARCH_MIN_LENGTH - 1));
    vi.advanceTimersByTime(SEARCH_DEBOUNCE_MS);

    expect(search).not.toHaveBeenCalled();
    expect(searching.value).toBe(false);
    expect(suggestions.value).toEqual([]);
  });

  it('debounces and reports the results of the newest query', async () => {
    const search = vi.fn().mockResolvedValue([suggestion('a@voorbeeld.nl')]);
    const { query, searching, suggestions } = useMemberSearch(search);

    query('an');
    expect(searching.value).toBe(true);
    expect(search).not.toHaveBeenCalled();

    vi.advanceTimersByTime(SEARCH_DEBOUNCE_MS);
    await vi.waitFor(() => expect(search).toHaveBeenCalledWith('an'));
    await Promise.resolve();
    await Promise.resolve();

    expect(suggestions.value).toEqual([suggestion('a@voorbeeld.nl')]);
    expect(searching.value).toBe(false);
  });

  it('ignores a stale answer that arrives after a newer query has already landed', async () => {
    let resolveFirst!: (rows: MemberSuggestion[]) => void;
    const search = vi
      .fn()
      .mockImplementationOnce(() => new Promise((resolve) => { resolveFirst = resolve; }))
      .mockResolvedValueOnce([suggestion('tweede@voorbeeld.nl')]);
    const { query, searching, suggestions } = useMemberSearch(search);

    query('eerste');
    vi.advanceTimersByTime(SEARCH_DEBOUNCE_MS);
    await vi.waitFor(() => expect(search).toHaveBeenCalledTimes(1));

    query('tweede');
    vi.advanceTimersByTime(SEARCH_DEBOUNCE_MS);
    await vi.waitFor(() => expect(search).toHaveBeenCalledTimes(2));
    await Promise.resolve();
    await Promise.resolve();

    // The second (newest) query's answer lands first.
    expect(suggestions.value).toEqual([suggestion('tweede@voorbeeld.nl')]);
    expect(searching.value).toBe(false);

    // The stale first answer must not overwrite it.
    resolveFirst([suggestion('eerste@voorbeeld.nl')]);
    await Promise.resolve();
    await Promise.resolve();

    expect(suggestions.value).toEqual([suggestion('tweede@voorbeeld.nl')]);
  });

  it('clears the suggestions and stops searching on a rejection', async () => {
    const search = vi.fn().mockRejectedValue(new Error('netwerkfout'));
    const { query, searching, suggestions } = useMemberSearch(search);

    query('foutmelding');
    vi.advanceTimersByTime(SEARCH_DEBOUNCE_MS);
    await vi.waitFor(() => expect(search).toHaveBeenCalledTimes(1));
    await Promise.resolve();
    await Promise.resolve();

    expect(suggestions.value).toEqual([]);
    expect(searching.value).toBe(false);
  });

  it('ignores a stale rejection that arrives after a newer query has already landed', async () => {
    let rejectFirst!: (error: Error) => void;
    const search = vi
      .fn()
      .mockImplementationOnce(() => new Promise((_resolve, reject) => { rejectFirst = reject; }))
      .mockResolvedValueOnce([suggestion('tweede@voorbeeld.nl')]);
    const { query, suggestions } = useMemberSearch(search);

    query('eerste');
    vi.advanceTimersByTime(SEARCH_DEBOUNCE_MS);
    await vi.waitFor(() => expect(search).toHaveBeenCalledTimes(1));

    query('tweede');
    vi.advanceTimersByTime(SEARCH_DEBOUNCE_MS);
    await vi.waitFor(() => expect(search).toHaveBeenCalledTimes(2));
    await Promise.resolve();
    await Promise.resolve();

    expect(suggestions.value).toEqual([suggestion('tweede@voorbeeld.nl')]);

    // The stale first rejection must not clear the newer suggestions.
    rejectFirst(new Error('te laat'));
    await Promise.resolve();
    await Promise.resolve();

    expect(suggestions.value).toEqual([suggestion('tweede@voorbeeld.nl')]);
  });

  it('drops a pending search and its timer on clear', () => {
    const search = vi.fn().mockResolvedValue([]);
    const { query, clear, searching, suggestions } = useMemberSearch(search);

    query('opnieuw');
    expect(searching.value).toBe(true);

    clear();

    expect(searching.value).toBe(false);
    expect(suggestions.value).toEqual([]);

    vi.advanceTimersByTime(SEARCH_DEBOUNCE_MS);
    expect(search).not.toHaveBeenCalled();
  });

  it('clears its pending timer when its owning scope is disposed', () => {
    const search = vi.fn().mockResolvedValue([]);
    const scope = effectScope();
    const { query } = scope.run(() => useMemberSearch(search))!;

    query('opnieuw');
    scope.stop();
    vi.advanceTimersByTime(SEARCH_DEBOUNCE_MS);

    expect(search).not.toHaveBeenCalled();
  });
});
