/**
 * English counterpart of admin.nl.ts. Typed against it, so a key present in
 * one language and missing from the other is a compile error rather than a
 * raw key on screen.
 */
import type { adminNl } from './admin.nl';

export const adminEn: Record<keyof typeof adminNl, string> = {
  // -- Column headers shared by the admin tables ----------------------------
  'admin.column.member': 'Member',
  'admin.column.role': 'Role',
  'admin.column.actions': 'Actions',

  // -- Shared controls ------------------------------------------------------
  'admin.retry': 'Try again',
  'admin.confirm.keep': 'Keep',
  'admin.confirm.phrase.label': 'Type {phrase} to confirm',
  'admin.confirm.phrase.mismatch': 'Exactly {phrase}',
  'admin.rowActions.label': 'Actions for {label}',
  'admin.notice.unknownError': 'Unknown error.',
  'admin.errorBanner.status': 'Error code {status}.',

  // -- Platform administration ----------------------------------------------
  'admin.members.column.created': 'Created',
  'admin.members.column.activity': 'Last activity',
  'admin.members.search.label': 'Search for a member by name or email address',
  'admin.members.search.placeholder': 'Search for a member',
  'admin.members.loading': 'Loading members...',
  'admin.members.loadFailed': 'Something went wrong while fetching the members.',
  'admin.members.table.label': 'Platform members',
  'admin.members.empty': 'There are no members to show yet.',
  'admin.members.notFound': 'No member found.',
  'admin.members.notFound.detail': "Nothing matches '{query}'.",
  'admin.members.standing.admin': 'Platform administrator',
  'admin.members.standing.member': 'Member',
  'admin.members.standing.noAccess': 'No access',
  'admin.members.action.revoke': 'Withdraw access',
  'admin.members.action.restore': 'Give access back',
  'admin.members.action.promote': 'Make platform administrator',
  'admin.members.action.demote': 'Take away the administrator role',
  'admin.members.blocked.bootstrap': 'bootstrap account',
  'admin.members.blocked.lastAdmin': 'last administrator',
  'admin.members.blocked.title': 'This is not possible for {name}',
  'admin.members.blocked.bootstrap.explanation':
    'The bootstrap account always keeps its access and administrator role.',
  'admin.members.blocked.lastAdmin.explanation':
    'At least one active administrator has to remain.',
  'admin.members.roleFailed': 'Changing the role of {name} did not work',
  'admin.members.activateFailed': 'Activating {name} did not work',
  'admin.members.deactivateFailed': 'Deactivating {name} did not work',

  // -- Linked sessions ------------------------------------------------------
  'admin.sessions.subtitle': 'Every sign-in through {command} that still has access to your account.',
  'admin.sessions.loading': 'Loading sessions',
  'admin.sessions.empty': 'No linked sessions yet',
  'admin.sessions.empty.install.before': 'No Plak CLI yet? ',
  'admin.sessions.empty.install.link': 'Here is how to install and use it.',
  'admin.sessions.empty.detail':
    'Run plak login on your computer to link the Plak CLI to your account.',
  'admin.sessions.unknownClient': 'Unknown program',
  'admin.sessions.neverUsed': 'not yet',
  'admin.sessions.times': 'Linked {linked} - last used {lastUsed} - expires {expires}',
  'admin.sessions.revoke': 'Revoke',
  'admin.sessions.revoke.label': 'Revoke the session of {name}',
  'admin.sessions.confirm.title': 'Revoke the session of {name}?',
  'admin.sessions.confirm.text':
    'This session loses access to your account straight away; linking up again takes another plak login.',
  'admin.sessions.confirm.keep': 'Keep the session',
  'admin.sessions.confirm.confirm': 'Revoke session',
  'admin.sessions.revokeFailed': 'The session of {name} was not revoked',
  'admin.sessions.revokeFailed.detail': 'Revoking did not work.',

  // -- Site members: the members tab of a site ------------------------------
  'admin.siteMembers.inherited.summary.one': '{count} member via the group {group}',
  'admin.siteMembers.inherited.summary.many': '{count} members via the group {group}',
  'admin.siteMembers.inherited.note':
    'These roles apply to the whole group and you change them there, not here: {link}.',
  'admin.siteMembers.inherited.link': 'members of the group {group}',
  'admin.siteMembers.inherited.table': 'Members via the group',
  'admin.siteMembers.table': 'Members with a role on this site',
  'admin.siteMembers.empty': 'Nobody has a role of their own on this site',
  'admin.siteMembers.empty.detail': 'Give someone a role below that only applies here.',
  'admin.siteMembers.viaGroup': 'via the group',
  'admin.siteMembers.keepsViaGroup': 'Stays {role} via the group',
  'admin.siteMembers.action.setRole': 'Make {role}',
  'admin.siteMembers.action.remove': 'Remove site role',
  'admin.siteMembers.confirm.remove.title': 'Remove the site role of {name}?',
  'admin.siteMembers.confirm.remove.keepsViaGroup':
    '{name} keeps access to this site as {role} via the group.',
  'admin.siteMembers.confirm.remove.noAccess':
    '{name} loses access to this site: there is no role via the group to fall back on.',
  'admin.siteMembers.confirm.remove.keep': 'Keep the site role',
  'admin.siteMembers.confirm.remove.confirm': 'Remove site role',
  'admin.siteMembers.addFailed': 'Giving {name} a role on this site did not work',
  'admin.siteMembers.roleFailed': 'Changing the site role of {name} did not work',
  'admin.siteMembers.removeFailed': 'Removing the site role of {name} did not work',
  'admin.siteMembers.suggestion.alreadyMember': 'already has a site role',
  'admin.siteMembers.suggestion.viaGroup': '{role} via the group',
  'admin.siteMembers.form.heading': 'Give someone a role on this site',
  'admin.siteMembers.form.hint':
    'A site role only widens: whoever may already do more via the group keeps that. You take access away at the group. Someone has to log in on the administration themselves first; Plak does not create an account here.',
  'admin.siteMembers.form.identifier': 'Name or email address',
  'admin.siteMembers.form.identifier.placeholder': 'Type a name or email address',
  'admin.siteMembers.form.identifier.help':
    'The list starts with the members of this group, with the role they already have there. From two letters onwards we search the names and addresses of everyone who has logged in before, inside the group and outside it.',
  'admin.siteMembers.form.identifier.required': 'Someone from the list',
  'admin.siteMembers.form.searchFurther':
    'Type two letters to search further, outside this group as well.',
  'admin.siteMembers.form.tooShort': 'Type two letters to search',
  'admin.siteMembers.form.searching': 'Searching...',
  'admin.siteMembers.form.noSuggestions': 'Nobody found',
  'admin.siteMembers.form.role': 'Role',
  'admin.siteMembers.form.role.noEffect':
    'This changes nothing about what this person may do here: via the group they are already {role}.',
  'admin.siteMembers.form.submit': 'Give role',

  // -- Platform administration: the content volume --------
  'admin.volume.heading': 'Content volume',
  'admin.volume.loading': 'Loading the content volume...',
  'admin.volume.summary.label': 'Fill level of the content volume',
  'admin.volume.used': 'Used',
  'admin.volume.used.value': '{used} of {total}',
  'admin.volume.free': 'Free',
  'admin.volume.reserve': 'Reserve',
  'admin.volume.reserve.off': 'Off',
  'admin.volume.low.title': 'Little room left on the content volume',
  'admin.volume.low.detail':
    'A deploy of the maximum size ({maxDeploy}) would take the volume below the reserve of {reserve}. Free up space or enlarge the volume.',
};
