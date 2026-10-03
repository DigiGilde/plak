import { afterEach, describe, expect, it } from 'vitest';

import { installMock, makeMockBackend } from './mock';

describe('mockFetch (fallback)', () => {
  it('answers an unmatched route with a 404 problem', async () => {
    const backend = makeMockBackend();

    const response = await backend.fetch('/-/api/v1/does-not-exist', { method: 'GET' });

    expect(response.status).toBe(404);
    const body = (await response.json()) as { title: string };
    expect(body.title).toBe('Onbekend eindpunt');
  });
});

describe('mockFetch route parsing', () => {
  it('defaults the method to GET when none is given', async () => {
    const backend = makeMockBackend();

    const response = await backend.fetch('/-/api/v1/overview');

    expect(response.status).toBe(200);
  });

  it('accepts a URL instance as input', async () => {
    const backend = makeMockBackend();

    const response = await backend.fetch(new URL('https://plak.test/-/api/v1/overview'));

    expect(response.status).toBe(200);
  });

  it('accepts a Request instance as input', async () => {
    const backend = makeMockBackend();

    const response = await backend.fetch(new Request('https://plak.test/-/api/v1/overview'));

    expect(response.status).toBe(200);
  });
});

describe('groups: not found and defaults', () => {
  it('refuses default-access for an unknown group', async () => {
    const backend = makeMockBackend();

    const response = await backend.fetch('/-/api/v1/groups/geen-groep/default-access', {
      method: 'PUT',
    });

    expect(response.status).toBe(404);
  });

  it('defaults default-access to public and an empty body when none is given', async () => {
    const backend = makeMockBackend();

    const response = await backend.fetch('/-/api/v1/groups/team-aurora/default-access', { method: 'PUT' });

    expect(response.status).toBe(200);
    const body = (await response.json()) as { defaultAccess: { base: string } };
    expect(body.defaultAccess.base).toBe('public');
  });

  it('refuses creating a site in an unknown group', async () => {
    const backend = makeMockBackend();

    const response = await backend.fetch('/-/api/v1/groups/geen-groep/sites', {
      method: 'POST',
      body: JSON.stringify({ slug: 'nieuw', title: 'Nieuw' }),
    });

    expect(response.status).toBe(404);
  });

  it('refuses creating a group without a slug', async () => {
    const backend = makeMockBackend();

    const response = await backend.fetch('/-/api/v1/groups', { method: 'POST' });

    expect(response.status).toBe(422);
  });

  it('refuses creating a site without a slug', async () => {
    const backend = makeMockBackend();

    const response = await backend.fetch('/-/api/v1/groups/team-aurora/sites', { method: 'POST' });

    expect(response.status).toBe(422);
  });

  it('refuses listing members of an unknown group', async () => {
    const backend = makeMockBackend();

    const response = await backend.fetch('/-/api/v1/groups/geen-groep/members', { method: 'GET' });

    expect(response.status).toBe(404);
  });

  it('refuses an identifier-less member invite and defaults the role', async () => {
    const backend = makeMockBackend();

    const response = await backend.fetch('/-/api/v1/groups/team-aurora/members', { method: 'POST' });

    expect(response.status).toBe(422);
  });

  it('adds a group member with the default reader role when none is given', async () => {
    const backend = makeMockBackend();

    const response = await backend.fetch('/-/api/v1/groups/team-aurora/members', {
      method: 'POST',
      body: JSON.stringify({ identifier: 'iris@voorbeeld.nl' }),
    });

    expect(response.status).toBe(201);
    const body = (await response.json()) as { role: string };
    expect(body.role).toBe('reader');
  });

  it('refuses searching members of an unknown group', async () => {
    const backend = makeMockBackend();

    const response = await backend.fetch('/-/api/v1/groups/geen-groep/members/search', {
      method: 'GET',
    });

    expect(response.status).toBe(404);
  });

  it('treats a missing search query as empty and refuses it as too short', async () => {
    const backend = makeMockBackend();

    const response = await backend.fetch('/-/api/v1/groups/team-aurora/members/search', { method: 'GET' });

    expect(response.status).toBe(422);
  });

  it('refuses changing a role in an unknown group', async () => {
    const backend = makeMockBackend();

    const response = await backend.fetch('/-/api/v1/groups/geen-groep/members/lid-1/role', {
      method: 'PUT',
      body: JSON.stringify({ role: 'admin' }),
    });

    expect(response.status).toBe(404);
  });

  it('refuses changing the role of an unknown member', async () => {
    const backend = makeMockBackend();

    const response = await backend.fetch('/-/api/v1/groups/team-aurora/members/geen-lid/role', {
      method: 'PUT',
      body: JSON.stringify({ role: 'admin' }),
    });

    expect(response.status).toBe(404);
  });

  it('refuses removing a member from an unknown group', async () => {
    const backend = makeMockBackend();

    const response = await backend.fetch('/-/api/v1/groups/geen-groep/members/lid-1', {
      method: 'DELETE',
    });

    expect(response.status).toBe(404);
  });
});

describe('platform members: unknown member', () => {
  it('refuses (de)activating an unknown member', async () => {
    const backend = makeMockBackend();

    const response = await backend.fetch('/-/api/v1/platform/members/geen-lid/_activate', {
      method: 'POST',
    });

    expect(response.status).toBe(404);
  });

  it('refuses setting the platform role of an unknown member', async () => {
    const backend = makeMockBackend();

    const response = await backend.fetch('/-/api/v1/platform/members/geen-lid/platform-role', {
      method: 'PUT',
      body: JSON.stringify({ platformRole: 'admin' }),
    });

    expect(response.status).toBe(404);
  });
});

describe('cli device authorizations: defaults', () => {
  it('treats a missing userCode as empty and refuses it as unknown', async () => {
    const backend = makeMockBackend();

    const response = await backend.fetch('/-/api/v1/cli/device-authorizations/lookup', {
      method: 'POST',
    });

    expect(response.status).toBe(404);
    const body = (await response.json()) as { code: string };
    expect(body.code).toBe('USER_CODE_UNKNOWN');
  });
});

describe('site members and invitees: defaults', () => {
  it('refuses an identifier-less site member invite', async () => {
    const backend = makeMockBackend();

    const response = await backend.fetch('/-/api/v1/sites/team-aurora/website/members', {
      method: 'POST',
    });

    expect(response.status).toBe(422);
  });

  it('adds a site member with the default reader role when none is given', async () => {
    const backend = makeMockBackend();

    const response = await backend.fetch('/-/api/v1/sites/team-aurora/website/members', {
      method: 'POST',
      body: JSON.stringify({ identifier: 'iris@voorbeeld.nl' }),
    });

    expect(response.status).toBe(201);
    const body = (await response.json()) as { siteRole: string };
    expect(body.siteRole).toBe('reader');
  });

  it('refuses an identifier-less invitee', async () => {
    const backend = makeMockBackend();

    const response = await backend.fetch('/-/api/v1/sites/team-aurora/website/invitees', {
      method: 'POST',
    });

    expect(response.status).toBe(422);
  });
});

describe('sites: not found', () => {
  it.each([
    ['DELETE', '/-/api/v1/sites/team-aurora/geen-site'],
    ['PUT', '/-/api/v1/sites/team-aurora/geen-site/external-sources'],
    ['PUT', '/-/api/v1/sites/team-aurora/geen-site/sandbox'],
    ['PUT', '/-/api/v1/sites/team-aurora/geen-site/live-versions-kept'],
    ['GET', '/-/api/v1/sites/team-aurora/geen-site/members'],
    ['GET', '/-/api/v1/sites/team-aurora/geen-site/members/search'],
    ['PUT', '/-/api/v1/sites/team-aurora/geen-site/members/lid-1/role'],
    ['DELETE', '/-/api/v1/sites/team-aurora/geen-site/members/lid-1'],
    ['GET', '/-/api/v1/sites/team-aurora/geen-site/invitees'],
    ['DELETE', '/-/api/v1/sites/team-aurora/geen-site/invitees/genodigde-1'],
    ['GET', '/-/api/v1/sites/team-aurora/geen-site/keys'],
    ['DELETE', '/-/api/v1/sites/team-aurora/geen-site/keys/sel-abc123'],
  ])('answers %s %s with a 404 for an unknown site', async (method, path) => {
    const backend = makeMockBackend();

    const response = await backend.fetch(path, {
      method,
      body: method === 'PUT' ? JSON.stringify({ role: 'admin' }) : undefined,
    });

    expect(response.status).toBe(404);
  });

  it('treats a missing site member search query as empty and refuses it as too short', async () => {
    const backend = makeMockBackend();

    const response = await backend.fetch('/-/api/v1/sites/team-aurora/website/members/search', {
      method: 'GET',
    });

    expect(response.status).toBe(422);
  });

  it('refuses revoking an unknown key', async () => {
    const backend = makeMockBackend();

    const response = await backend.fetch('/-/api/v1/sites/team-aurora/website/keys/geen-sleutel', {
      method: 'DELETE',
    });

    expect(response.status).toBe(404);
  });
});

describe('site repository: defaults', () => {
  it('does not find a "prive" repository without ids, and links it with them', async () => {
    const backend = makeMockBackend();
    const link = (extra: Record<string, unknown>) =>
      backend.fetch('/-/api/v1/sites/team-aurora/website/repository', {
        method: 'PUT',
        body: JSON.stringify({ provider: 'github', owner: 'team-aurora', repo: 'prive-site', ...extra }),
      });

    const notFound = await link({});
    expect(notFound.status).toBe(422);
    expect(((await notFound.json()) as { code: string }).code).toBe('REPOSITORY_NOT_FOUND');

    const linked = await link({ repositoryId: 5005, ownerId: 6006 });
    expect(linked.status).toBe(200);
    expect(await linked.json()).toMatchObject({
      repo: 'prive-site',
      repositoryId: 5005,
      ownerId: 6006,
      idsConfirmed: false,
    });
  });

  it('confirms the ids of a repository the lookup finds', async () => {
    const response = await makeMockBackend().fetch('/-/api/v1/sites/team-aurora/website/repository', {
      method: 'PUT',
      body: JSON.stringify({ provider: 'github', owner: 'team-aurora', repo: 'openbaar' }),
    });
    expect(response.status).toBe(200);
    expect(await response.json()).toMatchObject({ repo: 'openbaar', idsConfirmed: true });
  });

  it('refuses ids that are incomplete or not positive whole numbers', async () => {
    for (const ids of [{ repositoryId: 5005 }, { ownerId: 6006 }, { repositoryId: 0, ownerId: 6006 }, { repositoryId: 1.5, ownerId: 6006 }]) {
      const response = await makeMockBackend().fetch('/-/api/v1/sites/team-aurora/website/repository', {
        method: 'PUT',
        body: JSON.stringify({ provider: 'github', owner: 'team-aurora', repo: 'prive-site', ...ids }),
      });
      expect(response.status).toBe(422);
      expect(((await response.json()) as { code: string }).code).toBe('REPOSITORY_IDS_INVALID');
    }
  });

  it('refuses linking a repository without owner or repo', async () => {
    const backend = makeMockBackend();

    const response = await backend.fetch('/-/api/v1/sites/team-aurora/website/repository', {
      method: 'PUT',
      body: JSON.stringify({ provider: 'github' }),
    });

    expect(response.status).toBe(422);
  });

  it('refuses a Forgejo repository without a host', async () => {
    const backend = makeMockBackend();

    const response = await backend.fetch('/-/api/v1/sites/team-aurora/website/repository', {
      method: 'PUT',
      body: JSON.stringify({ provider: 'forgejo', owner: 'team-aurora', repo: 'website' }),
    });

    expect(response.status).toBe(422);
    const body = (await response.json()) as { code: string };
    expect(body.code).toBe('HOST_NOT_ALLOWED');
  });

  it('allows a Forgejo host only when it is exactly an allowed one', async () => {
    const link = (host: string) =>
      makeMockBackend().fetch('/-/api/v1/sites/team-aurora/website/repository', {
        method: 'PUT',
        body: JSON.stringify({ provider: 'forgejo', host, owner: 'team-aurora', repo: 'website' }),
      });

    expect((await link('https://code.overheid.nl')).status).toBe(200);
    for (const nearMiss of [
      'https://code.overheid.nl.evil.example',
      'https://evil.example/https://code.overheid.nl',
      'https://code.overheid',
      'https://github.com',
    ]) {
      expect((await link(nearMiss)).status, nearMiss).toBe(422);
    }
  });
});

describe('derived member rows: edge cases', () => {
  it('falls back to the identifier when a site role belongs to nobody known', async () => {
    const backend = makeMockBackend();
    backend.data.siteRoles.push({
      groupSlug: 'team-aurora',
      siteSlug: 'website',
      identifier: 'spook@voorbeeld.nl',
      role: 'reader',
    });

    const response = await backend.fetch('/-/api/v1/sites/team-aurora/website/members', { method: 'GET' });

    expect(response.status).toBe(200);
    const body = (await response.json()) as Array<{
      identifier: string;
      memberId: string;
      name: string;
      email: string;
      effectiveRole: string;
    }>;
    const row = body.find((l) => l.identifier === 'spook@voorbeeld.nl');
    expect(row).toMatchObject({
      memberId: '',
      name: 'spook@voorbeeld.nl',
      email: 'spook@voorbeeld.nl',
      effectiveRole: 'reader',
    });
  });

  it('falls back to the site slug as title when a member holds a role on a since-removed site', async () => {
    const backend = makeMockBackend();
    backend.data.siteRoles.push({
      groupSlug: 'team-aurora',
      siteSlug: 'verdwenen',
      identifier: 'ada@voorbeeld.nl',
      role: 'editor',
    });

    const response = await backend.fetch('/-/api/v1/groups/team-aurora/members', { method: 'GET' });

    expect(response.status).toBe(200);
    const body = (await response.json()) as Array<{
      identifier: string;
      siteRoles: Array<{ siteSlug: string; siteTitle: string }>;
    }>;
    const ada = body.find((l) => l.identifier === 'ada@voorbeeld.nl');
    const removedSite = ada?.siteRoles.find((r) => r.siteSlug === 'verdwenen');
    expect(removedSite?.siteTitle).toBe('verdwenen');
  });

  it('breaks a tie between equally-ranked search results by email', async () => {
    const backend = makeMockBackend();
    backend.data.members.push(
      {
        id: 'lid-dup-2',
        ssoSubject: 'twee',
        email: 'z-tweede@voorbeeld.nl',
        name: 'Dubbele Naam',
        platformRole: 'member',
        status: 'active',
        createdAt: '2026-01-01T00:00:00Z',
        lastLoginAt: null,
      },
      {
        id: 'lid-dup-1',
        ssoSubject: 'een',
        email: 'a-eerste@voorbeeld.nl',
        name: 'Dubbele Naam',
        platformRole: 'member',
        status: 'active',
        createdAt: '2026-01-01T00:00:00Z',
        lastLoginAt: null,
      },
    );

    const response = await backend.fetch('/-/api/v1/groups/team-aurora/members/search?q=Dubbele', {
      method: 'GET',
    });

    expect(response.status).toBe(200);
    const body = (await response.json()) as Array<{ email: string }>;
    const emails = body.map((l) => l.email);
    const first = emails.indexOf('a-eerste@voorbeeld.nl');
    const second = emails.indexOf('z-tweede@voorbeeld.nl');
    expect(first).toBeGreaterThanOrEqual(0);
    expect(second).toBeGreaterThan(first);
  });
});

describe('groups: creator not logged in', () => {
  it('creates a group without a creator membership when nobody is logged in', async () => {
    const backend = makeMockBackend();
    backend.data.loggedInMemberId = null;

    const response = await backend.fetch('/-/api/v1/groups', {
      method: 'POST',
      body: JSON.stringify({ slug: 'los-groepje', name: 'Los groepje' }),
    });

    expect(response.status).toBe(201);
    expect(backend.data.groupMembers.some((l) => l.groupSlug === 'los-groepje')).toBe(false);
  });
});

describe('mockFetch: unmatched methods fall through to the route 404', () => {
  it.each([
    ['PUT', '/-/api/v1/groups/team-aurora/members'],
    ['PUT', '/-/api/v1/me/cli-sessions/extra'],
    ['PUT', '/-/api/v1/sites/team-aurora/website/members'],
    ['DELETE', '/-/api/v1/sites/team-aurora/website/invitees'],
    ['PUT', '/-/api/v1/sites/team-aurora/website/keys'],
    ['PATCH', '/-/api/v1/sites/team-aurora/website/repository'],
    ['GET', '/-/api/v1/sites/team-aurora/website/deploys'],
  ])('answers %s %s with a 404, having matched no route for it', async (method, path) => {
    const backend = makeMockBackend();

    const response = await backend.fetch(path, { method });

    expect(response.status).toBe(404);
    const body = (await response.json()) as { title: string };
    expect(body.title).toBe('Onbekend eindpunt');
  });

  it('answers an unrecognised device-authorization action with a 404', async () => {
    const backend = makeMockBackend();

    const response = await backend.fetch('/-/api/v1/cli/device-authorizations/onbekend', {
      method: 'POST',
      body: JSON.stringify({ userCode: 'ABCD-EFGH' }),
    });

    expect(response.status).toBe(404);
    const body = (await response.json()) as { title: string };
    expect(body.title).toBe('Onbekend eindpunt');
  });

  it('answers an unrecognised platform-members sub-route with a 404', async () => {
    const backend = makeMockBackend();

    const response = await backend.fetch('/-/api/v1/platform/members/lid-1', { method: 'DELETE' });

    expect(response.status).toBe(404);
    const body = (await response.json()) as { title: string };
    expect(body.title).toBe('Onbekend eindpunt');
  });
});

describe('installMock', () => {
  const originalFetch = globalThis.fetch;

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it('replaces globalThis.fetch with the mock, and restores it again', () => {
    const backend = makeMockBackend();

    const restore = installMock(backend);
    expect(globalThis.fetch).toBe(backend.fetch);

    restore();
    expect(globalThis.fetch).toBe(originalFetch);
  });

  it('builds its own backend when none is given', () => {
    const restore = installMock();
    expect(globalThis.fetch).not.toBe(originalFetch);

    restore();
    expect(globalThis.fetch).toBe(originalFetch);
  });
});
