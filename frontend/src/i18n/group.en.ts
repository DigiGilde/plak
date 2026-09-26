/**
 * English counterpart of group.nl.ts. Typed against it, so a key present in
 * one language and missing from the other is a compile error rather than a
 * raw key on screen.
 */
import type { groupNl } from './group.nl';

export const groupEn: Record<keyof typeof groupNl, string> = {
  // -- Actions and states shared by more than one of these screens ---------
  'group.action.publishSite': 'Publish a site',
  'group.action.newGroup': 'Create a group',
  'group.drop.release': 'Let go to upload',

  // -- Overview ------------------------------------------------------------
  'group.overview.heading': 'Overview',
  'group.overview.loading': 'Loading the overview...',
  'group.overview.error': 'Could not load the overview',
  'group.overview.errorFallback': 'Unknown error',
  'group.overview.empty': 'Nothing is online here yet.',
  'group.overview.empty.supportingText':
    'Pick a file or an archive from your folder, give it a title, and put it online. You create a group in the same step.',
  'group.overview.table.label': 'Sites in {group}',
  'group.overview.table.empty': 'No sites in this group yet.',

  // -- The site table, shared by the overview and the group's sites tab ----
  'group.sites.column.live': 'Live',
  'group.sites.column.site': 'Site',
  'group.sites.column.access': 'Access',
  'group.sites.column.lastPublished': 'Last published',
  'group.siteRow.live': 'Has a live version',
  'group.siteRow.notLive': 'No live version',
  'group.siteRow.neverPublished': 'Not published yet',

  // -- Groups page ---------------------------------------------------------
  'group.groups.heading': 'Groups',
  'group.groups.loading': 'Loading the groups...',
  'group.groups.list.label': 'Groups',
  'group.groups.empty': 'You are not in any group yet.',
  'group.groups.empty.supportingText': 'Create one; after that you put sites in it.',
  'group.groups.sites.none': 'No sites yet',
  'group.groups.sites.one': '1 site',
  'group.groups.sites.many': '{count} sites',
  'group.groups.online.none': 'Nothing online',
  'group.groups.online.one': '1 site online',
  'group.groups.online.many': '{count} sites online',

  // -- Group page: header and tab bar --------------------------------------
  'group.page.loading': 'Loading the group…',
  'group.page.tabs.label': 'Group sections',
  'group.page.tab.sites': 'Sites',
  'group.page.tab.members': 'Members',
  'group.page.tab.settings': 'Settings',

  // -- New group sheet -----------------------------------------------------
  'group.new.title': 'New group',
  'group.new.name.label': 'Name',
  'group.new.name.required': 'A name',
  'group.new.slug.label': 'Slug (in the URL)',
  'group.new.slug.required': 'A slug',
  'group.new.slug.pattern':
    'Only lowercase letters, digits and hyphens, no hyphen at the start or the end',
  'group.new.cancel': 'Cancel',

  // -- Group page: sites tab -----------------------------------------------
  'group.sites.heading': 'Sites',
  'group.sites.intro':
    'The sites of this group. Open a site to publish, to arrange access or to look at the versions.',
  'group.sites.table.label': 'Sites in this group',
  'group.sites.table.empty': 'No sites in this group yet',
  'group.sites.table.empty.supportingText':
    "Choose 'Publish a site' to put the first one in it.",

  // -- Group page: members tab ---------------------------------------------
  'group.members.heading': 'Members',
  'group.members.intro':
    'The role decides what someone may do in this group: a reader looks on, an editor puts sites online, an administrator decides who may join. Everyone on this list also sees what is set to "The site team only". You add someone by the verified email address they sign in with through SSO Rijk.',
  'group.members.table.label': 'Members of this group',
  'group.members.column.member': 'Member',
  'group.members.column.role': 'Role',
  'group.members.column.actions': 'Actions',
  'group.members.empty': 'No group members yet',
  'group.members.empty.supportingText': 'Add the first group member below.',
  'group.members.suggestion.alreadyMember': 'already a member',
  'group.members.action.setRole': 'Make {role}',
  'group.members.action.remove': 'Remove from the group',
  'group.members.confirm.remove.title': 'Remove {name} from the group?',
  'group.members.confirm.remove.text':
    '{name} is then no longer {role} in this group and loses that role on every site in it.',
  'group.members.confirm.remove.noSiteRoles':
    '{name} holds no site role of their own in this group, so nothing else changes.',
  'group.members.confirm.remove.siteRoles.one':
    'On 1 site in this group {name} also holds a site role of their own:',
  'group.members.confirm.remove.siteRoles.many':
    'On {count} sites in this group {name} also holds a site role of their own:',
  'group.members.confirm.remove.siteRoles.list': 'Sites in this group with a site role of their own',
  'group.members.confirm.remove.siteRoles.more.one': 'And 1 more site',
  'group.members.confirm.remove.siteRoles.more.many': 'And {count} more sites',
  'group.members.confirm.remove.siteRoles.also': 'Remove these site roles as well',
  'group.members.confirm.remove.siteRoles.keeps':
    'Leave this off and {name} keeps those site roles, and can still reach those sites.',
  'group.members.confirm.remove.keep': 'Keep the membership',
  'group.members.confirm.remove.confirm': 'Remove from the group',
  'group.members.confirm.remove.confirmWithSiteRoles':
    'Remove from the group and remove the site roles',
  'group.members.addFailed': 'Could not add group member {name}',
  'group.members.roleChangeFailed': 'Could not change the role of {name}',
  'group.members.removeFailed': 'Could not remove group member {name}',
  'group.members.retry': 'Try again',
  'group.members.add.legend': 'Add a member',
  'group.members.add.supportingText':
    'Someone has to sign in to the admin themselves first; Plak creates no account here.',
  'group.members.add.identifier.label': 'Name or email address',
  'group.members.add.identifier.placeholder': 'Type a name or email address',
  'group.members.add.identifier.help':
    'From two letters on we search the names and addresses of everyone who has signed in before. People who are already members are listed as such.',
  'group.members.add.identifier.required': 'Someone from the list',
  'group.members.add.suggestions.tooShort': 'Type two letters to search',
  'group.members.add.suggestions.searching': 'Searching...',
  'group.members.add.suggestions.empty': 'Nobody found',
  'group.members.add.role.label': 'Role',
  'group.members.add.submit': 'Add member',

  // -- Group page: settings tab --------------------------------------------
  'group.settings.heading': 'Default access for new sites',
  'group.settings.intro':
    'This is what a new site in this group starts with. Access stays adjustable per site afterwards, on the Access tab of that site. Existing sites do not change along.',
  'group.settings.list.label': 'Default access',
  'group.settings.saved': 'Default access saved',
  'group.settings.saveFailed': 'Default access not saved',
  'group.settings.saveFailed.detail': 'Saving did not work.',
};
