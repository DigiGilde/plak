import { describe, expect, it } from 'vitest';

import type { Me } from '@/api/types';

import { isGroupAdmin, mayCreateSiteIn } from './roles';

function me(groupRoles: Me['groupRoles']): Me {
  return {
    id: 'lid-1',
    ssoSubject: 'sub-1',
    email: 'lid@voorbeeld.nl',
    name: 'Lid',
    platformRole: 'member',
    status: 'active',
    createdAt: '2026-01-01T00:00:00Z',
    lastLoginAt: null,
    contentBaseUrl: 'https://sites.plak.test',
    groupRoles,
    siteRoles: [],
    ciForgejoHosts: [],
    ciAudience: 'plak',
    language: null,
  };
}

describe('mayCreateSiteIn', () => {
  it('refuses without a member at all', () => {
    expect(mayCreateSiteIn(null, 'team-aurora')).toBe(false);
    expect(mayCreateSiteIn(undefined, 'team-aurora')).toBe(false);
  });

  it('refuses a member without a role in that group', () => {
    expect(mayCreateSiteIn(me([{ groupSlug: 'andere-groep', role: 'admin' }]), 'team-aurora')).toBe(
      false,
    );
  });

  it('refuses a reader of the group', () => {
    expect(mayCreateSiteIn(me([{ groupSlug: 'team-aurora', role: 'reader' }]), 'team-aurora')).toBe(false);
  });

  it('allows an editor or an admin of the group', () => {
    expect(mayCreateSiteIn(me([{ groupSlug: 'team-aurora', role: 'editor' }]), 'team-aurora')).toBe(true);
    expect(mayCreateSiteIn(me([{ groupSlug: 'team-aurora', role: 'admin' }]), 'team-aurora')).toBe(true);
  });
});

describe('isGroupAdmin', () => {
  it('refuses without a member at all', () => {
    expect(isGroupAdmin(null, 'team-aurora')).toBe(false);
    expect(isGroupAdmin(undefined, 'team-aurora')).toBe(false);
  });

  it('refuses an admin of another group', () => {
    expect(isGroupAdmin(me([{ groupSlug: 'andere-groep', role: 'admin' }]), 'team-aurora')).toBe(false);
  });

  it('refuses an editor or a reader of the group', () => {
    expect(isGroupAdmin(me([{ groupSlug: 'team-aurora', role: 'editor' }]), 'team-aurora')).toBe(false);
    expect(isGroupAdmin(me([{ groupSlug: 'team-aurora', role: 'reader' }]), 'team-aurora')).toBe(false);
  });

  it('refuses a platform admin without a group role', () => {
    expect(isGroupAdmin({ ...me([]), platformRole: 'admin' }, 'team-aurora')).toBe(false);
  });

  it('allows an admin of the group', () => {
    expect(isGroupAdmin(me([{ groupSlug: 'team-aurora', role: 'admin' }]), 'team-aurora')).toBe(true);
  });
});
