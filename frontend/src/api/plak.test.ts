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

describe('overview (filled and empty)', () => {
  it('returns the seeded groups with their sites', async () => {
    const result = await plak.overview();

    expect(result.groups).toHaveLength(1);
    expect(result.groups[0]!.group.slug).toBe('nldd');
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
    const detail = await plak.group('nldd');

    expect(detail.group.name).toBe('NLDD');
    expect(detail.members.map((l) => l.identifier)).toContain('dev-beheerder');
    expect(detail.members.every((l) => l.groupSlug === 'nldd')).toBe(true);
  });

  it('returns 404 problem+json for an unknown group', async () => {
    const error = await refusedWith(plak.group('onbekend'));
    expect(error.problem.status).toBe(404);
  });

  it('refuses a duplicate group slug with 409', async () => {
    const error = await refusedWith(plak.createGroup('NLDD nogmaals', 'nldd'));
    expect(error.problem.status).toBe(409);
  });

  it('refuses an invalid slug with 422', async () => {
    const error = await refusedWith(plak.createGroup('Hoofdletters', 'Niet-Geldig'));
    expect(error.problem.status).toBe(422);
  });
});

describe('sites', () => {
  it('creates a site and deletes it again', async () => {
    const site = await plak.createSite('nldd', 'Nieuw site', 'nieuw');
    expect(site.access).toEqual({ base: 'public', keys: false, invitees: false });
    expect(site.hasLiveVersion).toBe(false);

    await plak.deleteSite('nldd', 'nieuw');

    const error = await refusedWith(
      plak.setAccess('nldd', 'nieuw', { base: 'sso', keys: false, invitees: false }),
    );
    expect(error.problem.status).toBe(404);
  });

  it('sets base and exceptions of an existing site in one go', async () => {
    const access = { base: 'sso', keys: true, invitees: true } as const;
    const site = await plak.setAccess('nldd', 'website', access);
    expect(site.access).toEqual(access);
  });

  it('turns off an exception by leaving it out', async () => {
    // A PUT sets access as a whole, so a missing field is a choice, not
    // "leave what was there".
    await plak.setAccess('nldd', 'website', { base: 'sso', keys: true, invitees: true });
    const site = await plak.setAccess('nldd', 'website', {
      base: 'sso',
      keys: false,
      invitees: false,
    });
    expect(site.access).toEqual({ base: 'sso', keys: false, invitees: false });
  });
});

describe('invitees (empty, filled, error)', () => {
  it('starts empty for a fresh site', async () => {
    await plak.createSite('nldd', 'Vers site', 'vers');
    const list = await plak.invitees('nldd', 'vers');
    expect(list).toEqual([]);
  });

  it('contains the seeded invitee for the existing site', async () => {
    const list = await plak.invitees('nldd', 'website');
    expect(list).toHaveLength(1);
    expect(list[0]!.identifier).toBe('reviewer@voorbeeld.nl');
  });

  it('refuses an invalid email address with 422', async () => {
    const error = await refusedWith(plak.addInvitee('nldd', 'website', 'niet-een-email'));
    expect(error.problem.status).toBe(422);
  });

  it('adds and removes again, by id and not by address', async () => {
    const added = await plak.addInvitee('nldd', 'website', 'extra@voorbeeld.nl');
    let list = await plak.invitees('nldd', 'website');
    expect(list.map((g) => g.identifier)).toContain('extra@voorbeeld.nl');

    await plak.removeInvitee('nldd', 'website', added.id);
    list = await plak.invitees('nldd', 'website');
    expect(list.map((g) => g.identifier)).not.toContain('extra@voorbeeld.nl');
  });
});

describe('keys (shown once)', () => {
  it('shows the full value only on creation', async () => {
    const created = await plak.createKey('nldd', 'website', 'Nieuwe demo', null);
    expect(created.value).toContain(created.key.selector);

    const list = await plak.keys('nldd', 'website');
    const found = list.find((s) => s.selector === created.key.selector);
    expect(found).toBeDefined();
    expect((found as unknown as { value?: string }).value).toBeUndefined();
  });

  it('revokes a key', async () => {
    await plak.revokeKey('nldd', 'website', 'sel-abc123');
    const list = await plak.keys('nldd', 'website');
    const revoked = list.find((s) => s.selector === 'sel-abc123');
    expect(revoked?.status).toBe('revoked');
  });
});

describe('site repository (trusted publishing)', () => {
  it('returns the linked repository', async () => {
    const repository = await plak.siteRepository('nldd', 'website');
    expect(repository).not.toBeNull();
    expect(repository?.owner).toBe('nldd');
    expect(repository?.repo).toBe('website');
  });

  it('returns null instead of an error when nothing is linked', async () => {
    await plak.deleteSiteRepository('nldd', 'website');
    const repository = await plak.siteRepository('nldd', 'website');
    expect(repository).toBeNull();
  });

  it('lets a different error pass through unchanged', async () => {
    const error = await refusedWith(plak.siteRepository('onbekend', 'website'));
    expect(error.problem.status).toBe(404);
  });

  it('links and unlinks a repository', async () => {
    await plak.deleteSiteRepository('nldd', 'website');
    const linked = await plak.setSiteRepository('nldd', 'website', {
      provider: 'forgejo',
      host: 'https://code.overheid.nl',
      owner: 'robbertbos',
      repo: 'waggle',
      liveBranch: null,
    });
    expect(linked.provider).toBe('forgejo');
    expect(linked.liveBranch).toBeNull();

    await plak.deleteSiteRepository('nldd', 'website');
    const error = await refusedWith(plak.deleteSiteRepository('nldd', 'website'));
    expect(error.problem.code).toBe('REPOSITORY_NOT_SET');
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

  it('approves a code and adds a linked device', async () => {
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

describe('linked devices', () => {
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
    const added = await plak.addGroupMember('nldd', 'collega@voorbeeld.nl');
    // Without a role given the wire carries reader: adding someone grants no
    // more than looking on.
    expect(added.role).toBe('reader');
    let members = await plak.groupMembers('nldd');
    expect(members.map((l) => l.identifier)).toContain('collega@voorbeeld.nl');

    await plak.removeGroupMember('nldd', added.memberId);
    members = await plak.groupMembers('nldd');
    expect(members.map((l) => l.identifier)).not.toContain('collega@voorbeeld.nl');
  });

  it('changes the role of a group member', async () => {
    const ada = (await plak.groupMembers('nldd')).find((l) => l.identifier === 'ada@voorbeeld.nl')!;
    const changed = await plak.setGroupRole('nldd', ada.memberId, 'admin');
    expect(changed.role).toBe('admin');

    const members = await plak.groupMembers('nldd');
    expect(members.find((l) => l.identifier === 'ada@voorbeeld.nl')?.role).toBe('admin');
  });

  it('activates and deactivates a platform member', async () => {
    const activated = await plak.activatePlatformMember('lid-2');
    expect(activated.status).toBe('active');

    const deactivated = await plak.deactivatePlatformMember('lid-2');
    expect(deactivated.status).toBe('deactivated');
  });
});

describe('searching members', () => {
  it('searches by name and by email address, regardless of case', async () => {
    const byName = await plak.searchGroupMembers('nldd', 'VERMEULEN');
    expect(byName.map((hit) => hit.identifier)).toEqual(['sanne@voorbeeld.nl']);

    const byEmail = await plak.searchGroupMembers('nldd', 'jamal@');
    expect(byEmail.map((hit) => hit.name)).toEqual(['Jamal Verhoeven']);
  });

  it('refuses a single character, so the field does not spill the whole address list', async () => {
    const error = await refusedWith(plak.searchGroupMembers('nldd', 'a'));
    expect(error.problem.status).toBe(422);
    expect(error.problem.code).toBe('SEARCH_TOO_SHORT');
  });

  it('marks who is already in the group instead of leaving them out', async () => {
    const hits = await plak.searchGroupMembers('nldd', 'ver');

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
    expect(await plak.searchGroupMembers('nldd', 'oud')).toEqual([]);
    expect(await plak.searchGroupMembers('nldd', 'weg')).toEqual([]);
  });

  it('looks at the roles of that site at the site level', async () => {
    const hits = await plak.searchSiteMembers('nldd', 'website', 'de wit');
    // Zoe holds a role of her own on this site, so here she counts as a member.
    expect(hits.map((hit) => hit.alreadyMember)).toEqual([true]);

    const outsider = await plak.searchSiteMembers('nldd', 'website', 'bakker');
    expect(outsider.map((hit) => hit.identifier)).toEqual(['iris@voorbeeld.nl']);
    expect(outsider[0]!.alreadyMember).toBe(false);
  });
});

describe('versions and rollback', () => {
  it('puts an older live version back live (rollback)', async () => {
    const before = await plak.versions('nldd', 'website');
    const parentVersion = before.find((v) => v.id === 'versie-0');
    expect(parentVersion?.isLive).toBe(false);

    const site = await plak.setVersionLive('nldd', 'website', 'versie-0');
    expect(site.liveVersionId).toBe('versie-0');

    const after = await plak.versions('nldd', 'website');
    expect(after.find((v) => v.id === 'versie-0')?.isLive).toBe(true);
    expect(after.find((v) => v.id === 'versie-1')?.isLive).toBe(false);
  });

  it('refuses a preview version as a rollback target', async () => {
    const error = await refusedWith(plak.setVersionLive('nldd', 'website', 'versie-preview-42'));
    expect(error.problem.status).toBe(422);
  });
});

describe('previews and override', () => {
  it('contains the seeded preview and can override its access', async () => {
    const voor = await plak.previews('nldd', 'website');
    expect(voor).toHaveLength(1);

    const override = { base: 'public', keys: false, invitees: false } as const;
    const updated = await plak.setPreviewAccess('nldd', 'website', 'pr-42', override);
    expect(updated.accessOverride).toEqual(override);

    const back = await plak.setPreviewAccess('nldd', 'website', 'pr-42', null);
    expect(back.accessOverride).toBeNull();
  });

  it('deletes a preview idempotently (204 twice)', async () => {
    await plak.deletePreview('nldd', 'website', 'pr-42');
    await expect(plak.deletePreview('nldd', 'website', 'pr-42')).resolves.toBeUndefined();

    const list = await plak.previews('nldd', 'website');
    expect(list).toEqual([]);
  });
});

describe('upload', () => {
  it('does a live deploy and puts the new version live', async () => {
    const file = new Blob(['<html></html>'], { type: 'text/html' });
    const result = await plak.upload('nldd', 'website', file, 'index.html');

    expect(result.versionId).toBeTruthy();
    const detail = await plak.group('nldd');
    const site = detail.sites.find((p) => p.slug === 'website');
    expect(site?.liveVersionId).toBe(result.versionId);
  });

  it('does a preview deploy and counts the preview in the overview', async () => {
    const file = new Blob(['<html></html>'], { type: 'text/html' });
    await plak.upload('nldd', 'website', file, 'index.html', 'pr-99');

    const previews = await plak.previews('nldd', 'website');
    expect(previews.some((p) => p.ref === 'pr-99')).toBe(true);

    const overview = await plak.overview();
    const site = overview.groups[0]!.sites.find((p) => p.slug === 'website');
    expect(site?.previewCount).toBe(2);
  });

  it('replaces the previous preview version on a second deploy to the same ref', async () => {
    const file = new Blob(['<html></html>'], { type: 'text/html' });
    const first = await plak.upload('nldd', 'website', file, 'index.html', 'pr-42');
    const second = await plak.upload('nldd', 'website', file, 'index.html', 'pr-42');

    expect(second.versionId).not.toBe(first.versionId);
    const previews = await plak.previews('nldd', 'website');
    const preview = previews.find((p) => p.ref === 'pr-42');
    expect(preview?.versionId).toBe(second.versionId);
  });
});
