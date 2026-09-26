/**
 * The English catalogue. Typed as `Catalogue`, so leaving a key out is a
 * compile error rather than a raw key on screen.
 *
 * The role names keep their Dutch shape in spirit but not their words: an
 * English reader is not helped by "redacteur". The name "SSO Rijk" does
 * keep its name, because that is what the login service is called and
 * translating a proper noun only makes it unfindable.
 */
import { adminEn } from './admin.en';
import { groupEn } from './group.en';
import { pagesEn } from './pages.en';
import { publishEn } from './publish.en';
import { siteEn } from './site.en';
import type { Catalogue } from './nl';

const shared = {
  'error.generic.title': 'Something went wrong',
  'error.generic.detail': 'Try again, or reload the page.',
  'error.expired.title': 'You are no longer signed in',
  'error.expired.detail':
    'Your session expired or was ended. Sign in again to continue where you were.',
  'error.expired.action': 'Sign in again',
  'error.code.MEMBER_DEACTIVATED': 'A platform administrator withdrew your access.',
  'error.code.LAST_GROUP_ADMIN':
    'This is the last administrator of the group. Appoint someone else first.',
  'error.code.LAST_PLATFORM_ADMIN':
    'This is the last active platform administrator. Appoint someone else first.',
  'error.code.SELF_NOT_ALLOWED': 'You cannot do this to your own account.',
  'error.code.UNKNOWN_MEMBER': 'Unknown member; they have to sign in to the admin once first.',

  'beta.bar': 'Beta - Plak is under development and may contain errors',

  'role.reader': 'Reader',
  'role.editor': 'Editor',
  'role.admin': 'Administrator',
  'role.hint.group.reader': 'Looks on, changes nothing.',
  'role.hint.group.editor': 'Puts sites online and manages their content.',
  'role.hint.group.admin': 'Decides who may join and what the group does.',
  'role.hint.site.reader': 'Looks on, changes nothing.',
  'role.hint.site.editor': 'Publishes new versions of this site.',
  'role.hint.site.admin': 'Decides who may reach this site and how it is set up.',

  'access.base.public': 'Anyone',
  'access.base.sso': 'Anyone who signs in with SSO Rijk',
  'access.base.site_team': 'The site team only',
  'access.base.nobody': 'Only through a link or an invitation',
  'access.hint.public': 'The site is public. Viewing it needs no sign-in.',
  'access.hint.sso': 'Anyone with an SSO Rijk account can view the site after signing in.',
  'access.hint.site_team': 'Only whoever has a role on this site or on its group.',
  'access.hint.nobody':
    'The address of the site grants no access by itself. Only the exceptions below let somebody in.',
  'access.keys': 'Secret links',
  'access.invitees': 'Invitees',
  'access.short.public': 'Public',
  'access.short.sso': 'SSO Rijk',
  'access.short.site_team': 'Site team',
  'access.short.nobody': 'Link or invitation',
  'access.extra.keys': 'secret links',
  'access.extra.invitees': 'invitees',
  'access.extra.and': ' and ',
  'access.label.only': 'Only {extras}',
  'access.label.plus': '{base} plus {extras}',
  'access.keys.hint':
    'Whoever has the full link can view the site without signing in. You see each link only once, right after creating it.',
  'access.invitees.hint':
    'Addresses on the list below can view the site after signing in with SSO Rijk. Their verified e-mail address has to be on the list.',
  'access.summary.public': 'Anyone can view the site, without signing in.',
  'access.summary.public.extras':
    'Anyone can view the site, without signing in. The exceptions below add nothing to that: whoever comes by is already allowed to look.',
  'access.summary.nobody':
    'Nobody can view the site. Turn on secret links or invitees below to let somebody in.',
  'access.summary.nobody.only': 'Only {extras}.',
  'access.summary.plus': '{base} On top of that: {extras}.',
  'access.summary.extra.keys': 'whoever has a valid secret link, without signing in',
  'access.summary.extra.invitees': 'invitees from the list, after signing in with SSO Rijk',
  'access.summary.extra.both': '{first}, and {second}',

  'shell.skip': 'Skip to main content',
  'shell.brand.label': 'Plak, to the overview',
  'shell.menu': 'Menu',
  'page.cliPair.title': 'Link a device',
  'page.done.title': 'The site is online',
  'nav.overview': 'Overview',
  'nav.groups': 'Groups',
  'nav.platform': 'Platform administration',
  'nav.account': 'Account',
  'nav.profile': 'Profile',
  'nav.devices': 'Linked devices',
  'nav.logout': 'Sign out',
  'footer.about': 'About Plak',
  'footer.accessibility': 'Accessibility',
  'footer.privacy': 'Privacy',
  'footer.api': 'API documentation',

  'profile.title': 'Profile',
  'profile.subtitle': 'Your account, and how the admin adapts itself to you.',
  'profile.account.heading': 'Your account',
  'profile.account.name': 'Name',
  'profile.account.email': 'E-mail address',
  'profile.account.role': 'Role on the platform',
  'profile.account.role.admin': 'Platform administrator',
  'profile.account.role.member': 'Member',
  'profile.account.unknown': 'Unknown',
  'profile.account.hint':
    'These details come from SSO Rijk; Plak cannot change them. If something is wrong, report it to your own organisation.',
  'profile.language.heading': 'Language of the admin',
  'profile.language.subtitle':
    'The choice lives on your account, so it holds on every device you sign in from.',
  'profile.language.auto': 'Follow my browser',
  'profile.language.auto.hint': 'Now: {language}. If your browser asks for neither, it becomes English.',
  'profile.language.nl.hint': 'The admin is then always in Dutch.',
  'profile.language.en.hint': 'The admin is then always in English.',
  'profile.language.saved': 'Language saved',
  'profile.language.saved.detail': 'The admin is now in Dutch.',
  'profile.language.saved.detail.en': 'The admin is now in English.',
  'profile.language.saved.detail.auto': 'The admin now follows your browser.',
  'profile.language.failed': 'Language not saved',
  'profile.language.failed.detail': 'Saving did not work.',
  'profile.devices.heading': 'Linked devices',
  'profile.devices.body':
    'Every device that gained access to your account through plak login, with a way to unlink it.',
  'profile.devices.link': 'View linked devices',

  'language.label': 'Language',
  'language.nl': 'Dutch',
  'language.en': 'English',
};

export const en: Catalogue = {
  ...shared,
  ...pagesEn,
  ...groupEn,
  ...adminEn,
  ...siteEn,
  ...publishEn,
};
