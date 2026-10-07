/**
 * Types for the resources of the session API (`/-/api/v1`). Field names
 * follow the backend's camelCase wire contract (NL GOV API Design Rules).
 */

export type { Problem } from './client';

/**
 * The base answer to "who can view this site": exactly one per
 * site, a single shared enum type in the backend. `nobody` grants nothing by
 * itself, so a site on that base is reachable only through the extras.
 */
export type AccessBase = 'public' | 'sso' | 'site_team' | 'nobody';

/** Widest first, which is the order the radio group offers them in. */
export const ACCESS_BASE_VALUES: readonly AccessBase[] = [
  'public',
  'sso',
  'site_team',
  'nobody',
];

/**
 * A base plus the two extras that widen it. Someone gets in when the base lets
 * them, or they redeem a valid secret link, or they are an invitee and logged
 * in; the extras never narrow the base.
 */
export interface Access {
  base: AccessBase;
  keys: boolean;
  invitees: boolean;
}

export type PlatformRole = 'admin' | 'member';
export type MemberStatus = 'active' | 'deactivated';
export type VersionTarget = 'live' | 'preview';
export type VersionOrigin = 'upload' | 'action';
export type KeyStatus = 'active' | 'revoked';
export type RepositoryProvider = 'github' | 'forgejo';

/** ISO 8601 instant as the backend serialises it (UTC, with "Z"). */
export type Timestamp = string;

/** The interface language a member chose; null means "follow my browser". */
export type MemberLanguage = 'nl' | 'en';

export interface Member {
  id: string;
  ssoSubject: string;
  email: string;
  name: string;
  platformRole: PlatformRole;
  status: MemberStatus;
  /** The PLAK_BOOTSTRAP_ADMIN_SUB account: its status and role cannot be changed. */
  isBootstrap?: boolean;
  createdAt: Timestamp;
  lastLoginAt: Timestamp | null;
}

/**
 * Response of `GET /me`: the logged-in member plus the platform context the
 * SPA needs to build links to the content host (content and admin live on
 * different origins).
 */
export interface Me extends Member {
  /** Origin of the content host (e.g. `https://plak.example`), without a trailing slash. */
  contentBaseUrl: string;
  /** Groups this member has a role in, on slug. Empty when the member is nowhere a group member. */
  groupRoles: MyGroupRole[];
  /**
   * Only the sites this member has a site role of their own on; everywhere
   * else in a group the role in `groupRoles` stands on its own.
   */
  siteRoles: MySiteRole[];
  /** Configured Forgejo base URLs a site's trusted repository can live on. */
  ciForgejoHosts: string[];
  /** Audience CI must request for its ID token; also the `host` action input. */
  ciAudience: string;
  /**
   * The interface language this member set for themselves, or null when they
   * left the choice to their browser. It lives on the account, so it holds on
   * every device they sign in from.
   */
  language: MemberLanguage | null;
}

export interface MyGroupRole {
  groupSlug: string;
  role: Role;
}

/** `effectiveRole` is the widest of the group role and this site role. */
export interface MySiteRole {
  groupSlug: string;
  siteSlug: string;
  role: Role;
  effectiveRole: Role;
}

export interface Group {
  slug: string;
  name: string;
  defaultAccess: Access;
}

/**
 * Site representation for the overview: besides the base fields
 * also the derived status the site list shows per row, so the SPA never
 * has to count versions or previews itself.
 */
export interface Site {
  /** Fixed id of the site. A workflow names it as `site-id`, so a deploy can only land on this site. */
  id: string;
  groupSlug: string;
  slug: string;
  title: string;
  access: Access;
  /** Whether the content may load scripts, styles and fonts from a fixed list of external hosts. On by default. */
  externalSources: boolean;
  /** Whether the content is served with its own empty origin, so it can read no other site on the shared hostname. On by default. */
  sandbox: boolean;
  liveVersionId: string | null;
  createdBy: string;
  /** true as soon as liveVersionId is set; an explicit field rather than derived in the UI. */
  hasLiveVersion: boolean;
  lastPublishedAt: Timestamp | null;
  previewCount: number;
  /**
   * Previous live versions this site keeps besides the current one, set on the
   * site itself; null when it follows the platform default, 0 keeps all.
   */
  liveVersionsKept: number | null;
}

export interface SiteStorage {
  /** What all versions of the site (live and preview) occupy on disk, in bytes. */
  usedBytes: number;
  /** The per-site quota in bytes; 0 means no quota. */
  maxBytes: number;
  /** Previous live versions kept besides the current one, the effective number; 0 means all versions are kept. */
  liveVersionsKept: number;
  /** True when the site follows the platform default instead of its own number. */
  liveVersionsKeptIsDefault: boolean;
  /** The platform default; 0 means all versions are kept. */
  defaultLiveVersionsKept: number;
}

export interface Version {
  id: string;
  siteSlug: string;
  groupSlug: string;
  target: VersionTarget;
  storageRef: string;
  origin: VersionOrigin;
  createdByMember: string | null;
  createdByName: string | null;
  /** Set when `origin === 'action'`: the repository that published this version, e.g. "github.com/minbzk/website". */
  createdByRepository: string | null;
  createdAt: Timestamp;
  isLive: boolean;
}

export interface Preview {
  siteSlug: string;
  groupSlug: string;
  ref: string;
  versionId: string;
  accessOverride: Access | null;
  lastUpdatedAt: Timestamp;
  expiresAt: Timestamp;
  url: string;
}

export interface Invitee {
  id: string;
  siteSlug: string;
  groupSlug: string;
  identifier: string;
  addedBy: string;
  addedAt: Timestamp;
}

export interface Key {
  siteSlug: string;
  groupSlug: string;
  label: string;
  selector: string;
  status: KeyStatus;
  createdAt: Timestamp;
  expiresAt: Timestamp | null;
}

/**
 * Response of creating a key: the full value (`selector.verifier`) is shown
 * exactly once.
 */
export interface KeyCreated {
  key: Key;
  value: string;
}

/**
 * A site's trusted CI repository (trusted publishing). A site links to
 * exactly one repository; a linked repository may publish to this site from
 * its workflow without any secret, authenticated by an OIDC ID token.
 */
export interface SiteRepository {
  groupSlug: string;
  siteSlug: string;
  /** The site's fixed id, which a workflow names as `site-id`. */
  siteId: string;
  /** False while a workflow that does not name the site id is still accepted. It only ever goes from false to true. */
  siteIdRequired: boolean;
  provider: RepositoryProvider;
  /** 'https://github.com' or the forgejo base URL. */
  host: string;
  owner: string;
  repo: string;
  repositoryId: number;
  ownerId: number;
  /** null means every branch may publish live; previews may always publish from any branch. */
  liveBranch: string | null;
  /** False while the IDs are only as entered: neither the provider nor a CI token has confirmed them. */
  idsConfirmed: boolean;
  /** Name or e-mail of whoever linked the repository; empty string if unknown. */
  createdBy: string;
  createdAt: Timestamp;
}

/** Body of `PUT /sites/{group}/{site}/repository`. */
export interface SiteRepositoryInput {
  provider: RepositoryProvider;
  /** Required for forgejo (one of `me.ciForgejoHosts`), omitted for github. */
  host?: string;
  owner: string;
  repo: string;
  liveBranch: string | null;
  /**
   * Both or neither: the provider's ids, for a repository Plak cannot look up
   * without credentials (a private one).
   */
  repositoryId?: number;
  ownerId?: number;
}

/** The CLI device flow's lookup response: what a human approves or denies. */
export interface DeviceAuthorization {
  /** 'ABCD-EFGH'. */
  userCode: string;
  clientName: string | null;
  /** e.g. '203.0.113.0/24'. */
  ipTruncated: string | null;
  createdAt: Timestamp;
  expiresAt: Timestamp;
  /**
   * Whether the CLI started from the same truncated network as the approver
   * is on now; null when either address is unknown. Computed server-side.
   */
  sameNetwork: boolean | null;
}

/** A device (CLI login) linked to the current member's account. */
export interface CliSession {
  id: string;
  clientName: string | null;
  createdAt: Timestamp;
  lastUsedAt: Timestamp | null;
  expiresAt: Timestamp;
}

/** A role of someone's own on one site, always a site in the group being read. */
export interface GroupSiteRole {
  siteSlug: string;
  siteTitle: string;
  role: Role;
}

export interface GroupMember {
  groupSlug: string;
  memberId: string;
  identifier: string;
  name: string;
  email: string;
  role: Role;
  /**
   * The sites in this group this member holds a role of their own on. Only
   * this group: what someone holds elsewhere belongs to that group, and would
   * say where else in the organisation they work.
   */
  siteRoles: GroupSiteRole[];
}

/**
 * Hit of the member search under a group or one site: someone who could be
 * given a role there. `identifier` is what the add call wants, and
 * `alreadyMember` says whether this person already has a role of their own at
 * that level: in that group, or on that one site. `groupRole` only comes from
 * the site search and holds the role this person already reaches the site with
 * through the group, which a site role widens but never narrows.
 */
export interface MemberSuggestion {
  identifier: string;
  name: string;
  email: string;
  alreadyMember: boolean;
  groupRole: Role | null;
}

/**
 * Role within a group or within one site. The interface labels these lezer,
 * redacteur and beheerder; the wire carries the English values.
 */
export type Role = 'reader' | 'editor' | 'admin';

/**
 * Row in the members list of one site: everyone who can reach that site, not
 * only whoever has a role of their own on it. `groupRole` is the role in the
 * group of this site, `siteRole` the role that holds on this one site;
 * `effectiveRole` is the widest of the two and never null, because a site role
 * only ever widens.
 */
export interface SiteMember {
  groupSlug: string;
  siteSlug: string;
  memberId: string;
  identifier: string;
  name: string;
  email: string;
  groupRole: Role | null;
  siteRole: Role | null;
  effectiveRole: Role;
}

/** Row in the site overview: a group with its sites. */
export interface OverviewGroup {
  group: Group;
  sites: Site[];
}

/** Response of `GET /platform/storage` (platform admins only). */
export interface Volume {
  totalBytes: number;
  usedBytes: number;
  freeBytes: number;
  /** Free space the volume must keep; 0 means the check is off. */
  reserveBytes: number;
  /** Largest unpacked deploy; free space below reserve plus this stops a maximum deploy. */
  maxDeployBytes: number;
}

export interface Overview {
  groups: OverviewGroup[];
}

export interface GroupDetail {
  group: Group;
  sites: Site[];
  members: GroupMember[];
}
