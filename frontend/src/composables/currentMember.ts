/**
 * Shared session state: who is logged in (and whether anyone is). One
 * module-level cache, so that the router guard and the UI (user menu,
 * platform-admin checks) do not each fire their own `me()` call.
 */
import { ref } from 'vue';

import { ApiError } from '../api/client';
import { me } from '../api/plak';
import type { Member } from '../api/types';
import { applyMemberLanguage } from '../i18n';

/**
 * The three states a visitor of the admin host can be in, as `/me` reports them
 * (auth/members.py): 401 without a session, 403 for a deactivated member, and
 * 200 only once the admin API is open.
 */
export type SessionState = 'no-session' | 'awaiting-activation' | 'active';

export interface Session {
  state: SessionState;
  member: Member | null;
  /** Explanation from the 403 response; empty in the other two states. */
  reason: string;
}

const member = ref<Member | null>(null);
const state = ref<SessionState>('no-session');
const reason = ref('');
const loaded = ref(false);
let fetchPromise: Promise<Session> | null = null;

async function fetch(): Promise<Session> {
  try {
    const profile = await me();
    member.value = profile;
    state.value = 'active';
    reason.value = '';
    // The account is what decides the interface language, so this is the
    // moment the browser's guess is either confirmed or overruled.
    applyMemberLanguage(profile.language);
  } catch (error) {
    if (!(error instanceof ApiError) || (error.problem.status !== 401 && error.problem.status !== 403)) {
      throw error;
    }
    const waiting = error.problem.status === 403;
    member.value = null;
    state.value = waiting ? 'awaiting-activation' : 'no-session';
    reason.value = waiting ? (error.problem.detail ?? '') : '';
  } finally {
    loaded.value = true;
  }
  return {
    state: state.value,
    member: member.value,
    reason: reason.value,
  };
}

/**
 * Returns the session, from cache when there already is one. `refresh: true`
 * bypasses the cache (e.g. after logging in or out, or to check whether a
 * waiting account has been released in the meantime).
 */
export function fetchSession(refresh = false): Promise<Session> {
  if (refresh) {
    fetchPromise = null;
  }
  if (!fetchPromise) {
    fetchPromise = fetch();
  }
  return fetchPromise;
}

/** The logged-in member, or null while the admin API is closed (no session, or not active). */
export function fetchCurrentMember(refresh = false): Promise<Member | null> {
  return fetchSession(refresh).then((session) => session.member);
}

export function currentMemberState(): { member: typeof member; loaded: typeof loaded } {
  return { member, loaded };
}

export function isPlatformAdmin(subject: Member | null): boolean {
  return subject !== null && subject.platformRole === 'admin' && subject.status === 'active';
}

/** For tests only: forces a fresh fetch on the next call. */
export function _resetCurrentMemberCache(): void {
  fetchPromise = null;
  member.value = null;
  state.value = 'no-session';
  reason.value = '';
  loaded.value = false;
}
