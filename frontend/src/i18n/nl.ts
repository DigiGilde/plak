/**
 * The Dutch catalogue, and the shape every other language has to match.
 *
 * A TypeScript object rather than JSON on purpose: the key union is inferred
 * from this file, so a key that does not exist is a compile error and a
 * language that misses one fails `vue-tsc`. A forgotten translation should
 * break the build, not surface as a raw key in front of a civil servant.
 *
 * Keys are grouped by where the words appear, not by what they say, because
 * that is how you find them again when a screen changes.
 */
import { addressNl } from './address.nl';
import { adminNl } from './admin.nl';
import { designSystemNl } from './designSystem.nl';
import { groupNl } from './group.nl';
import { pagesNl } from './pages.nl';
import { publishNl } from './publish.nl';
import { siteNl } from './site.nl';

/**
 * What every screen shares: the ways an error is told, the roles, the access
 * levels and the app shell. Everything that belongs to one area lives in the
 * section beside this file.
 */
const shared = {
  // -- Errors and the ways out ---------------------------------------------
  'error.generic.title': 'Er ging iets mis',
  'error.generic.detail': 'Probeer het opnieuw of herlaad de pagina.',
  'error.expired.title': 'Je bent niet meer ingelogd',
  'error.expired.detail':
    'Je sessie is verlopen of beëindigd. Log opnieuw in om verder te gaan waar je was.',
  'error.expired.action': 'Opnieuw inloggen',
  // What this interface can add to a refusal that holds for every client of
  // the API: where, on this screen, the thing it names is changed.
  'error.guidance.ALREADY_GROUP_MEMBER':
    'De rol wijzig je in het menu achter de regel van dit lid.',
  'error.guidance.ALREADY_SITE_MEMBER':
    'Die rol wijzig je in het menu achter de regel van dit lid.',
  // A site or group that is not found, on the page of a site or a group: the
  // address may have been changed since the link was made.
  'error.renamed.hint': 'Is de site of groep hernoemd? Zoek hem dan in je overzicht.',
  'error.renamed.link': 'Naar het overzicht',

  // -- The development notice, on both hosts --------------------------------
  // One line: nldd-status-bar shows one and cuts the rest off with an
  // ellipsis. The first word carries the message, so it survives the
  // truncation on a narrow screen.
  'beta.bar': 'Bètaversie - Plak is in ontwikkeling en kan fouten bevatten',

  // -- Roles ----------------------------------------------------------------
  'role.reader': 'Lezer',
  'role.editor': 'Redacteur',
  'role.admin': 'Beheerder',
  'role.hint.group.reader': 'Kijkt mee, verandert niets.',
  'role.hint.group.editor': 'Zet sites online en beheert de inhoud.',
  'role.hint.group.admin': 'Bepaalt wie erbij mag en wat de groep doet.',
  'role.hint.site.reader': 'Kijkt mee, verandert niets.',
  'role.hint.site.editor': 'Publiceert nieuwe versies van deze site.',
  'role.hint.site.admin': 'Bepaalt wie bij deze site mag en hoe hij is ingesteld.',

  // -- Access: the base and the two exceptions -------------------------------
  'access.base.public': 'Iedereen',
  'access.base.sso': 'Iedereen die inlogt met SSO Rijk',
  'access.base.site_team': 'Alleen het siteteam',
  'access.base.nobody': 'Alleen via een link of uitnodiging',
  'access.hint.public': 'De site is openbaar. Bekijken kan zonder in te loggen.',
  'access.hint.sso':
    'Iedereen met een account bij SSO Rijk kan de site bekijken na inloggen.',
  'access.hint.site_team': 'Alleen wie een rol heeft op deze site of op haar groep.',
  'access.hint.nobody':
    'Het adres van de site geeft op zichzelf geen toegang. Alleen de uitzonderingen hieronder laten iemand binnen.',
  'access.keys': 'Geheime links',
  'access.invitees': 'Genodigden',
  'access.short.public': 'Publiek',
  'access.short.sso': 'SSO Rijk',
  'access.short.site_team': 'Siteteam',
  'access.short.nobody': 'Link of uitnodiging',
  'access.extra.keys': 'geheime links',
  'access.extra.invitees': 'genodigden',
  'access.extra.and': ' en ',
  'access.label.only': 'Alleen {extras}',
  'access.label.plus': '{base} plus {extras}',
  'access.keys.hint':
    'Wie de volledige link heeft, kan de site bekijken zonder in te loggen. Je ziet elke link maar één keer, direct na het aanmaken.',
  'access.invitees.hint':
    'Adressen op de lijst hieronder kunnen de site bekijken na inloggen met SSO Rijk. Hun geverifieerde e-mailadres moet op de lijst staan.',
  'access.summary.public': 'Iedereen kan de site bekijken, ook zonder in te loggen.',
  'access.summary.public.extras':
    'Iedereen kan de site bekijken, ook zonder in te loggen. De uitzonderingen hieronder voegen daar niets aan toe: wie langskomt, mag toch al kijken.',
  'access.summary.nobody':
    'Niemand kan de site bekijken. Zet hieronder geheime links of genodigden aan om iemand binnen te laten.',
  'access.summary.nobody.only': 'Alleen {extras}.',
  'access.summary.plus': '{base} Daarnaast: {extras}.',
  'access.summary.extra.keys':
    'wie een geldige geheime link heeft, ook zonder in te loggen',
  'access.summary.extra.invitees': 'genodigden van de lijst, na inloggen met SSO Rijk',
  'access.summary.extra.both': '{first}, en {second}',

  // -- The language choice itself -------------------------------------------
  // -- The app shell: toolbar, menu, footer ---------------------------------
  'shell.skip': 'Direct naar de inhoud',
  'shell.brand.label': 'Plak, naar het overzicht',
  'shell.menu': 'Menu',
  'page.cliPair.title': 'CLI-sessie koppelen',
  'page.done.title': 'Site staat online',
  'nav.overview': 'Overzicht',
  'nav.groups': 'Groepen',
  'nav.platform': 'Platformbeheer',
  'nav.account': 'Account',
  'nav.profile': 'Profiel',
  'nav.sessions': 'Gekoppelde CLI-sessies',
  'nav.logout': 'Uitloggen',
  'footer.whatsNew': 'Wat is er nieuw',
  'footer.version': 'Versie {version}',
  'footer.about': 'Over Plak',
  'footer.accessibility': 'Toegankelijkheid',
  'footer.privacy': 'Privacy',
  'footer.api': 'API-documentatie',
  'footer.language.en': 'English',
  'footer.language.nl': 'Nederlands',

  // -- Profile --------------------------------------------------------------
  'profile.title': 'Profiel',
  'profile.subtitle': 'Je account en hoe het beheer zich aan jou aanpast.',
  'profile.account.heading': 'Je account',
  'profile.account.name': 'Naam',
  'profile.account.email': 'E-mailadres',
  'profile.account.role': 'Rol op het platform',
  'profile.account.role.admin': 'Platformbeheerder',
  'profile.account.role.member': 'Lid',
  'profile.account.unknown': 'Onbekend',
  'profile.account.hint':
    'Deze gegevens komen van SSO Rijk; Plak kan ze niet wijzigen. Klopt er iets niet, meld dat dan bij je eigen organisatie.',
  'profile.language.heading': 'Taal van het beheer',
  'profile.language.subtitle':
    'De keuze staat op je account, dus hij geldt op elk apparaat waarop je inlogt.',
  'profile.language.auto': 'Volg mijn browser',
  'profile.language.auto.hint': 'Nu: {language}. Vraagt je browser om geen van beide, dan wordt het Engels.',
  'profile.language.nl.hint': 'Het beheer is dan altijd in het Nederlands.',
  'profile.language.en.hint': 'Het beheer is dan altijd in het Engels.',
  'profile.language.saved': 'Taal opgeslagen',
  'profile.language.saved.detail': 'Het beheer staat nu in het Nederlands.',
  'profile.language.saved.detail.en': 'Het beheer staat nu in het Engels.',
  'profile.language.saved.detail.auto': 'Het beheer volgt nu je browser.',
  'profile.language.failed': 'Taal niet opgeslagen',
  'profile.language.failed.detail': 'Opslaan is niet gelukt.',
  'profile.sessions.heading': 'Gekoppelde CLI-sessies',
  'profile.sessions.body':
    'Elke aanmelding met plak login die nog toegang heeft tot je account, met de mogelijkheid die in te trekken.',
  'profile.sessions.install.before': 'Nog geen Plak-CLI? ',
  'profile.sessions.install.link': 'Zo installeer en gebruik je hem.',
  'profile.sessions.link': 'Bekijk gekoppelde CLI-sessies',

  'language.label': 'Taal',
  'language.nl': 'Nederlands',
  'language.en': 'Engels',
} as const;

/**
 * The whole catalogue. Spreading typed objects keeps the literal keys, so the
 * union below stays exact and a key that does not exist is a compile error.
 */
export const nl = {
  ...shared,
  ...pagesNl,
  ...groupNl,
  ...addressNl,
  ...adminNl,
  ...siteNl,
  ...publishNl,
  ...designSystemNl,
} as const;

/** Every key the interface may ask for. */
export type MessageKey = keyof typeof nl;

/** The shape a language file has to fill completely. */
export type Catalogue = Record<MessageKey, string>;
