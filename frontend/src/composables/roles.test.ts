import { describe, expect, it } from 'vitest';

import type { Me } from '@/api/types';

import { isGroupAdmin, isGroupMember, isSiteAdmin, mayCreateSiteIn } from './roles';

function me(groupRoles: Me['groupRoles'], siteRoles: Me['siteRoles'] = []): Me {
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
    siteRoles,
    ciForgejoHosts: [],
    ciAudience: 'plak',
    language: null,
    slugRedirectDays: 30,
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

describe('isGroupMember', () => {
  it('refuses without a member at all', () => {
    expect(isGroupMember(null, 'team-aurora')).toBe(false);
    expect(isGroupMember(undefined, 'team-aurora')).toBe(false);
  });

  it('refuses a member with a role in another group only', () => {
    expect(isGroupMember(me([{ groupSlug: 'andere-groep', role: 'admin' }]), 'team-aurora')).toBe(false);
  });

  it('refuses a platform admin without a group role', () => {
    expect(isGroupMember({ ...me([]), platformRole: 'admin' }, 'team-aurora')).toBe(false);
  });

  it('allows any role in the group, a reader included', () => {
    for (const role of ['reader', 'editor', 'admin'] as const) {
      expect(isGroupMember(me([{ groupSlug: 'team-aurora', role }]), 'team-aurora')).toBe(true);
    }
  });

  it('is not met by a role on a site alone', () => {
    const site = { groupSlug: 'team-aurora', siteSlug: 'website', role: 'admin', effectiveRole: 'admin' } as const;
    expect(isGroupMember(me([], [site]), 'team-aurora')).toBe(false);
  });
});

describe('isSiteAdmin', () => {
  type R = 'reader' | 'editor' | 'admin';
  const site = (role: R, effectiveRole: R, siteSlug = 'website') => ({
    groupSlug: 'team-aurora',
    siteSlug,
    role,
    effectiveRole,
  });

  it('refuses without a member', () => {
    expect(isSiteAdmin(null, 'team-aurora', 'website')).toBe(false);
    expect(isSiteAdmin(undefined, 'team-aurora', 'website')).toBe(false);
  });

  it('accepts an admin of the group', () => {
    expect(isSiteAdmin(me([{ groupSlug: 'team-aurora', role: 'admin' }]), 'team-aurora', 'website')).toBe(true);
  });

  it('accepts an admin role on that site', () => {
    expect(isSiteAdmin(me([], [site('admin', 'admin')]), 'team-aurora', 'website')).toBe(true);
  });

  it('refuses a lower effective role, and an admin role on another site', () => {
    expect(isSiteAdmin(me([], [site('editor', 'editor')]), 'team-aurora', 'website')).toBe(false);
    expect(isSiteAdmin(me([], [site('admin', 'admin', 'andere')]), 'team-aurora', 'website')).toBe(false);
  });
});
