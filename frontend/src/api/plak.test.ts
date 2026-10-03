import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError } from './client';
import { makeMockBackend, type MockBackend } from './mock';
import * as plak from './plak';

let backend: MockBackend;

/**
 * vue-tsc cannot type `rejects.toSatisfy`: vitest wraps the chai matcher in
 * `Promisify`, which drops the call signature. So we catch the rejection
 * ourselves and return the error to assert on.
 */
async function refusedWith(promise: Promise<unknown>): Promise<ApiError> {
  await expect(promise).rejects.toBeInstanceOf(ApiError);
  return (await promise.catch((error: unknown) => error)) as ApiError;
}

beforeEach(() => {
  backend = makeMockBackend();
  vi.stubGlobal('fetch', backend.fetch);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('me (session)', () => {
  it('returns the logged-in member, with the content origin for shared links', async () => {
    const loggedIn = await plak.me();
    expect(loggedIn.ssoSubject).toBe('dev-beheerder');
    expect(loggedIn.contentBaseUrl).toBe('https://sites.plak.test');
  });

  it('returns 401 when there is no session', async () => {
    backend.data.loggedInMemberId = null;

    const error = await refusedWith(plak.me());
    expect(error.problem.status).toBe(401);
  });
});

describe('interface language on the account', () => {
  it('records a chosen language', async () => {
    await plak.setMyLanguage('en');
    expect(backend.data.myLanguage).toBe('en');
  });

  it('hands the choice back to the browser default with null', async () => {
    await plak.setMyLanguage('en');
    await plak.setMyLanguage(null);
    expect(backend.data.myLanguage).toBeNull();
  });

  it('refuses a language code that is neither nl nor en', async () => {
    const error = await refusedWith(
      plak.setMyLanguage('de' as unknown as import('@/api/types').MemberLanguage),
    );
    expect(error.problem.status).toBe(422);
  });
});

describe('overview (filled and empty)', () => {
  it('returns the seeded groups with their sites', async () => {
    const result = await plak.overview();

    expect(result.groups).toHaveLength(1);
    expect(result.groups[0]!.group.slug).toBe('team-aurora');
    expect(result.groups[0]!.sites).toHaveLength(1);
    expect(result.groups[0]!.sites[0]!.slug).toBe('website');
  });

  it('shows a new group without sites as an empty list', async () => {
    await plak.createGroup('Lege groep', 'leeg');

    const result = await plak.overview();
    const emptyGroup = result.groups.find((g) => g.group.slug === 'leeg');

    expect(emptyGroup).toBeDefined();
    expect(emptyGroup!.sites).toEqual([]);
  });

  it('makes the creator of a new group a group member right away', async () => {
    await plak.createGroup('Nieuwe groep', 'nieuw');

    const members = await plak.groupMembers('nieuw');
    expect(members.map((l) => l.identifier)).toEqual(['beheerder@voorbeeld.nl']);
  });
});

describe('error handling', () => {
  it('carries the machine-readable code along, not just the sentence', async () => {
    // The code is stable across rewordings and across an interface in a
    // different language; without this rule nothing in the SPA could
    // branch on it.
    vi.stubGlobal(
      'fetch',
      vi.fn(() =>
        Promise.resolve(
          new Response(
            JSON.stringify({
              type: 'about:blank',
              title: 'Geen toegang',
              status: 403,
              detail: 'Je toegang is ingetrokken door een platformbeheerder.',
              code: 'MEMBER_DEACTIVATED',
            }),
            { status: 403, headers: { 'content-type': 'application/problem+json' } },
          ),
        ),
      ),
    );

    const error = await refusedWith(plak.me());
    expect(error.problem.code).toBe('MEMBER_DEACTIVATED');
    expect(error.problem.status).toBe(403);
  });
});

describe('groups', () => {
  it('fetches group detail including members', async () => {
    const detail = await plak.group('team-aurora');

    expect(detail.group.name).toBe('Team Aurora');
    expect(detail.members.map((l) => l.identifier)).toContain('dev-beheerder');
    expect(detail.members.every((l) => l.groupSlug === 'team-aurora')).toBe(true);
  });

  it('returns 404 problem+json for an unknown group', async () => {
    const error = await refusedWith(plak.group('onbekend'));
    expect(error.problem.status).toBe(404);
  });

  it('refuses a duplicate group slug with 409', async () => {
    const error = await refusedWith(plak.createGroup('Team Aurora nogmaals', 'team-aurora'));
    expect(error.problem.status).toBe(409);
  });

  it('refuses an invalid slug with 422', async () => {
    const error = await refusedWith(plak.createGroup('Hoofdletters', 'Niet-Geldig'));
    expect(error.problem.status).toBe(422);
  });

  it('deletes a group along with its members, sites and everything on them', async () => {
    await plak.createGroup('Ander', 'ander');
    await plak.createSite('ander', 'Blijft', 'blijft');

    await plak.deleteGroup('team-aurora');

    expect((await refusedWith(plak.group('team-aurora'))).problem.status).toBe(404);
    expect((await refusedWith(plak.groupMembers('team-aurora'))).problem.status).toBe(404);
    expect((await refusedWith(plak.versions('team-aurora', 'website'))).problem.status).toBe(404);
    expect((await plak.group('ander')).sites.map((s) => s.slug)).toEqual(['blijft']);
  });

  it('returns 404 when deleting an unknown group', async () => {
    const error = await refusedWith(plak.deleteGroup('onbekend'));
    expect(error.problem.status).toBe(404);
  });
});

describe('sites', () => {
  it('creates a site and deletes it again', async () => {
    const site = await plak.createSite('team-aurora', 'Nieuw site', 'nieuw');
    expect(site.access).toEqual({ base: 'public', keys: false, invitees: false });
    expect(site.hasLiveVersion).toBe(false);

    await plak.deleteSite('team-aurora', 'nieuw');

    const error = await refusedWith(
      plak.setAccess('team-aurora', 'nieuw', { base: 'sso', keys: false, invitees: false }),
    );
    expect(error.problem.status).toBe(404);
  });

  it('sets base and exceptions of an existing site in one go', async () => {
    const access = { base: 'sso', keys: true, invitees: true } as const;
    const site = await plak.setAccess('team-aurora', 'website', access);
    expect(site.access).toEqual(access);
  });

  it('turns off an exception by leaving it out', async () => {
    // A PUT sets access as a whole, so a missing field is a choice, not
    // "leave what was there".
    await plak.setAccess('team-aurora', 'website', { base: 'sso', keys: true, invitees: true });
    const site = await plak.setAccess('team-aurora', 'website', {
      base: 'sso',
      keys: false,
      invitees: false,
    });
    expect(site.access).toEqual({ base: 'sso', keys: false, invitees: false });
  });

  it('refuses an invalid slug with 422', async () => {
    const error = await refusedWith(plak.createSite('team-aurora', 'Hoofdletters', 'Niet-Geldig'));
    expect(error.problem.status).toBe(422);
  });

  it('refuses a duplicate slug within the same group with 409', async () => {
    const error = await refusedWith(plak.createSite('team-aurora', 'Website nogmaals', 'website'));
    expect(error.problem.status).toBe(409);
  });
});

describe('invitees (empty, filled, error)', () => {
  it('starts empty for a fresh site', async () => {
    await plak.createSite('team-aurora', 'Vers site', 'vers');
    const list = await plak.invitees('team-aurora', 'vers');
    expect(list).toEqual([]);
  });

  it('contains the seeded invitee for the existing site', async () => {
    const list = await plak.invitees('team-aurora', 'website');
    expect(list).toHaveLength(1);
    expect(list[0]!.identifier).toBe('reviewer@voorbeeld.nl');
  });

  it('refuses an invalid email address with 422', async () => {
    const error = await refusedWith(plak.addInvitee('team-aurora', 'website', 'niet-een-email'));
    expect(error.problem.status).toBe(422);
  });

  it('adds and removes again, by id and not by address', async () => {
    const added = await plak.addInvitee('team-aurora', 'website', 'extra@voorbeeld.nl');
    let list = await plak.invitees('team-aurora', 'website');
    expect(list.map((g) => g.identifier)).toContain('extra@voorbeeld.nl');

    await plak.removeInvitee('team-aurora', 'website', added.id);
    list = await plak.invitees('team-aurora', 'website');
    expect(list.map((g) => g.identifier)).not.toContain('extra@voorbeeld.nl');
  });
});

describe('keys (shown once)', () => {
  it('shows the full value only on creation', async () => {
    const created = await plak.createKey('team-aurora', 'website', 'Nieuwe demo', null);
    expect(created.value).toContain(created.key.selector);

    const list = await plak.keys('team-aurora', 'website');
    const found = list.find((s) => s.selector === created.key.selector);
    expect(found).toBeDefined();
    expect((found as unknown as { value?: string }).value).toBeUndefined();
  });

  it('refuses an expiry date that already lies in the past', async () => {
    const past = new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString();
    const error = await refusedWith(plak.createKey('team-aurora', 'website', 'Verlopen', past));
    expect(error.problem.status).toBe(422);
    expect(error.problem.code).toBe('EXPIRY_IN_PAST');
  });

  it('refuses an expiry date too far in the future', async () => {
    const farFuture = new Date(Date.now() + 400 * 24 * 60 * 60 * 1000).toISOString();
    const error = await refusedWith(plak.createKey('team-aurora', 'website', 'Te ver', farFuture));
    expect(error.problem.status).toBe(422);
    expect(error.problem.code).toBe('EXPIRY_TOO_FAR');
  });

  it('revokes a key', async () => {
    await plak.revokeKey('team-aurora', 'website', 'sel-abc123');
    const list = await plak.keys('team-aurora', 'website');
    const revoked = list.find((s) => s.selector === 'sel-abc123');
    expect(revoked?.status).toBe('revoked');
  });
});

describe('site repository (trusted publishing)', () => {
  it('returns the linked repository', async () => {
    const repository = await plak.siteRepository('team-aurora', 'website');
    expect(repository).not.toBeNull();
    expect(repository?.owner).toBe('team-aurora');
    expect(repository?.repo).toBe('website');
  });

  it('returns null instead of an error when nothing is linked', async () => {
    await plak.deleteSiteRepository('team-aurora', 'website');
    const repository = await plak.siteRepository('team-aurora', 'website');
    expect(repository).toBeNull();
  });

  it('lets a different error pass through unchanged', async () => {
    const error = await refusedWith(plak.siteRepository('onbekend', 'website'));
    expect(error.problem.status).toBe(404);
  });

  it('links and unlinks a repository', async () => {
    await plak.deleteSiteRepository('team-aurora', 'website');
    const linked = await plak.setSiteRepository('team-aurora', 'website', {
      provider: 'forgejo',
      host: 'https://code.overheid.nl',
      owner: 'robbertbos',
      repo: 'waggle',
      liveBranch: null,
    });
    expect(linked.provider).toBe('forgejo');
    expect(linked.liveBranch).toBeNull();

    await plak.deleteSiteRepository('team-aurora', 'website');
    const error = await refusedWith(plak.deleteSiteRepository('team-aurora', 'website'));
    expect(error.problem.code).toBe('REPOSITORY_NOT_SET');
  });

  it('refuses to link a repository without an owner or a repo name', async () => {
    const error = await refusedWith(
      plak.setSiteRepository('team-aurora', 'website', {
        provider: 'github',
        host: 'https://github.com',
        owner: '',
        repo: '',
        liveBranch: null,
      }),
    );
    expect(error.problem.code).toBe('REPOSITORY_INVALID');
  });

  it('refuses a Forgejo host that is not on the allowlist', async () => {
    const error = await refusedWith(
      plak.setSiteRepository('team-aurora', 'website', {
        provider: 'forgejo',
        host: 'https://onbekende-forge.example',
        owner: 'team-aurora',
        repo: 'website',
        liveBranch: null,
      }),
    );
    expect(error.problem.code).toBe('HOST_NOT_ALLOWED');
  });
});

describe('cli device linking', () => {
  it('looks up a valid code', async () => {
    const found = await plak.lookupDeviceAuthorization('abcd-efgh');
    expect(found.userCode).toBe('ABCD-EFGH');
    expect(found.clientName).toBe('plak-cli');
  });

  it('returns 404 USER_CODE_UNKNOWN for an unknown code', async () => {
    const error = await refusedWith(plak.lookupDeviceAuthorization('ZZZZ-ZZZZ'));
    expect(error.problem.status).toBe(404);
    expect(error.problem.code).toBe('USER_CODE_UNKNOWN');
  });

  it('approves a code and adds a linked session', async () => {
    const before = await plak.cliSessions();
    await plak.approveDeviceAuthorization('ABCD-EFGH');
    const after = await plak.cliSessions();
    expect(after.length).toBe(before.length + 1);

    const error = await refusedWith(plak.lookupDeviceAuthorization('ABCD-EFGH'));
    expect(error.problem.code).toBe('USER_CODE_UNKNOWN');
  });

  it('refuses a code again after denying it', async () => {
    await plak.denyDeviceAuthorization('ABCD-EFGH');
    const error = await refusedWith(plak.lookupDeviceAuthorization('ABCD-EFGH'));
    expect(error.problem.code).toBe('USER_CODE_UNKNOWN');
  });
});

describe('linked sessions', () => {
  it('returns the seeded session and revokes it again', async () => {
    const before = await plak.cliSessions();
    expect(before.map((s) => s.id)).toContain('cli-sessie-1');

    await plak.revokeCliSession('cli-sessie-1');
    const after = await plak.cliSessions();
    expect(after.map((s) => s.id)).not.toContain('cli-sessie-1');
  });

  it('returns 404 CLI_SESSION_UNKNOWN for an unknown device', async () => {
    const error = await refusedWith(plak.revokeCliSession('onbekend'));
    expect(error.problem.status).toBe(404);
    expect(error.problem.code).toBe('CLI_SESSION_UNKNOWN');
  });
});

describe('group members and platform members', () => {
  it('adds a group member and removes it again', async () => {
    const added = await plak.addGroupMember('team-aurora', 'collega@voorbeeld.nl');
    // Without a role given the wire carries reader: adding someone grants no
    // more than looking on.
    expect(added.role).toBe('reader');
    let members = await plak.groupMembers('team-aurora');
    expect(members.map((l) => l.identifier)).toContain('collega@voorbeeld.nl');

    await plak.removeGroupMember('team-aurora', added.memberId);
    members = await plak.groupMembers('team-aurora');
    expect(members.map((l) => l.identifier)).not.toContain('collega@voorbeeld.nl');
  });

  it('refuses to add a group member with an invalid identifier', async () => {
    const error = await refusedWith(plak.addGroupMember('team-aurora', 'niet-een-email'));
    expect(error.problem.status).toBe(422);
  });

  it('reports the site roles a group member holds in this group', async () => {
    const zoe = (await plak.groupMembers('team-aurora')).find((l) => l.identifier === 'zoe@voorbeeld.nl')!;
    expect(zoe.siteRoles).toEqual([
      { siteSlug: 'website', siteTitle: 'Team Aurora website', role: 'admin' },
    ]);
  });

  it('sorts several site roles for the same member by site slug', async () => {
    await plak.createSite('team-aurora', 'Alpha', 'alpha');
    backend.data.siteRoles.push({
      groupSlug: 'team-aurora',
      siteSlug: 'alpha',
      identifier: 'zoe@voorbeeld.nl',
      role: 'reader',
    });

    const zoe = (await plak.groupMembers('team-aurora')).find((l) => l.identifier === 'zoe@voorbeeld.nl')!;

    expect(zoe.siteRoles.map((r) => r.siteSlug)).toEqual(['alpha', 'website']);
  });

  it('leaves the site roles standing unless the removal asks for them', async () => {
    const zoe = (await plak.groupMembers('team-aurora')).find((l) => l.identifier === 'zoe@voorbeeld.nl')!;

    await plak.removeGroupMember('team-aurora', zoe.memberId);

    const site = await plak.siteMembers('team-aurora', 'website');
    expect(site.find((l) => l.identifier === 'zoe@voorbeeld.nl')?.siteRole).toBe('admin');
  });

  it('takes the site roles along when the removal asks for them', async () => {
    const zoe = (await plak.groupMembers('team-aurora')).find((l) => l.identifier === 'zoe@voorbeeld.nl')!;

    await plak.removeGroupMember('team-aurora', zoe.memberId, 'remove');

    const site = await plak.siteMembers('team-aurora', 'website');
    expect(site.find((l) => l.identifier === 'zoe@voorbeeld.nl')).toBeUndefined();
  });

  it('changes the role of a group member', async () => {
    const ada = (await plak.groupMembers('team-aurora')).find((l) => l.identifier === 'ada@voorbeeld.nl')!;
    const changed = await plak.setGroupRole('team-aurora', ada.memberId, 'admin');
    expect(changed.role).toBe('admin');

    const members = await plak.groupMembers('team-aurora');
    expect(members.find((l) => l.identifier === 'ada@voorbeeld.nl')?.role).toBe('admin');
  });

  it('activates and deactivates a platform member', async () => {
    const activated = await plak.activatePlatformMember('lid-2');
    expect(activated.status).toBe('active');

    const deactivated = await plak.deactivatePlatformMember('lid-2');
    expect(deactivated.status).toBe('deactivated');
  });
});

describe('site members', () => {
  it('refuses an invalid identifier with 422', async () => {
    const error = await refusedWith(plak.addSiteMember('team-aurora', 'website', 'niet-een-email', 'reader'));
    expect(error.problem.status).toBe(422);
  });

  it('refuses a member who already has a role on the site, with 409', async () => {
    await plak.addSiteMember('team-aurora', 'website', 'ada@voorbeeld.nl', 'reader');
    const error = await refusedWith(
      plak.addSiteMember('team-aurora', 'website', 'ada@voorbeeld.nl', 'editor'),
    );
    expect(error.problem.status).toBe(409);
  });

  it('refuses to change the role of a member who has none on the site, with 404', async () => {
    const error = await refusedWith(
      plak.setSiteRole('team-aurora', 'website', 'onbekend-lid-id', 'editor'),
    );
    expect(error.problem.status).toBe(404);
  });

  it('refuses to remove a member who has no role on the site, with 404', async () => {
    const error = await refusedWith(plak.removeSiteMember('team-aurora', 'website', 'onbekend-lid-id'));
    expect(error.problem.status).toBe(404);
  });
});

describe('searching members', () => {
  it('searches by name and by email address, regardless of case', async () => {
    const byName = await plak.searchGroupMembers('team-aurora', 'VERMEULEN');
    expect(byName.map((hit) => hit.identifier)).toEqual(['sanne@voorbeeld.nl']);

    const byEmail = await plak.searchGroupMembers('team-aurora', 'jamal@');
    expect(byEmail.map((hit) => hit.name)).toEqual(['Jamal Verhoeven']);
  });

  it('refuses a single character, so the field does not spill the whole address list', async () => {
    const error = await refusedWith(plak.searchGroupMembers('team-aurora', 'a'));
    expect(error.problem.status).toBe(422);
    expect(error.problem.code).toBe('SEARCH_TOO_SHORT');
  });

  it('marks who is already in the group instead of leaving them out', async () => {
    const hits = await plak.searchGroupMembers('team-aurora', 'ver');

    // Ordered by name, with Ada in the list although she is a group member.
    expect(hits.map((hit) => hit.name)).toEqual([
      'Ada Vermeer',
      'Jamal Verhoeven',
      'Sanne Vermeulen',
    ]);
    expect(hits.map((hit) => hit.alreadyMember)).toEqual([true, false, false]);
  });

  it('leaves whoever no longer has access out of the suggestions', async () => {
    // Karel Oud and Wim Weg are both deactivated: neither is someone you add
    // to a group.
    expect(await plak.searchGroupMembers('team-aurora', 'oud')).toEqual([]);
    expect(await plak.searchGroupMembers('team-aurora', 'weg')).toEqual([]);
  });

  it('looks at the roles of that site at the site level', async () => {
    const hits = await plak.searchSiteMembers('team-aurora', 'website', 'de wit');
    // Zoe holds a role of her own on this site, so here she counts as a member.
    expect(hits.map((hit) => hit.alreadyMember)).toEqual([true]);

    const outsider = await plak.searchSiteMembers('team-aurora', 'website', 'bakker');
    expect(outsider.map((hit) => hit.identifier)).toEqual(['iris@voorbeeld.nl']);
    expect(outsider[0]!.alreadyMember).toBe(false);
    expect(outsider[0]!.groupRole).toBeNull();
  });

  it('offers a group member on a site, with the role the group gives them', async () => {
    const hits = await plak.searchSiteMembers('team-aurora', 'website', 'ada');

    // Ada is redacteur in the group and has no role of her own on this site:
    // a site role would still widen what she may here.
    expect(hits.map((hit) => hit.alreadyMember)).toEqual([false]);
    expect(hits.map((hit) => hit.groupRole)).toEqual(['editor']);
  });
});

describe('site storage', () => {
  it('reports the usage and the retention rule of a site', async () => {
    expect(await plak.siteStorage('team-aurora', 'website')).toEqual({
      usedBytes: 77594624,
      maxBytes: 524288000,
      liveVersionsKept: 5,
      liveVersionsKeptIsDefault: true,
      defaultLiveVersionsKept: 5,
    });
  });

  it('reports the site\'s own number as the effective one', async () => {
    await plak.setLiveVersionsKept('team-aurora', 'website', 3);

    expect(await plak.siteStorage('team-aurora', 'website')).toMatchObject({
      liveVersionsKept: 3,
      liveVersionsKeptIsDefault: false,
      defaultLiveVersionsKept: 5,
    });
  });

  it('follows an overridden platform default', async () => {
    backend.data.defaultLiveVersionsKept = 0;

    expect(await plak.siteStorage('team-aurora', 'website')).toMatchObject({
      liveVersionsKept: 0,
      liveVersionsKeptIsDefault: true,
    });
  });

  it('refuses an unknown site', async () => {
    expect((await refusedWith(plak.siteStorage('team-aurora', 'bestaat-niet'))).problem.status).toBe(404);
  });
});

describe('live versions kept', () => {
  it('sets an own number and hands back the site', async () => {
    const site = await plak.setLiveVersionsKept('team-aurora', 'website', 3);

    expect(site.liveVersionsKept).toBe(3);
  });

  it('accepts 0 (keep all) and a large number', async () => {
    expect((await plak.setLiveVersionsKept('team-aurora', 'website', 0)).liveVersionsKept).toBe(0);
    expect((await plak.setLiveVersionsKept('team-aurora', 'website', 5000)).liveVersionsKept).toBe(5000);
  });

  it('goes back to the platform default with null', async () => {
    await plak.setLiveVersionsKept('team-aurora', 'website', 3);

    expect((await plak.setLiveVersionsKept('team-aurora', 'website', null)).liveVersionsKept).toBeNull();
  });

  it.each([-1, 2.5])('refuses %s with LIVE_VERSIONS_KEPT_INVALID', async (value) => {
    const error = await refusedWith(plak.setLiveVersionsKept('team-aurora', 'website', value));

    expect(error.problem.status).toBe(422);
    expect(error.problem.code).toBe('LIVE_VERSIONS_KEPT_INVALID');
  });

  it('refuses a number the column cannot hold with LIVE_VERSIONS_KEPT_TOO_LARGE', async () => {
    const error = await refusedWith(plak.setLiveVersionsKept('team-aurora', 'website', 2 ** 31));

    expect(error.problem.status).toBe(422);
    expect(error.problem.code).toBe('LIVE_VERSIONS_KEPT_TOO_LARGE');
  });

  it('refuses an unknown site', async () => {
    expect((await refusedWith(plak.setLiveVersionsKept('team-aurora', 'bestaat-niet', 3))).problem.status).toBe(404);
  });
});

describe('versions and rollback', () => {
  it('puts an older live version back live (rollback)', async () => {
    const before = await plak.versions('team-aurora', 'website');
    const parentVersion = before.find((v) => v.id === 'versie-0');
    expect(parentVersion?.isLive).toBe(false);

    const site = await plak.setVersionLive('team-aurora', 'website', 'versie-0');
    expect(site.liveVersionId).toBe('versie-0');

    const after = await plak.versions('team-aurora', 'website');
    expect(after.find((v) => v.id === 'versie-0')?.isLive).toBe(true);
    expect(after.find((v) => v.id === 'versie-1')?.isLive).toBe(false);
  });

  it('refuses a preview version as a rollback target', async () => {
    const error = await refusedWith(plak.setVersionLive('team-aurora', 'website', 'versie-preview-42'));
    expect(error.problem.status).toBe(422);
  });

  it('returns 404 for setting an unknown version live', async () => {
    const error = await refusedWith(plak.setVersionLive('team-aurora', 'website', 'onbekend'));
    expect(error.problem.status).toBe(404);
  });

  it('returns 404 for setting a version live on an unknown site', async () => {
    const error = await refusedWith(plak.setVersionLive('team-aurora', 'onbekend', 'versie-0'));
    expect(error.problem.status).toBe(404);
  });
});

describe('previews and override', () => {
  it('contains the seeded preview and can override its access', async () => {
    const voor = await plak.previews('team-aurora', 'website');
    expect(voor).toHaveLength(1);

    const override = { base: 'public', keys: false, invitees: false } as const;
    const updated = await plak.setPreviewAccess('team-aurora', 'website', 'pr-42', override);
    expect(updated.accessOverride).toEqual(override);

    const back = await plak.setPreviewAccess('team-aurora', 'website', 'pr-42', null);
    expect(back.accessOverride).toBeNull();
  });

  it('defaults the base to public when the override leaves it out', async () => {
    const updated = await plak.setPreviewAccess(
      'team-aurora',
      'website',
      'pr-42',
      { keys: true, invitees: false } as unknown as import('@/api/types').Access,
    );
    expect(updated.accessOverride?.base).toBe('public');
  });

  it('returns 404 for listing previews of an unknown site', async () => {
    const error = await refusedWith(plak.previews('team-aurora', 'onbekend'));
    expect(error.problem.status).toBe(404);
  });

  it('returns 404 for overriding preview access on an unknown site', async () => {
    const error = await refusedWith(
      plak.setPreviewAccess('team-aurora', 'onbekend', 'pr-42', { base: 'public', keys: false, invitees: false }),
    );
    expect(error.problem.status).toBe(404);
  });

  it('returns 404 for overriding access on an unknown preview', async () => {
    const error = await refusedWith(
      plak.setPreviewAccess('team-aurora', 'website', 'onbekend-ref', { base: 'public', keys: false, invitees: false }),
    );
    expect(error.problem.status).toBe(404);
  });

  it('deletes a preview idempotently (204 twice)', async () => {
    await plak.deletePreview('team-aurora', 'website', 'pr-42');
    await expect(plak.deletePreview('team-aurora', 'website', 'pr-42')).resolves.toBeUndefined();

    const list = await plak.previews('team-aurora', 'website');
    expect(list).toEqual([]);
  });

  it('returns 404 for deleting a preview on an unknown site', async () => {
    const error = await refusedWith(plak.deletePreview('team-aurora', 'onbekend', 'pr-42'));
    expect(error.problem.status).toBe(404);
  });
});

describe('upload', () => {
  it('does a live deploy and puts the new version live', async () => {
    const file = new Blob(['<html></html>'], { type: 'text/html' });
    const result = await plak.upload('team-aurora', 'website', file, 'index.html');

    expect(result.versionId).toBeTruthy();
    const detail = await plak.group('team-aurora');
    const site = detail.sites.find((p) => p.slug === 'website');
    expect(site?.liveVersionId).toBe(result.versionId);
  });

  it('does a preview deploy and counts the preview in the overview', async () => {
    const file = new Blob(['<html></html>'], { type: 'text/html' });
    await plak.upload('team-aurora', 'website', file, 'index.html', 'pr-99');

    const previews = await plak.previews('team-aurora', 'website');
    expect(previews.some((p) => p.ref === 'pr-99')).toBe(true);

    const overview = await plak.overview();
    const site = overview.groups[0]!.sites.find((p) => p.slug === 'website');
    expect(site?.previewCount).toBe(2);
  });

  it('replaces the previous preview version on a second deploy to the same ref', async () => {
    const file = new Blob(['<html></html>'], { type: 'text/html' });
    const first = await plak.upload('team-aurora', 'website', file, 'index.html', 'pr-42');
    const second = await plak.upload('team-aurora', 'website', file, 'index.html', 'pr-42');

    expect(second.versionId).not.toBe(first.versionId);
    const previews = await plak.previews('team-aurora', 'website');
    const preview = previews.find((p) => p.ref === 'pr-42');
    expect(preview?.versionId).toBe(second.versionId);
  });

  // The mock mirrors the real backend's defensive checks on the raw request,
  // which plak.upload() itself can never trigger (it always builds a proper
  // FormData with a "file" field); this reaches them directly, the way a
  // malformed request from outside the SPA would.
  it('returns 404 for a deploy to an unknown site', async () => {
    const file = new Blob(['<html></html>'], { type: 'text/html' });
    const error = await refusedWith(plak.upload('team-aurora', 'onbekend', file, 'index.html'));
    expect(error.problem.status).toBe(404);
  });

  it('refuses a deploy whose body is not multipart/form-data', async () => {
    const response = await backend.fetch('/-/api/v1/sites/team-aurora/website/deploys', {
      method: 'POST',
      body: JSON.stringify({ not: 'a form' }),
    });
    expect(response.status).toBe(422);
  });

  it('refuses a deploy whose form carries no file field', async () => {
    const response = await backend.fetch('/-/api/v1/sites/team-aurora/website/deploys', {
      method: 'POST',
      body: new FormData(),
    });
    expect(response.status).toBe(422);
  });
});
