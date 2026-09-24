/**
 * In-memory mock of the session API (`/-/api/v1`), for use in vitest and
 * (via `VITE_MOCK_API=true`, see main.ts) in `npm run dev` as long as the
 * real backend is not there yet. Every call goes through `fetch` itself (no
 * serialisation boundary), so multipart bodies stay real `FormData`
 * instances.
 *
 * `makeMockBackend()` returns an isolated instance with its own state, so
 * tests cannot affect each other. `installMock()` replaces
 * `globalThis.fetch` and returns a restore function.
 */
import type {
  CliSession,
  DeviceAuthorization,
  Group,
  GroupDetail,
  GroupMember,
  Invitee,
  Key,
  KeyCreated,
  Me,
  Member,
  MemberLanguage,
  MemberSuggestion,
  MyGroupRole,
  MySiteRole,
  Overview,
  PlatformRole,
  Preview,
  RepositoryProvider,
  Role,
  Site,
  SiteMember,
  SiteRepository,
  Version,
  Access,
  AccessBase,
} from './types';

/** Content of the mock's `/me`, matching a real CI setup for local dev. */
const MOCK_CI_FORGEJO_HOSTS = ['https://code.overheid.nl'];
const MOCK_CI_AUDIENCE = 'https://plak.test';

// Mirrors constants.py, so the mock rejects exactly what the real
// backend rejects.
const SLUG_RE = /^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$/;
const RESERVED_SLUGS = new Set(['admin', 'robots.txt', 'favicon.ico', '.well-known', 'cli-koppelen']);
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

// Mirrors backend/src/plak/expiry.py, so the mock rejects and defaults
// exactly what the real backend does.
const DEFAULT_VALIDITY_DAYS = 90;
const MAX_VALIDITY_DAYS = 365;

type ExpiryResolution = { ok: true; expiresAt: string } | { ok: false; response: Response };

function resolveExpiry(requested: string | null): ExpiryResolution {
  const now = Date.now();
  if (requested === null) {
    return { ok: true, expiresAt: new Date(now + DEFAULT_VALIDITY_DAYS * 24 * 60 * 60 * 1000).toISOString() };
  }
  const requestedMs = new Date(requested).getTime();
  if (requestedMs <= now) {
    return {
      ok: false,
      response: problem(422, 'Onverwerkbare invoer', 'De vervaldatum ligt in het verleden.', 'EXPIRY_IN_PAST'),
    };
  }
  if (requestedMs > now + MAX_VALIDITY_DAYS * 24 * 60 * 60 * 1000) {
    return {
      ok: false,
      response: problem(
        422,
        'Onverwerkbare invoer',
        `De vervaldatum mag hoogstens ${MAX_VALIDITY_DAYS} dagen vooruit liggen.`,
        'EXPIRY_TOO_FAR',
      ),
    };
  }
  return { ok: true, expiresAt: requested };
}

/**
 * Content origin the mock hands out in `/me`. Deliberately a different host
 * from the tests' admin origin (https://plak.test), so that a link built on
 * `window.location.origin` by accident stands out in the tests.
 */
export const MOCK_CONTENT_BASE = 'https://sites.plak.test';

/** A role on one site, as `site_members` holds it in the backend. */
interface MockSiteRole {
  groupSlug: string;
  siteSlug: string;
  identifier: string;
  role: Role;
}

interface MockData {
  /** id of the logged-in member in `members`, or null for an anonymous visitor. */
  loggedInMemberId: string | null;
  members: Member[];
  groups: Group[];
  groupMembers: GroupMember[];
  /** Roles that hold on one site; the members list of a site is derived from these plus the group roles. */
  siteRoles: MockSiteRole[];
  sites: Site[];
  versions: Version[];
  previews: Preview[];
  invitees: Invitee[];
  keys: Key[];
  repositories: SiteRepository[];
  deviceAuthorizations: MockDeviceAuthorization[];
  cliSessions: CliSession[];
  /** The interface language on the logged-in account; null is "follow my browser". */
  myLanguage: MemberLanguage | null;
}

/** A device-flow authorization pending approval, as the CLI creates it. */
interface MockDeviceAuthorization extends DeviceAuthorization {
  status: 'pending' | 'approved' | 'denied';
}

function now(): string {
  return new Date().toISOString();
}

const DUTCH_MONTHS = [
  'januari',
  'februari',
  'maart',
  'april',
  'mei',
  'juni',
  'juli',
  'augustus',
  'september',
  'oktober',
  'november',
  'december',
];

/** Mirrors the server's fallback for a key created without a label (access/keys.py). */
function defaultKeyLabel(): string {
  const moment = new Date();
  return `Link van ${moment.getDate()} ${DUTCH_MONTHS[moment.getMonth()]}`;
}

function defaultData(): MockData {
  const firstDeploy = '2026-07-10T09:00:00.000Z';
  const lastPublishedAt = '2026-07-17T14:32:00.000Z';
  return {
    loggedInMemberId: 'lid-1',
    members: [
      {
        id: 'lid-1',
        ssoSubject: 'dev-beheerder',
        email: 'beheerder@voorbeeld.nl',
        name: 'Bea Heerder',
        platformRole: 'admin',
        status: 'active',
        isBootstrap: true,
        createdAt: '2026-01-08T09:12:00Z',
        lastLoginAt: lastPublishedAt,
      },
      {
        id: 'lid-2',
        ssoSubject: 'weg-lid',
        email: 'weg@voorbeeld.nl',
        name: 'Wim Weg',
        platformRole: 'member',
        status: 'deactivated',
        createdAt: '2026-09-15T14:40:00Z',
        lastLoginAt: null,
      },
      // Three more so the three sort orders on the platform page actually
      // differ from each other in dev: alphabetically, by creation and by last
      // activity these five come out in three different sequences.
      {
        id: 'lid-3',
        ssoSubject: 'ada-redacteur',
        email: 'ada@voorbeeld.nl',
        name: 'Ada Vermeer',
        platformRole: 'member',
        status: 'active',
        createdAt: '2026-03-02T11:05:00Z',
        lastLoginAt: '2026-09-16T08:30:00Z',
      },
      {
        id: 'lid-4',
        ssoSubject: 'zoe-lezer',
        email: 'zoe@voorbeeld.nl',
        name: 'Zoë de Wit',
        platformRole: 'member',
        status: 'active',
        createdAt: '2026-02-19T16:20:00Z',
        lastLoginAt: '2026-06-01T12:00:00Z',
      },
      {
        id: 'lid-5',
        ssoSubject: 'oud-lid',
        email: 'oud@voorbeeld.nl',
        name: 'Karel Oud',
        platformRole: 'member',
        status: 'deactivated',
        createdAt: '2026-01-05T08:00:00Z',
        lastLoginAt: '2026-04-11T09:45:00Z',
      },
      // Four accounts without a role anywhere, so typing a few letters in the
      // toevoegveld actually turns up someone to pick. Three of them answer to
      // "ver", next to the Ada Vermeer above who is already a group member:
      // that is what the al-lid marking is there for.
      {
        id: 'lid-6',
        ssoSubject: 'sanne-vermeulen',
        email: 'sanne@voorbeeld.nl',
        name: 'Sanne Vermeulen',
        platformRole: 'member',
        status: 'active',
        createdAt: '2026-04-02T10:15:00Z',
        lastLoginAt: '2026-09-10T11:20:00Z',
      },
      {
        id: 'lid-7',
        ssoSubject: 'jamal-verhoeven',
        email: 'jamal@voorbeeld.nl',
        name: 'Jamal Verhoeven',
        platformRole: 'member',
        status: 'active',
        createdAt: '2026-05-14T13:40:00Z',
        lastLoginAt: '2026-08-28T15:05:00Z',
      },
      {
        id: 'lid-8',
        ssoSubject: 'iris-bakker',
        email: 'iris@voorbeeld.nl',
        name: 'Iris Bakker',
        platformRole: 'member',
        status: 'active',
        createdAt: '2026-06-21T09:30:00Z',
        lastLoginAt: '2026-09-01T07:55:00Z',
      },
      {
        id: 'lid-9',
        ssoSubject: 'tom-de-jong',
        email: 'tom@voorbeeld.nl',
        name: 'Tom de Jong',
        platformRole: 'member',
        status: 'active',
        createdAt: '2026-07-08T16:00:00Z',
        lastLoginAt: '2026-07-30T10:10:00Z',
      },
    ],
    groups: [{ slug: 'nldd', name: 'NLDD', defaultAccess: { base: 'public', keys: false, invitees: false } }],
    groupMembers: [
      {
        groupSlug: 'nldd',
        memberId: 'lid-1',
        identifier: 'dev-beheerder',
        name: 'Bea Heerder',
        email: 'beheerder@voorbeeld.nl',
        role: 'admin',
      },
      // The three roles side by side, so a members list in dev shows what the
      // column is for.
      {
        groupSlug: 'nldd',
        memberId: 'lid-3',
        identifier: 'ada@voorbeeld.nl',
        name: 'Ada Vermeer',
        email: 'ada@voorbeeld.nl',
        role: 'editor',
      },
      {
        groupSlug: 'nldd',
        memberId: 'lid-4',
        identifier: 'zoe@voorbeeld.nl',
        name: 'Zoë de Wit',
        email: 'zoe@voorbeeld.nl',
        role: 'reader',
      },
    ],
    siteRoles: [
      // The two cases the site members list exists for: someone who reaches
      // this one site without being in the group, and a group member whose
      // site role widens what the group gives them.
      {
        groupSlug: 'nldd',
        siteSlug: 'website',
        identifier: 'weg@voorbeeld.nl',
        role: 'editor',
      },
      {
        groupSlug: 'nldd',
        siteSlug: 'website',
        identifier: 'zoe@voorbeeld.nl',
        role: 'admin',
      },
    ],
    sites: [
      {
        groupSlug: 'nldd',
        slug: 'website',
        title: 'NLDD website',
        access: { base: 'public', keys: false, invitees: false },
        externalSources: true,
        liveVersionId: 'versie-1',
        createdBy: 'dev-beheerder',
        hasLiveVersion: true,
        lastPublishedAt: lastPublishedAt,
        previewCount: 1,
      },
    ],
    versions: [
      {
        id: 'versie-1',
        siteSlug: 'website',
        groupSlug: 'nldd',
        target: 'live',
        storageRef: 'nldd/website/versie-1',
        origin: 'action',
        createdByMember: null,
        createdByName: null,
        createdByRepository: 'github.com/nldd/website',
        createdAt: lastPublishedAt,
        isLive: true,
      },
      {
        id: 'versie-0',
        siteSlug: 'website',
        groupSlug: 'nldd',
        target: 'live',
        storageRef: 'nldd/website/versie-0',
        origin: 'upload',
        createdByMember: 'dev-beheerder',
        createdByName: 'Bea Heerder',
        createdByRepository: null,
        createdAt: firstDeploy,
        isLive: false,
      },
      {
        id: 'versie-preview-42',
        siteSlug: 'website',
        groupSlug: 'nldd',
        target: 'preview',
        storageRef: 'nldd/website/_preview/pr-42',
        origin: 'action',
        createdByMember: null,
        createdByName: null,
        createdByRepository: 'github.com/nldd/website',
        createdAt: lastPublishedAt,
        isLive: false,
      },
    ],
    previews: [
      {
        siteSlug: 'website',
        groupSlug: 'nldd',
        ref: 'pr-42',
        versionId: 'versie-preview-42',
        accessOverride: { base: 'nobody', keys: false, invitees: true },
        lastUpdatedAt: lastPublishedAt,
        expiresAt: '2026-08-16T14:32:00.000Z',
        url: '/nldd/website/_preview/pr-42/',
      },
    ],
    invitees: [
      {
        id: 'genodigde-1',
        siteSlug: 'website',
        groupSlug: 'nldd',
        identifier: 'reviewer@voorbeeld.nl',
        addedBy: 'dev-beheerder',
        addedAt: firstDeploy,
      },
    ],
    keys: [
      {
        siteSlug: 'website',
        groupSlug: 'nldd',
        label: 'Demo voor stakeholders',
        selector: 'sel-abc123',
        status: 'active',
        createdAt: firstDeploy,
        expiresAt: null,
      },
    ],
    repositories: [
      {
        groupSlug: 'nldd',
        siteSlug: 'website',
        provider: 'github',
        host: 'https://github.com',
        owner: 'nldd',
        repo: 'website',
        repositoryId: 123456,
        ownerId: 654321,
        liveBranch: 'main',
        createdBy: 'Bea Heerder',
        createdAt: firstDeploy,
      },
    ],
    deviceAuthorizations: [
      {
        userCode: 'ABCD-EFGH',
        clientName: 'plak-cli',
        ipTruncated: '203.0.113.0/24',
        sameNetwork: true,
        createdAt: lastPublishedAt,
        expiresAt: new Date(Date.now() + 5 * 60 * 1000).toISOString(),
        status: 'pending',
      },
    ],
    cliSessions: [
      {
        id: 'cli-sessie-1',
        clientName: 'plak-cli',
        createdAt: firstDeploy,
        lastUsedAt: lastPublishedAt,
        expiresAt: new Date(Date.now() + 30 * 24 * 60 * 60 * 1000).toISOString(),
      },
    ],
    myLanguage: null,
  };
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  });
}

function empty(status: number): Response {
  return new Response(null, { status });
}

function problem(status: number, title: string, detail?: string, code?: string): Response {
  return new Response(
    JSON.stringify({ type: 'about:blank', title, status, detail, code }),
    { status, headers: { 'content-type': 'application/problem+json' } },
  );
}

function siteDerived(data: MockData, site: Site): Site {
  const previewCount = data.previews.filter(
    (p) => p.groupSlug === site.groupSlug && p.siteSlug === site.slug,
  ).length;
  return { ...site, previewCount };
}

/** Narrowest first, as in the backend's Role enum. */
const ROLE_ORDER: Role[] = ['reader', 'editor', 'admin'];

function widestRole(first: Role | null, second: Role | null): Role {
  return ROLE_ORDER.indexOf(first ?? 'reader') >= ROLE_ORDER.indexOf(second ?? 'reader')
    ? (first ?? second)!
    : (second ?? first)!;
}

/**
 * Everyone who can reach one site, like the backend builds it: the group
 * members plus whoever has a role on this site, widest effective role first
 * and alphabetically by e-mail within a role.
 */
function siteMemberRows(data: MockData, groupSlug: string, siteSlug: string): SiteMember[] {
  const groupRows = data.groupMembers.filter((l) => l.groupSlug === groupSlug);
  const siteRows = data.siteRoles.filter(
    (r) => r.groupSlug === groupSlug && r.siteSlug === siteSlug,
  );
  const identifiers = [
    ...new Set([...groupRows.map((l) => l.identifier), ...siteRows.map((r) => r.identifier)]),
  ];
  return identifiers
    .map((identifier) => {
      const groupRow = groupRows.find((l) => l.identifier === identifier);
      const siteRow = siteRows.find((r) => r.identifier === identifier);
      const account = data.members.find(
        (l) => l.email === identifier || l.ssoSubject === identifier,
      );
      const groupRole = groupRow?.role ?? null;
      const siteRole = siteRow?.role ?? null;
      return {
        groupSlug,
        siteSlug,
        memberId: groupRow?.memberId ?? account?.id ?? '',
        identifier,
        name: groupRow?.name ?? account?.name ?? identifier,
        email: groupRow?.email ?? account?.email ?? identifier,
        groupRole,
        siteRole,
        effectiveRole: widestRole(groupRole, siteRole),
      };
    })
    .sort(
      (a, b) =>
        ROLE_ORDER.indexOf(b.effectiveRole) - ROLE_ORDER.indexOf(a.effectiveRole) ||
        a.email.localeCompare(b.email),
    );
}

/**
 * Roles the given member holds, in the same shape as the backend's `/me`
 * (only sites with a site role of their own go into `siteRoles`; elsewhere
 * the group role in `groupRoles` stands on its own).
 */
function myRoles(data: MockData, member: Member): { groupRoles: MyGroupRole[]; siteRoles: MySiteRole[] } {
  const groupRows = data.groupMembers.filter(
    (l) => l.identifier === member.email || l.identifier === member.ssoSubject,
  );
  const groupRoles = groupRows.map((row) => ({ groupSlug: row.groupSlug, role: row.role }));
  const siteRows = data.siteRoles.filter(
    (r) => r.identifier === member.email || r.identifier === member.ssoSubject,
  );
  const siteRoles = siteRows.map((row) => {
    const groupRole = groupRows.find((g) => g.groupSlug === row.groupSlug)?.role ?? null;
    return {
      groupSlug: row.groupSlug,
      siteSlug: row.siteSlug,
      role: row.role,
      effectiveRole: widestRole(groupRole, row.role),
    };
  });
  return { groupRoles, siteRoles };
}

/** Fewest characters the search accepts; below it the backend refuses. */
const SEARCH_MIN_LENGTH = 2;
/** A shortlist, not an export of the whole directory. */
const SEARCH_LIMIT = 10;

/**
 * The member search behind both toevoegvelden: active accounts whose name or
 * e-mail contains the query, by name and then e-mail, capped. `taken` holds
 * the identifiers and addresses that already have a role in this group or on
 * this site, which marks them in the list instead of leaving them out.
 */
function memberSuggestions(
  data: MockData,
  query: string,
  taken: Set<string>,
): MemberSuggestion[] {
  const needle = query.toLowerCase();
  return data.members
    .filter((l) => l.status === 'active')
    .filter(
      (l) => l.name.toLowerCase().includes(needle) || l.email.toLowerCase().includes(needle),
    )
    .sort((a, b) => a.name.localeCompare(b.name) || a.email.localeCompare(b.email))
    .slice(0, SEARCH_LIMIT)
    .map((l) => ({
      identifier: l.email,
      name: l.name,
      email: l.email,
      alreadyMember: taken.has(l.email) || taken.has(l.ssoSubject),
    }));
}

function searchTooShort(): Response {
  return problem(
    422,
    'Zoekterm te kort',
    'Typ minstens twee tekens om op te zoeken.',
    'SEARCH_TOO_SHORT',
  );
}

let sequenceNumber = 0;
function nextSequenceNumber(): number {
  sequenceNumber += 1;
  return sequenceNumber;
}
function nextId(prefix: string): string {
  return `${prefix}-${nextSequenceNumber()}`;
}

export interface MockBackend {
  fetch: typeof fetch;
  data: MockData;
}

/**
 * Builds an isolated mock backend. `seed` optionally supplies a starting
 * point of your own; it defaults to `defaultData()` (representative demo
 * content).
 */
export function makeMockBackend(seed: MockData = defaultData()): MockBackend {
  const data = seed;

  async function mockFetch(input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> {
    const url = new URL(
      typeof input === 'string' ? input : input instanceof URL ? input.toString() : input.url,
      'https://plak.test',
    );
    const method = (init.method ?? 'GET').toUpperCase();
    const path = url.pathname;
    const segments = path.split('/').filter(Boolean).map(decodeURIComponent);
    // ['-', 'api', 'v1', ...rest]
    const rest = segments.slice(3);

    const readJson = (): Record<string, unknown> =>
      init.body ? (JSON.parse(init.body as string) as Record<string, unknown>) : {};

    /** Base plus extras, with the backend's defaults for a field left out. */
    const readAccess = (): Access => {
      const body = readJson();
      return {
        base: (body.base ?? 'public') as AccessBase,
        keys: Boolean(body.keys),
        invitees: Boolean(body.invitees),
      };
    };

    const findSite = (groupSlug: string, siteSlug: string): Site | undefined =>
      data.sites.find((p) => p.groupSlug === groupSlug && p.slug === siteSlug);

    const findGroup = (slug: string): Group | undefined =>
      data.groups.find((g) => g.slug === slug);

    // GET /me
    if (method === 'GET' && rest.length === 1 && rest[0] === 'me') {
      const loggedIn = data.members.find((l) => l.id === data.loggedInMemberId);
      if (!loggedIn) {
        return problem(401, 'Niet ingelogd', 'Er is geen actieve sessie.');
      }
      const response: Me = {
        ...loggedIn,
        contentBaseUrl: MOCK_CONTENT_BASE,
        ...myRoles(data, loggedIn),
        ciForgejoHosts: MOCK_CI_FORGEJO_HOSTS,
        ciAudience: MOCK_CI_AUDIENCE,
        language: data.myLanguage,
      };
      return json(200, response);
    }

    // GET /overview
    if (method === 'GET' && rest.length === 1 && rest[0] === 'overview') {
      const overview: Overview = {
        groups: data.groups.map((g) => ({
          group: g,
          sites: data.sites
            .filter((p) => p.groupSlug === g.slug)
            .map((p) => siteDerived(data, p)),
        })),
      };
      return json(200, overview);
    }

    // POST /groups
    if (method === 'POST' && rest.length === 1 && rest[0] === 'groups') {
      const body = readJson();
      const slug = String(body.slug ?? '');
      const name = String(body.name ?? '');
      if (!SLUG_RE.test(slug) || RESERVED_SLUGS.has(slug)) {
        return problem(422, 'Ongeldige slug', `"${slug}" is geen geldige of toegestane slug.`);
      }
      if (findGroup(slug)) {
        return problem(409, 'Groep bestaat al', `Er bestaat al een groep met slug "${slug}".`);
      }
      const created: Group = {
        slug,
        name,
        defaultAccess: { base: 'site_team', keys: false, invitees: false },
      };
      data.groups.push(created);
      // Like the backend: the creator immediately becomes a group member.
      const creator = data.members.find((l) => l.id === data.loggedInMemberId);
      if (creator) {
        data.groupMembers.push({
          groupSlug: slug,
          memberId: creator.id,
          identifier: creator.email,
          name: creator.name,
          email: creator.email,
          // Like the backend: whoever makes the group is its beheerder.
          role: 'admin',
        });
      }
      return json(201, created);
    }

    // /groups/{group}...
    if (rest[0] === 'groups' && rest.length >= 2) {
      const groupSlug = rest[1]!;
      const groupRow = findGroup(groupSlug);

      // GET /groups/{group}
      if (method === 'GET' && rest.length === 2) {
        if (!groupRow) return problem(404, 'Onbekende groep', `Geen groep met slug "${groupSlug}".`);
        const detail: GroupDetail = {
          group: groupRow,
          sites: data.sites
            .filter((p) => p.groupSlug === groupSlug)
            .map((p) => siteDerived(data, p)),
          members: data.groupMembers.filter((l) => l.groupSlug === groupSlug),
        };
        return json(200, detail);
      }

      // DELETE /groups/{group}
      if (method === 'DELETE' && rest.length === 2) {
        if (!groupRow) return problem(404, 'Onbekende groep', `Geen groep met slug "${groupSlug}".`);
        if (data.sites.some((p) => p.groupSlug === groupSlug)) {
          return problem(409, 'Groep niet leeg', 'Verwijder eerst alle sites in deze groep.');
        }
        data.groups = data.groups.filter((g) => g.slug !== groupSlug);
        return empty(204);
      }

      // PUT /groups/{group}/default-access
      if (method === 'PUT' && rest.length === 3 && rest[2] === 'default-access') {
        if (!groupRow) return problem(404, 'Onbekende groep', `Geen groep met slug "${groupSlug}".`);
        groupRow.defaultAccess = readAccess();
        return json(200, groupRow);
      }

      // POST /groups/{group}/sites
      if (method === 'POST' && rest.length === 3 && rest[2] === 'sites') {
        if (!groupRow) return problem(404, 'Onbekende groep', `Geen groep met slug "${groupSlug}".`);
        const body = readJson();
        const slug = String(body.slug ?? '');
        const title = String(body.title ?? '');
        if (!SLUG_RE.test(slug)) {
          return problem(422, 'Ongeldige slug', `"${slug}" is geen geldige slug.`);
        }
        if (findSite(groupSlug, slug)) {
          return problem(409, 'Site bestaat al', `Er bestaat al een site met slug "${slug}" in deze groep.`);
        }
        const created: Site = {
          groupSlug,
          slug,
          title,
          access: { ...groupRow.defaultAccess },
          externalSources: true,
          liveVersionId: null,
          createdBy: 'dev-beheerder',
          hasLiveVersion: false,
          lastPublishedAt: null,
          previewCount: 0,
        };
        data.sites.push(created);
        return json(201, created);
      }

      // /groups/{group}/members
      if (rest.length === 3 && rest[2] === 'members') {
        if (!groupRow) return problem(404, 'Onbekende groep', `Geen groep met slug "${groupSlug}".`);
        if (method === 'GET') {
          return json(200, data.groupMembers.filter((l) => l.groupSlug === groupSlug));
        }
        if (method === 'POST') {
          const body = readJson();
          const identifier = String(body.identifier ?? '');
          if (!EMAIL_RE.test(identifier)) {
            return problem(422, 'Ongeldige identifier', 'Verwacht een e-mailadres.');
          }
          const member: GroupMember = {
            groupSlug,
            memberId: data.members.find((l) => l.email === identifier)?.id ?? identifier,
            identifier,
            name: identifier,
            email: identifier,
            role: (body.role as Role) ?? 'reader',
          };
          data.groupMembers.push(member);
          return json(201, member);
        }
      }
      // GET /groups/{group}/members/search
      if (method === 'GET' && rest.length === 4 && rest[2] === 'members' && rest[3] === 'search') {
        if (!groupRow) return problem(404, 'Onbekende groep', `Geen groep met slug "${groupSlug}".`);
        const query = (url.searchParams.get('q') ?? '').trim();
        if (query.length < SEARCH_MIN_LENGTH) return searchTooShort();
        const taken = new Set(
          data.groupMembers
            .filter((l) => l.groupSlug === groupSlug)
            .flatMap((l) => [l.identifier, l.email]),
        );
        return json(200, memberSuggestions(data, query, taken));
      }
      if (rest.length === 5 && rest[2] === 'members' && rest[4] === 'role' && method === 'PUT') {
        if (!groupRow) return problem(404, 'Onbekende groep', `Geen groep met slug "${groupSlug}".`);
        const memberId = decodeURIComponent(rest[3]!);
        const member = data.groupMembers.find(
          (l) => l.groupSlug === groupSlug && l.memberId === memberId,
        );
        if (!member) return problem(404, 'Onbekend lid', 'Dit lid zit niet in deze groep.');
        const body = readJson();
        member.role = body.role as Role;
        return json(200, member);
      }
      if (rest.length === 4 && rest[2] === 'members' && method === 'DELETE') {
        if (!groupRow) return problem(404, 'Onbekende groep', `Geen groep met slug "${groupSlug}".`);
        const memberId = rest[3]!;
        data.groupMembers = data.groupMembers.filter(
          (l) => !(l.groupSlug === groupSlug && l.memberId === memberId),
        );
        return empty(204);
      }
    }

    // PUT /me/language
    if (method === 'PUT' && rest.length === 2 && rest[0] === 'me' && rest[1] === 'language') {
      const body = readJson();
      const value = body.language;
      if (value !== null && value !== 'nl' && value !== 'en') {
        return problem(422, 'Onbekende taal', `"${String(value)}" is geen ondersteunde taal.`);
      }
      data.myLanguage = value as MemberLanguage | null;
      return empty(204);
    }

    // /me/cli-sessions
    if (rest[0] === 'me' && rest[1] === 'cli-sessions') {
      if (method === 'GET' && rest.length === 2) {
        return json(200, data.cliSessions);
      }
      if (method === 'DELETE' && rest.length === 3) {
        const id = rest[2]!;
        const before = data.cliSessions.length;
        data.cliSessions = data.cliSessions.filter((s) => s.id !== id);
        if (data.cliSessions.length === before) {
          return problem(404, 'Onbekend apparaat', `Geen gekoppeld apparaat met id "${id}".`, 'CLI_SESSION_UNKNOWN');
        }
        return empty(204);
      }
    }

    // /cli/device-authorizations/{lookup,approve,deny}
    if (rest[0] === 'cli' && rest[1] === 'device-authorizations' && method === 'POST' && rest.length === 3) {
      const action = rest[2]!;
      const body = readJson();
      const userCode = String(body.userCode ?? '')
        .toUpperCase()
        .replace(/[\s-]/g, '');
      const authorization = data.deviceAuthorizations.find(
        (a) => a.userCode.replace(/-/g, '') === userCode && a.status === 'pending',
      );
      if (!authorization || new Date(authorization.expiresAt).getTime() < Date.now()) {
        return problem(
          404,
          'Onbekende code',
          'Deze code is onbekend, verlopen of al gebruikt.',
          'USER_CODE_UNKNOWN',
        );
      }
      if (action === 'lookup') {
        const response: DeviceAuthorization = {
          userCode: authorization.userCode,
          clientName: authorization.clientName,
          ipTruncated: authorization.ipTruncated,
          sameNetwork: authorization.sameNetwork,
          createdAt: authorization.createdAt,
          expiresAt: authorization.expiresAt,
        };
        return json(200, response);
      }
      if (action === 'approve') {
        authorization.status = 'approved';
        data.cliSessions.push({
          id: nextId('cli-sessie'),
          clientName: authorization.clientName,
          createdAt: now(),
          lastUsedAt: null,
          expiresAt: new Date(Date.now() + 30 * 24 * 60 * 60 * 1000).toISOString(),
        });
        return empty(204);
      }
      if (action === 'deny') {
        authorization.status = 'denied';
        return empty(204);
      }
    }

    // /platform/members...
    if (rest[0] === 'platform' && rest[1] === 'members') {
      if (method === 'GET' && rest.length === 2) {
        return json(200, data.members);
      }
      if (method === 'POST' && rest.length === 4 && (rest[3] === '_activate' || rest[3] === '_deactivate')) {
        const member = data.members.find((l) => l.id === rest[2]);
        if (!member) return problem(404, 'Onbekend lid', `Geen lid met id "${rest[2]}".`);
        member.status = rest[3] === '_activate' ? 'active' : 'deactivated';
        return json(200, member);
      }
      if (method === 'PUT' && rest.length === 4 && rest[3] === 'platform-role') {
        const member = data.members.find((l) => l.id === rest[2]);
        if (!member) return problem(404, 'Onbekend lid', `Geen lid met id "${rest[2]}".`);
        // The three refusals the real backend makes (yourself, the last active
        // beheerder, the bootstrap account) are deliberately not mocked: the
        // dev mock has one beheerder and no session of its own to compare
        // against, so it would refuse everything.
        const roleBody = readJson() as { platformRole: PlatformRole };
        member.platformRole = roleBody.platformRole;
        return json(200, member);
      }
    }

    // /sites/{group}/{site}...
    if (rest[0] === 'sites' && rest.length >= 3) {
      const groupSlug = rest[1]!;
      const siteSlug = rest[2]!;
      const siteRow = findSite(groupSlug, siteSlug);
      const siteNotFound = (): Response =>
        problem(404, 'Onbekend site', `Geen site "${siteSlug}" in groep "${groupSlug}".`);

      if (method === 'DELETE' && rest.length === 3) {
        if (!siteRow) return siteNotFound();
        data.sites = data.sites.filter((p) => !(p.groupSlug === groupSlug && p.slug === siteSlug));
        data.versions = data.versions.filter((v) => !(v.groupSlug === groupSlug && v.siteSlug === siteSlug));
        data.previews = data.previews.filter((p) => !(p.groupSlug === groupSlug && p.siteSlug === siteSlug));
        data.invitees = data.invitees.filter(
          (g) => !(g.groupSlug === groupSlug && g.siteSlug === siteSlug),
        );
        data.keys = data.keys.filter(
          (s) => !(s.groupSlug === groupSlug && s.siteSlug === siteSlug),
        );
        data.siteRoles = data.siteRoles.filter(
          (r) => !(r.groupSlug === groupSlug && r.siteSlug === siteSlug),
        );
        return empty(204);
      }

      if (method === 'PUT' && rest.length === 4 && rest[3] === 'external-sources') {
        if (!siteRow) return siteNotFound();
        const body = readJson();
        siteRow.externalSources = Boolean(body.externalSources);
        return json(200, siteDerived(data, siteRow));
      }

      if (method === 'PUT' && rest.length === 4 && rest[3] === 'access') {
        if (!siteRow) return siteNotFound();
        siteRow.access = readAccess();
        return json(200, siteDerived(data, siteRow));
      }

      // /sites/{group}/{site}/members
      if (rest.length === 4 && rest[3] === 'members') {
        if (!siteRow) return siteNotFound();
        if (method === 'GET') {
          return json(200, siteMemberRows(data, groupSlug, siteSlug));
        }
        if (method === 'POST') {
          const body = readJson();
          const identifier = String(body.identifier ?? '');
          if (!EMAIL_RE.test(identifier)) {
            return problem(422, 'Ongeldige identifier', 'Verwacht een e-mailadres.');
          }
          // Like the backend: a site role hangs on an existing account, so
          // someone has to have logged in on the admin once themselves.
          if (!data.members.some((l) => l.email === identifier)) {
            return problem(
              404,
              'Onbekend lid',
              'Onbekend lid; diegene moet eerst zelf inloggen op het beheer.',
            );
          }
          if (
            data.siteRoles.some(
              (r) =>
                r.groupSlug === groupSlug && r.siteSlug === siteSlug && r.identifier === identifier,
            )
          ) {
            return problem(409, 'Al lid van deze site', 'Dit lid heeft al een rol op deze site.');
          }
          data.siteRoles.push({
            groupSlug,
            siteSlug,
            identifier,
            role: (body.role as Role) ?? 'reader',
          });
          const added = siteMemberRows(data, groupSlug, siteSlug).find(
            (l) => l.identifier === identifier,
          );
          return json(201, added);
        }
      }
      // GET /sites/{group}/{site}/members/search
      if (method === 'GET' && rest.length === 5 && rest[3] === 'members' && rest[4] === 'search') {
        if (!siteRow) return siteNotFound();
        const query = (url.searchParams.get('q') ?? '').trim();
        if (query.length < SEARCH_MIN_LENGTH) return searchTooShort();
        const taken = new Set(
          siteMemberRows(data, groupSlug, siteSlug).flatMap((l) => [l.identifier, l.email]),
        );
        return json(200, memberSuggestions(data, query, taken));
      }
      if (rest.length === 6 && rest[3] === 'members' && rest[5] === 'role' && method === 'PUT') {
        if (!siteRow) return siteNotFound();
        const memberId = rest[4]!;
        const identifier = siteMemberRows(data, groupSlug, siteSlug).find(
          (l) => l.memberId === memberId,
        )?.identifier;
        const row = data.siteRoles.find(
          (r) =>
            r.groupSlug === groupSlug && r.siteSlug === siteSlug && r.identifier === identifier,
        );
        if (!row) {
          return problem(404, 'Geen siterol', 'Dit lid heeft geen rol op deze site.');
        }
        row.role = readJson().role as Role;
        const changed = siteMemberRows(data, groupSlug, siteSlug).find(
          (l) => l.memberId === memberId,
        );
        return json(200, changed);
      }
      if (rest.length === 5 && rest[3] === 'members' && method === 'DELETE') {
        if (!siteRow) return siteNotFound();
        const memberId = rest[4]!;
        const identifier = siteMemberRows(data, groupSlug, siteSlug).find(
          (l) => l.memberId === memberId,
        )?.identifier;
        const before = data.siteRoles.length;
        data.siteRoles = data.siteRoles.filter(
          (r) =>
            !(r.groupSlug === groupSlug && r.siteSlug === siteSlug && r.identifier === identifier),
        );
        if (data.siteRoles.length === before) {
          return problem(404, 'Geen siterol', 'Dit lid heeft geen rol op deze site.');
        }
        return empty(204);
      }

      // invitees
      if (rest.length === 4 && rest[3] === 'invitees') {
        if (!siteRow) return siteNotFound();
        if (method === 'GET') {
          return json(200, data.invitees.filter((g) => g.groupSlug === groupSlug && g.siteSlug === siteSlug));
        }
        if (method === 'POST') {
          const body = readJson();
          const identifier = String(body.identifier ?? '').toLowerCase();
          if (!EMAIL_RE.test(identifier)) {
            return problem(422, 'Ongeldige identifier', 'Verwacht een e-mailadres.');
          }
          if (data.invitees.some((g) => g.groupSlug === groupSlug && g.siteSlug === siteSlug && g.identifier === identifier)) {
            return problem(409, 'Al genodigd', `"${identifier}" staat al op de genodigdenlijst.`);
          }
          const invitee: Invitee = {
            id: `genodigde-${data.invitees.length + 1}`,
            siteSlug,
            groupSlug,
            identifier,
            addedBy: 'dev-beheerder',
            addedAt: now(),
          };
          data.invitees.push(invitee);
          return json(201, invitee);
        }
      }
      if (rest.length === 5 && rest[3] === 'invitees' && method === 'DELETE') {
        if (!siteRow) return siteNotFound();
        const inviteeId = rest[4]!;
        data.invitees = data.invitees.filter(
          (g) => !(g.groupSlug === groupSlug && g.siteSlug === siteSlug && g.id === inviteeId),
        );
        return empty(204);
      }

      // secret-link keys
      if (rest.length === 4 && rest[3] === 'keys') {
        if (!siteRow) return siteNotFound();
        if (method === 'GET') {
          return json(200, data.keys.filter((s) => s.groupSlug === groupSlug && s.siteSlug === siteSlug));
        }
        if (method === 'POST') {
          const body = readJson();
          const rawLabel = typeof body.label === 'string' ? body.label.trim() : '';
          const label = rawLabel || defaultKeyLabel();
          const expiry = resolveExpiry((body.expiresAt as string | null) ?? null);
          if (!expiry.ok) return expiry.response;
          const selector = nextId('sel');
          const key: Key = {
            siteSlug,
            groupSlug,
            label,
            selector,
            status: 'active',
            createdAt: now(),
            expiresAt: expiry.expiresAt,
          };
          data.keys.push(key);
          const response: KeyCreated = { key, value: `${selector}.${nextId('ver')}` };
          return json(201, response);
        }
      }
      if (rest.length === 5 && rest[3] === 'keys' && method === 'DELETE') {
        if (!siteRow) return siteNotFound();
        const selector = rest[4]!;
        const key = data.keys.find(
          (s) => s.groupSlug === groupSlug && s.siteSlug === siteSlug && s.selector === selector,
        );
        if (!key) return problem(404, 'Onbekende sleutel', `Geen sleutel met selector "${selector}".`);
        key.status = 'revoked';
        return empty(204);
      }

      // site repository (trusted CI publishing)
      if (rest.length === 4 && rest[3] === 'repository') {
        if (!siteRow) return siteNotFound();
        const existing = data.repositories.find(
          (r) => r.groupSlug === groupSlug && r.siteSlug === siteSlug,
        );
        if (method === 'GET') {
          if (!existing) {
            return problem(
              404,
              'Geen gekoppeld repository',
              'Er is nog geen repository aan deze site gekoppeld.',
              'REPOSITORY_NOT_SET',
            );
          }
          return json(200, existing);
        }
        if (method === 'PUT') {
          const body = readJson();
          const provider = body.provider as RepositoryProvider;
          const owner = String(body.owner ?? '');
          const repo = String(body.repo ?? '');
          const host = provider === 'forgejo' ? String(body.host ?? '') : 'https://github.com';
          if (!owner || !repo) {
            return problem(422, 'Ongeldig repository', 'Vul zowel eigenaar als repository in.', 'REPOSITORY_INVALID');
          }
          if (provider === 'forgejo' && !MOCK_CI_FORGEJO_HOSTS.includes(host)) {
            return problem(422, 'Host niet toegestaan', `"${host}" is geen toegestane Forgejo-host.`, 'HOST_NOT_ALLOWED');
          }
          const liveBranch = (body.liveBranch as string | null) ?? null;
          const linked: SiteRepository = {
            groupSlug,
            siteSlug,
            provider,
            host,
            owner,
            repo,
            repositoryId: existing?.repositoryId ?? nextSequenceNumber(),
            ownerId: existing?.ownerId ?? nextSequenceNumber(),
            liveBranch,
            createdBy: 'Bea Heerder',
            createdAt: existing?.createdAt ?? now(),
          };
          data.repositories = [
            ...data.repositories.filter((r) => !(r.groupSlug === groupSlug && r.siteSlug === siteSlug)),
            linked,
          ];
          return json(200, linked);
        }
        if (method === 'DELETE') {
          if (!existing) {
            return problem(
              404,
              'Geen gekoppeld repository',
              'Er is nog geen repository aan deze site gekoppeld.',
              'REPOSITORY_NOT_SET',
            );
          }
          data.repositories = data.repositories.filter(
            (r) => !(r.groupSlug === groupSlug && r.siteSlug === siteSlug),
          );
          return empty(204);
        }
      }

      // versions
      if (rest.length === 4 && rest[3] === 'versions' && method === 'GET') {
        if (!siteRow) return siteNotFound();
        return json(200, data.versions.filter((v) => v.groupSlug === groupSlug && v.siteSlug === siteSlug));
      }
      if (rest.length === 6 && rest[3] === 'versions' && rest[5] === '_set-live' && method === 'POST') {
        if (!siteRow) return siteNotFound();
        const versionId = rest[4]!;
        const version = data.versions.find((v) => v.id === versionId && v.groupSlug === groupSlug && v.siteSlug === siteSlug);
        if (!version) return problem(404, 'Onbekende versie', `Geen versie met id "${versionId}".`);
        if (version.target !== 'live') {
          return problem(422, 'Ongeldig doel', 'Alleen versies met doel "live" kunnen live gezet worden.');
        }
        data.versions
          .filter((v) => v.groupSlug === groupSlug && v.siteSlug === siteSlug)
          .forEach((v) => {
            v.isLive = v.id === versionId;
          });
        siteRow.liveVersionId = versionId;
        return json(200, siteDerived(data, siteRow));
      }

      // previews
      if (rest.length === 4 && rest[3] === 'previews' && method === 'GET') {
        if (!siteRow) return siteNotFound();
        return json(200, data.previews.filter((p) => p.groupSlug === groupSlug && p.siteSlug === siteSlug));
      }
      if (rest.length === 6 && rest[3] === 'previews' && rest[5] === 'access' && method === 'PUT') {
        if (!siteRow) return siteNotFound();
        const ref = rest[4]!;
        const preview = data.previews.find((p) => p.groupSlug === groupSlug && p.siteSlug === siteSlug && p.ref === ref);
        if (!preview) return problem(404, 'Onbekende preview', `Geen preview met ref "${ref}".`);
        const body = readJson();
        const access = body.access as Partial<Access> | null | undefined;
        preview.accessOverride = access
          ? {
              base: (access.base ?? 'public') as AccessBase,
              keys: Boolean(access.keys),
              invitees: Boolean(access.invitees),
            }
          : null;
        preview.lastUpdatedAt = now();
        return json(200, preview);
      }
      if (rest.length === 5 && rest[3] === 'previews' && method === 'DELETE') {
        if (!siteRow) return siteNotFound();
        const ref = rest[4]!;
        const preview = data.previews.find((p) => p.groupSlug === groupSlug && p.siteSlug === siteSlug && p.ref === ref);
        // Idempotent: already gone is a 204 too, not a 404.
        if (preview) {
          data.previews = data.previews.filter((p) => p !== preview);
          data.versions = data.versions.filter((v) => v.id !== preview.versionId);
        }
        return empty(204);
      }

      // deploys (upload)
      if (rest.length === 4 && rest[3] === 'deploys' && method === 'POST') {
        if (!siteRow) return siteNotFound();
        const body = init.body;
        if (!(body instanceof FormData)) {
          return problem(422, 'Ongeldig verzoek', 'Verwacht multipart/form-data met een "bestand"-veld.');
        }
        const file = body.get('file');
        if (!file) {
          return problem(422, 'Ongeldig archief', 'Veld "bestand" ontbreekt.');
        }
        const previewRef = body.get('preview');
        const versionId = nextId('versie');
        const target = previewRef ? 'preview' : 'live';
        const version: Version = {
          id: versionId,
          siteSlug,
          groupSlug,
          target,
          storageRef: `${groupSlug}/${siteSlug}/${versionId}`,
          origin: 'upload',
          createdByMember: 'dev-beheerder',
          createdByName: 'Bea Heerder',
          createdByRepository: null,
          createdAt: now(),
          isLive: target === 'live',
        };
        data.versions.push(version);
        if (target === 'live') {
          data.versions
            .filter((v) => v.groupSlug === groupSlug && v.siteSlug === siteSlug && v.target === 'live')
            .forEach((v) => {
              v.isLive = v.id === versionId;
            });
          siteRow.liveVersionId = versionId;
          siteRow.hasLiveVersion = true;
          siteRow.lastPublishedAt = version.createdAt;
        } else {
          const ref = String(previewRef);
          const existing = data.previews.find(
            (p) => p.groupSlug === groupSlug && p.siteSlug === siteSlug && p.ref === ref,
          );
          if (existing) {
            data.versions = data.versions.filter((v) => v.id !== existing.versionId);
            existing.versionId = versionId;
            existing.lastUpdatedAt = version.createdAt;
          } else {
            data.previews.push({
              siteSlug,
              groupSlug,
              ref,
              versionId,
              accessOverride: null,
              lastUpdatedAt: version.createdAt,
              expiresAt: new Date(Date.now() + 30 * 24 * 60 * 60 * 1000).toISOString(),
              url: `/${groupSlug}/${siteSlug}/_preview/${ref}/`,
            });
          }
        }
        return json(201, { versionId });
      }
    }

    return problem(404, 'Onbekend eindpunt', `Geen route voor ${method} ${path}.`);
  }

  return { fetch: mockFetch as typeof fetch, data };
}

/**
 * Replaces `globalThis.fetch` with the mock and returns a restore function.
 * Meant for `npm run dev` behind `VITE_MOCK_API`; vitest tests normally use
 * `vi.stubGlobal('fetch', makeMockBackend().fetch)` instead of this function,
 * so that `vi.unstubAllGlobals()` already takes care of the restore.
 */
export function installMock(backend: MockBackend = makeMockBackend()): () => void {
  const original = globalThis.fetch;
  globalThis.fetch = backend.fetch;
  return () => {
    globalThis.fetch = original;
  };
}
