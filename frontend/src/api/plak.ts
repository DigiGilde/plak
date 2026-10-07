/**
 * Typed functions over the session API (`/-/api/v1`) for the admin SPA.
 * `POST /sites/{group}/{site}/deploys` and
 * `DELETE /sites/{group}/{site}/previews/{ref}` are the deploy API's own
 * paths, shared with CI; the rest follow the same slug addressing and NL API
 * Design Rules conventions.
 */
import { ApiError, request, sendJson } from './client';
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
  Overview,
  PlatformRole,
  Preview,
  Role,
  Site,
  SiteMember,
  SiteRepository,
  SiteRepositoryInput,
  SiteStorage,
  Version,
  Volume,
  Access,
} from './types';

const BASE = '/-/api/v1';

/** `base` untouched, every further segment percent-encoded. */
function join(base: string, ...segments: string[]): string {
  return [base, ...segments.map(encodeURIComponent)].join('/');
}

// -- Session --------------------------------------------------------------

/**
 * The logged-in member, plus the content origin that shared links belong on.
 * Raises an ApiError with status 401 when there is no (valid) session; the
 * router guard and the landing page use this to tell logged in
 * from logged out.
 */
export function me(): Promise<Me> {
  return request<Me>(`${BASE}/me`);
}

/**
 * Record the interface language on the account. `null` hands the choice back
 * to the browser. Stored server-side rather than in this browser, so it
 * travels with the member instead of with the device.
 */
export function setMyLanguage(language: MemberLanguage | null): Promise<void> {
  return sendJson<void>('PUT', `${BASE}/me/language`, { language });
}

function sitePath(group: string, site: string): string {
  return join(BASE, 'sites', group, site);
}

function groupPath(group: string): string {
  return join(BASE, 'groups', group);
}

// -- Overview and groups ----------------------------------------------------

export function overview(): Promise<Overview> {
  return request<Overview>(`${BASE}/overview`);
}

export function group(slug: string): Promise<GroupDetail> {
  return request<GroupDetail>(groupPath(slug));
}

export function createGroup(name: string, slug: string): Promise<Group> {
  return sendJson<Group>('POST', `${BASE}/groups`, { name, slug });
}

export function deleteGroup(slug: string): Promise<void> {
  return request<void>(groupPath(slug), { method: 'DELETE' });
}

export function setGroupDefaultAccess(slug: string, access: Access): Promise<Group> {
  return sendJson<Group>('PUT', `${groupPath(slug)}/default-access`, access);
}

// -- Sites ----------------------------------------------------------------

export function createSite(groupSlug: string, title: string, slug: string): Promise<Site> {
  return sendJson<Site>('POST', `${groupPath(groupSlug)}/sites`, { title, slug });
}

export function deleteSite(groupSlug: string, siteSlug: string): Promise<void> {
  return request<void>(sitePath(groupSlug, siteSlug), { method: 'DELETE' });
}

export function setAccess(
  groupSlug: string,
  siteSlug: string,
  access: Access,
): Promise<Site> {
  return sendJson<Site>('PUT', `${sitePath(groupSlug, siteSlug)}/access`, access);
}

export function setExternalSources(
  groupSlug: string,
  siteSlug: string,
  externalSources: boolean,
): Promise<Site> {
  return sendJson<Site>('PUT', `${sitePath(groupSlug, siteSlug)}/external-sources`, { externalSources });
}

export function setSandbox(
  groupSlug: string,
  siteSlug: string,
  sandbox: boolean,
): Promise<Site> {
  return sendJson<Site>('PUT', `${sitePath(groupSlug, siteSlug)}/sandbox`, { sandbox });
}

export function setLiveVersionsKept(
  groupSlug: string,
  siteSlug: string,
  liveVersionsKept: number | null,
): Promise<Site> {
  return sendJson<Site>('PUT', `${sitePath(groupSlug, siteSlug)}/live-versions-kept`, { liveVersionsKept });
}

// -- Invitees -----------------------------------------------------------------

export function invitees(groupSlug: string, siteSlug: string): Promise<Invitee[]> {
  return request<Invitee[]>(`${sitePath(groupSlug, siteSlug)}/invitees`);
}

export function addInvitee(
  groupSlug: string,
  siteSlug: string,
  identifier: string,
): Promise<Invitee> {
  return sendJson<Invitee>('POST', `${sitePath(groupSlug, siteSlug)}/invitees`, { identifier });
}

export function removeInvitee(
  groupSlug: string,
  siteSlug: string,
  inviteeId: string,
): Promise<void> {
  return request<void>(
    join(sitePath(groupSlug, siteSlug), 'invitees', inviteeId),
    { method: 'DELETE' },
  );
}

// -- Secret-link keys ---------------------------------------------------------

export function keys(groupSlug: string, siteSlug: string): Promise<Key[]> {
  return request<Key[]>(`${sitePath(groupSlug, siteSlug)}/keys`);
}

export function createKey(
  groupSlug: string,
  siteSlug: string,
  label: string | null,
  expiresAt: string | null,
): Promise<KeyCreated> {
  return sendJson<KeyCreated>('POST', `${sitePath(groupSlug, siteSlug)}/keys`, { label, expiresAt });
}

export function revokeKey(
  groupSlug: string,
  siteSlug: string,
  selector: string,
): Promise<void> {
  return request<void>(
    join(sitePath(groupSlug, siteSlug), 'keys', selector),
    { method: 'DELETE' },
  );
}

// -- Site repository (trusted CI publishing) -----------------------------------

/**
 * The site's trusted CI repository, or null when nothing is linked yet
 * (404 REPOSITORY_NOT_SET, not an error for the UI). Needs site role editor+.
 */
export async function siteRepository(
  groupSlug: string,
  siteSlug: string,
): Promise<SiteRepository | null> {
  try {
    return await request<SiteRepository>(`${sitePath(groupSlug, siteSlug)}/repository`);
  } catch (error) {
    if (error instanceof ApiError && error.problem.code === 'REPOSITORY_NOT_SET') {
      return null;
    }
    throw error;
  }
}

/** Links (or replaces) the site's trusted repository. Needs site role admin. */
export function setSiteRepository(
  groupSlug: string,
  siteSlug: string,
  input: SiteRepositoryInput,
): Promise<SiteRepository> {
  return sendJson<SiteRepository>('PUT', `${sitePath(groupSlug, siteSlug)}/repository`, input);
}

/** Unlinks the site's trusted repository. Needs site role admin. */
export function deleteSiteRepository(groupSlug: string, siteSlug: string): Promise<void> {
  return request<void>(`${sitePath(groupSlug, siteSlug)}/repository`, { method: 'DELETE' });
}

// -- CLI device approval ---------------------------------------------------

export function lookupDeviceAuthorization(userCode: string): Promise<DeviceAuthorization> {
  return sendJson<DeviceAuthorization>('POST', `${BASE}/cli/device-authorizations/lookup`, { userCode });
}

export function approveDeviceAuthorization(userCode: string): Promise<void> {
  return sendJson<void>('POST', `${BASE}/cli/device-authorizations/approve`, { userCode });
}

export function denyDeviceAuthorization(userCode: string): Promise<void> {
  return sendJson<void>('POST', `${BASE}/cli/device-authorizations/deny`, { userCode });
}

// -- Linked sessions (CLI sessions) -----------------------------------------

export function cliSessions(): Promise<CliSession[]> {
  return request<CliSession[]>(`${BASE}/me/cli-sessions`);
}

export function revokeCliSession(id: string): Promise<void> {
  return request<void>(join(BASE, 'me', 'cli-sessions', id), {
    method: 'DELETE',
  });
}

// -- Group members -------------------------------------------------------------

export function groupMembers(groupSlug: string): Promise<GroupMember[]> {
  return request<GroupMember[]>(`${groupPath(groupSlug)}/members`);
}

export function addGroupMember(
  groupSlug: string,
  identifier: string,
  role: Role = 'reader',
): Promise<GroupMember> {
  return sendJson<GroupMember>('POST', `${groupPath(groupSlug)}/members`, { identifier, role });
}

export function setGroupRole(
  groupSlug: string,
  memberId: string,
  role: Role,
): Promise<GroupMember> {
  return sendJson<GroupMember>('PUT', join(groupPath(groupSlug), 'members', memberId, 'role'), { role });
}

/**
 * People who can be given a role in this group, matched case-insensitively on
 * name or e-mail. Needs at least two characters: below that the backend
 * refuses with 422 SEARCH_TOO_SHORT rather than hand out the whole directory.
 */
export function searchGroupMembers(
  groupSlug: string,
  query: string,
): Promise<MemberSuggestion[]> {
  return request<MemberSuggestion[]>(
    `${groupPath(groupSlug)}/members/search?q=${encodeURIComponent(query)}`,
  );
}

/**
 * Take someone out of the group. `siteRoles` says what happens to the roles
 * they hold on sites in this group: keeping them is the default here as it is
 * at the API, so taking more than was asked stays a deliberate choice.
 */
export function removeGroupMember(
  groupSlug: string,
  memberId: string,
  siteRoles: 'keep' | 'remove' = 'keep',
): Promise<void> {
  return request<void>(
    `${groupPath(groupSlug)}/members/${encodeURIComponent(memberId)}?siteRoles=${siteRoles}`,
    { method: 'DELETE' },
  );
}

// -- Site members --------------------------------------------------------------

/**
 * Everyone who can reach this site: whoever has a role on it of their own and
 * whoever reaches it through the group. Only the site role is editable through
 * the three calls below; a group role is changed on the group.
 */
export function siteMembers(group: string, site: string): Promise<SiteMember[]> {
  return request<SiteMember[]>(`${sitePath(group, site)}/members`);
}

export function addSiteMember(
  group: string,
  site: string,
  identifier: string,
  role: Role = 'reader',
): Promise<SiteMember> {
  return sendJson<SiteMember>('POST', `${sitePath(group, site)}/members`, { identifier, role });
}

export function setSiteRole(
  group: string,
  site: string,
  memberId: string,
  role: Role,
): Promise<SiteMember> {
  return sendJson<SiteMember>('PUT', join(sitePath(group, site), 'members', memberId, 'role'), { role });
}

/** The same search as on the group, against the members of one site. */
export function searchSiteMembers(
  group: string,
  site: string,
  query: string,
): Promise<MemberSuggestion[]> {
  return request<MemberSuggestion[]>(
    `${sitePath(group, site)}/members/search?q=${encodeURIComponent(query)}`,
  );
}

export function removeSiteMember(group: string, site: string, memberId: string): Promise<void> {
  return request<void>(join(sitePath(group, site), 'members', memberId), {
    method: 'DELETE',
  });
}

// -- Platform members (platform admins only) ------------------------------------

export function platformMembers(): Promise<Member[]> {
  return request<Member[]>(`${BASE}/platform/members`);
}

export function activatePlatformMember(memberId: string): Promise<Member> {
  return request<Member>(join(BASE, 'platform', 'members', memberId, '_activate'), {
    method: 'POST',
  });
}

export function deactivatePlatformMember(memberId: string): Promise<Member> {
  return request<Member>(join(BASE, 'platform', 'members', memberId, '_deactivate'), {
    method: 'POST',
  });
}

export function setPlatformRole(memberId: string, platformRole: PlatformRole): Promise<Member> {
  return sendJson<Member>('PUT', join(BASE, 'platform', 'members', memberId, 'platform-role'), { platformRole });
}

// -- Content volume (platform admins only) ---------------------------------------

export function platformStorage(): Promise<Volume> {
  return request<Volume>(`${BASE}/platform/storage`);
}

// -- Versions and rollback --------------------------------------------------

export function siteStorage(groupSlug: string, siteSlug: string): Promise<SiteStorage> {
  return request<SiteStorage>(`${sitePath(groupSlug, siteSlug)}/storage`);
}

export function versions(groupSlug: string, siteSlug: string): Promise<Version[]> {
  return request<Version[]>(`${sitePath(groupSlug, siteSlug)}/versions`);
}

export function setVersionLive(
  groupSlug: string,
  siteSlug: string,
  versionId: string,
): Promise<Site> {
  return request<Site>(
    join(sitePath(groupSlug, siteSlug), 'versions', versionId, '_set-live'),
    { method: 'POST' },
  );
}

// -- Previews ------------------------------------------------------------------

export function previews(groupSlug: string, siteSlug: string): Promise<Preview[]> {
  return request<Preview[]>(`${sitePath(groupSlug, siteSlug)}/previews`);
}

export function setPreviewAccess(
  groupSlug: string,
  siteSlug: string,
  ref: string,
  access: Access | null,
): Promise<Preview> {
  return sendJson<Preview>('PUT', join(sitePath(groupSlug, siteSlug), 'previews', ref, 'access'), { access });
}

/** Idempotent (204), like the deploy-API teardown. */
export function deletePreview(
  groupSlug: string,
  siteSlug: string,
  ref: string,
): Promise<void> {
  return request<void>(
    join(sitePath(groupSlug, siteSlug), 'previews', ref),
    { method: 'DELETE' },
  );
}

// -- Upload (deploy via session) -----------------------------------------------

export interface UploadResult {
  versionId: string;
}

/**
 * Live or preview deploy over the session: the same endpoint CI publishes to
 * with a bearer token, here with cookie auth. `file` is the
 * archive to unpack or a single html file; `previewRef` fills the `preview`
 * form field.
 */
export function upload(
  groupSlug: string,
  siteSlug: string,
  file: Blob,
  fileName: string,
  previewRef?: string,
): Promise<UploadResult> {
  const form = new FormData();
  form.set('file', file, fileName);
  if (previewRef) {
    form.set('preview', previewRef);
  }
  return request<UploadResult>(`${sitePath(groupSlug, siteSlug)}/deploys`, {
    method: 'POST',
    body: form,
  });
}
