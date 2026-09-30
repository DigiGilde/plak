/**
 * Shared role checks against `Me.groupRoles`/`siteRoles` (see
 * `frontend/src/api/types.ts`), so pages and the publish flow do not each
 * grow their own copy of "may this member do X".
 */
import type { Me, Role } from '@/api/types';

const CAN_CREATE_SITE: readonly Role[] = ['editor', 'admin'];

/**
 * Whether `me` may create a site in this group right now: a group role of
 * editor or beheerder. Mirrors `create_site` in backend/src/plak/api/admin.py,
 * which calls `_group_with_role(..., Role.EDITOR)` without a platform-admin
 * bypass, so a platform admin with no group role of their own is not eligible
 * either.
 */
export function mayCreateSiteIn(me: Me | null | undefined, groupSlug: string): boolean {
  return me?.groupRoles.some((r) => r.groupSlug === groupSlug && CAN_CREATE_SITE.includes(r.role)) ?? false;
}

/**
 * Whether `me` is beheerder of this group. Mirrors `delete_group` in
 * backend/src/plak/api/admin.py: group role admin, no platform-admin bypass.
 */
export function isGroupAdmin(me: Me | null | undefined, groupSlug: string): boolean {
  return me?.groupRoles.some((r) => r.groupSlug === groupSlug && r.role === 'admin') ?? false;
}
