/**
 * Presentation helpers shared by several tabs: timestamp formatting and the
 * plain-language labels and explanations for the access base, the two extras
 * and the combinations they make.
 *
 * Everything here is a function rather than a constant record, because every
 * value depends on the language on screen and that can change without a
 * reload. Read inside a template or a computed, `t()` and `intlLocale` track
 * the current language, so the screen follows a change of choice by itself.
 */
import type { Access, AccessBase, Role } from './api/types';
import { intlLocale, t } from './i18n';

/**
 * `Intl` objects are expensive to build and cheap to keep, and there are two
 * languages, so one per language and per shape is the whole cache.
 */
const dateTimeFormats = new Map<string, Intl.DateTimeFormat>();

function formatter(options: Intl.DateTimeFormatOptions, shape: string): Intl.DateTimeFormat {
  const key = `${intlLocale.value}:${shape}`;
  let format = dateTimeFormats.get(key);
  if (!format) {
    format = new Intl.DateTimeFormat(intlLocale.value, options);
    dateTimeFormats.set(key, format);
  }
  return format;
}

export function formatTimestamp(iso: string | null | undefined): string {
  return iso
    ? formatter({ dateStyle: 'medium', timeStyle: 'short' }, 'datetime').format(new Date(iso))
    : '-';
}

export function formatDate(iso: string | null | undefined): string {
  return iso ? formatter({ dateStyle: 'medium' }, 'date').format(new Date(iso)) : '-';
}

/** A number in the conventions of the language on screen (grouping, decimals). */
export function formatNumber(value: number, options?: Intl.NumberFormatOptions): string {
  return new Intl.NumberFormat(intlLocale.value, options).format(value);
}

/**
 * The interface names the roles in the language of its reader; the wire
 * carries the English enum. Keeping the two apart is what lets the interface
 * be bilingual without touching the database.
 */
export function roleLabel(role: Role): string {
  return t(`role.${role}`);
}

/**
 * One line each, for the picker and for the hint under a members list. Two
 * sets, because the same role means something narrower one level down: an
 * admin of a group decides about the whole group, an admin of one site only
 * about that site. One shared set would say "the group" on a site page.
 */
export function roleHint(role: Role): string {
  return t(`role.hint.group.${role}`);
}

export function siteRoleHint(role: Role): string {
  return t(`role.hint.site.${role}`);
}

/**
 * One icon per role, for a row menu where the label has to stay short. They
 * name the deed rather than the person: looking, changing, holding the key.
 */
export const ROLE_ICONS: Record<Role, string> = {
  reader: 'eye',
  editor: 'pencil',
  admin: 'key',
};

/** Narrowest first, which is the order a picker should offer them in. */
export const ROLES: Role[] = ['reader', 'editor', 'admin'];

export function accessBaseLabel(base: AccessBase): string {
  return t(`access.base.${base}`);
}

export function accessBaseHint(base: AccessBase): string {
  return t(`access.hint.${base}`);
}

/**
 * Short forms for a badge or a tag, where the full sentence does not fit. The
 * long labels double as the radio options, and a whole sentence on a table row
 * would push every other column off a phone.
 */
export function accessBaseShort(base: AccessBase): string {
  return t(`access.short.${base}`);
}

/** The base plus its extras in a handful of words, for a badge or a tag. */
export function accessLabel(access: Access): string {
  if (access.base === 'public') {
    return accessBaseShort('public');
  }
  const extras: string[] = [];
  if (access.keys) extras.push(t('access.extra.keys'));
  if (access.invitees) extras.push(t('access.extra.invitees'));
  if (extras.length === 0) {
    return accessBaseShort(access.base);
  }
  const joined = extras.join(t('access.extra.and'));
  return access.base === 'nobody'
    ? t('access.label.only', { extras: joined })
    : t('access.label.plus', { base: accessBaseShort(access.base), extras: joined });
}

export function keysLabel(): string {
  return t('access.keys');
}

export function keysHint(): string {
  return t('access.keys.hint');
}

export function inviteesLabel(): string {
  return t('access.invitees');
}

export function inviteesHint(): string {
  return t('access.invitees.hint');
}

/**
 * One sentence saying who can really see the site, for the combination as a
 * whole. Three separate controls each explaining themselves still leave the
 * reader to add them up, and that sum is exactly the thing they came for.
 *
 * On a public base the extras are said to add nothing rather than quietly
 * ignored: the switches stay operable, so the interface owes an explanation of
 * why turning one on changes nothing on the site.
 */
export function accessSummary(access: Access): string {
  if (access.base === 'public') {
    return access.keys || access.invitees
      ? t('access.summary.public.extras')
      : t('access.summary.public');
  }
  const extras: string[] = [];
  if (access.keys) extras.push(t('access.summary.extra.keys'));
  if (access.invitees) extras.push(t('access.summary.extra.invitees'));
  if (extras.length === 0) {
    return access.base === 'nobody' ? t('access.summary.nobody') : accessBaseHint(access.base);
  }
  const joined =
    extras.length === 2
      ? t('access.summary.extra.both', { first: extras[0], second: extras[1] })
      : extras[0];
  return access.base === 'nobody'
    ? t('access.summary.nobody.only', { extras: joined })
    : t('access.summary.plus', { base: accessBaseHint(access.base), extras: joined });
}

/**
 * Absolute URL on the content host for a content path such as
 * `/group/site/_preview/pr-42/`. `base` is the `contentBaseUrl` from
 * `/me`; never `window.location.origin`, which is the admin host, where
 * content paths return a 404.
 */
export function contentUrl(base: string, path: string): string {
  const origin = base.replace(/\/+$/, '');
  return `${origin}${path.startsWith('/') ? path : `/${path}`}`;
}

/** Public base URL of a site's live site, on the content host. */
export function siteUrl(base: string, group: string, site: string): string {
  return contentUrl(base, `/${group}/${site}/`);
}
