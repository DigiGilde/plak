/**
 * Dutch catalogue section: platform administration, linked sessions and the components several screens share (error banner, confirmation, row menu, notifications).
 *
 * A section per area rather than one file of many hundreds of keys: this is
 * the unit a reviewer can hold in their head, and the unit a screen change
 * touches. nl.ts folds the sections into one catalogue; the key union is
 * inferred from that, so nothing here is optional.
 */
export const adminNl = {
  // -- Column headers shared by the admin tables ----------------------------
  'admin.column.member': 'Lid',
  'admin.column.role': 'Rol',
  'admin.column.actions': 'Acties',

  // -- Shared controls ------------------------------------------------------
  'admin.retry': 'Opnieuw proberen',
  'admin.confirm.keep': 'Behoud',
  'admin.confirm.phrase.label': 'Typ {phrase} om te bevestigen',
  'admin.confirm.phrase.mismatch': 'Precies {phrase}',
  'admin.rowActions.label': 'Acties voor {label}',
  'admin.notice.unknownError': 'Onbekende fout.',
  'admin.errorBanner.status': 'Foutcode {status}.',

  // -- Platform administration ----------------------------------------------
  'admin.members.column.created': 'Aangemaakt',
  'admin.members.column.activity': 'Laatst ingelogd',
  'admin.members.search.label': 'Zoek een lid op naam of e-mailadres',
  'admin.members.search.placeholder': 'Zoek een lid',
  'admin.members.loading': 'Leden laden...',
  'admin.members.loadFailed': 'Onbekende fout bij het ophalen van leden.',
  'admin.members.table.label': 'Platformleden',
  'admin.members.empty': 'Er zijn nog geen leden om te tonen.',
  'admin.members.notFound': 'Geen lid gevonden.',
  'admin.members.notFound.detail': "Niets komt overeen met '{query}'.",
  'admin.members.standing.admin': 'Platformbeheerder',
  'admin.members.standing.member': 'Lid',
  'admin.members.standing.noAccess': 'Geen toegang',
  'admin.members.action.revoke': 'Toegang intrekken',
  'admin.members.action.restore': 'Toegang teruggeven',
  'admin.members.action.promote': 'Maak platformbeheerder',
  'admin.members.action.demote': 'Beheerdersrol afnemen',
  'admin.members.blocked.bootstrap': 'bootstrap-account',
  'admin.members.blocked.lastAdmin': 'laatste beheerder',
  'admin.members.roleFailed': 'De rol van {name} wijzigen is niet gelukt',
  'admin.members.activateFailed': '{name} activeren is niet gelukt',
  'admin.members.deactivateFailed': '{name} deactiveren is niet gelukt',

  // -- Linked sessions ------------------------------------------------------
  'admin.sessions.subtitle': 'Elke aanmelding met {command} die nog toegang heeft tot je account.',
  'admin.sessions.loading': 'Sessies laden',
  'admin.sessions.empty': 'Nog geen gekoppelde sessies',
  'admin.sessions.empty.detail':
    'Voer plak login uit op je computer om de Plak-CLI te koppelen aan je account.',
  'admin.sessions.empty.install.before': 'Nog geen Plak-CLI? ',
  'admin.sessions.empty.install.link': 'Zo installeer en gebruik je hem.',
  'admin.sessions.unknownClient': 'Onbekend programma',
  'admin.sessions.neverUsed': 'nog niet',
  'admin.sessions.times': 'Gekoppeld {linked} - laatst gebruikt {lastUsed} - verloopt {expires}',
  'admin.sessions.revoke': 'Intrekken',
  'admin.sessions.revoke.label': 'Sessie van {name} intrekken',
  'admin.sessions.confirm.title': 'Sessie van {name} intrekken?',
  'admin.sessions.confirm.text':
    'Deze sessie verliest meteen toegang tot je account; opnieuw koppelen gaat met plak login.',
  'admin.sessions.confirm.keep': 'Behoud sessie',
  'admin.sessions.confirm.confirm': 'Sessie intrekken',
  'admin.sessions.revokeFailed': 'Sessie van {name} niet ingetrokken',
  'admin.sessions.revokeFailed.detail': 'Intrekken is niet gelukt.',

  // -- Site members: the Leden tab of a site --------------------------------
  'admin.siteMembers.inherited.summary.one': '{count} lid via de groep {group}',
  'admin.siteMembers.inherited.summary.many': '{count} leden via de groep {group}',
  'admin.siteMembers.inherited.note':
    'Deze rollen gelden in de hele groep en wijzig je daar, niet hier: {link}.',
  'admin.siteMembers.inherited.link': 'leden van de groep {group}',
  'admin.siteMembers.inherited.table': 'Leden via de groep',
  'admin.siteMembers.table': 'Leden met een rol op deze site',
  'admin.siteMembers.empty': 'Niemand heeft een eigen rol op deze site',
  'admin.siteMembers.empty.detail': 'Geef hieronder iemand een rol die alleen hier geldt.',
  'admin.siteMembers.viaGroup': 'via de groep',
  'admin.siteMembers.keepsViaGroup': 'Blijft {role} via de groep',
  'admin.siteMembers.action.setRole': 'Maak {role}',
  'admin.siteMembers.action.remove': 'Siterol weghalen',
  'admin.siteMembers.confirm.remove.title': 'Siterol van {name} weghalen?',
  'admin.siteMembers.confirm.remove.keepsViaGroup':
    '{name} houdt toegang tot deze site als {role} via de groep.',
  'admin.siteMembers.confirm.remove.noAccess':
    '{name} verliest de toegang tot deze site: er is geen rol via de groep die dat opvangt.',
  'admin.siteMembers.confirm.remove.keep': 'Behoud siterol',
  'admin.siteMembers.confirm.remove.confirm': 'Siterol weghalen',
  'admin.siteMembers.addFailed': '{name} een rol op deze site geven is niet gelukt',
  'admin.siteMembers.roleFailed': 'De siterol van {name} wijzigen is niet gelukt',
  'admin.siteMembers.removeFailed': 'De siterol van {name} weghalen is niet gelukt',
  'admin.siteMembers.suggestion.alreadyMember': 'heeft al een siterol',
  'admin.siteMembers.suggestion.viaGroup': '{role} via de groep',
  'admin.siteMembers.form.heading': 'Iemand een rol op deze site geven',
  'admin.siteMembers.form.hint':
    'Een siterol verbreedt alleen: wie via de groep al meer mag, houdt dat. Toegang weghalen doe je bij de groep. Iemand moet eerst zelf op het beheer ingelogd hebben; Plak maakt hier geen account aan.',
  'admin.siteMembers.form.identifier': 'Naam of e-mailadres',
  'admin.siteMembers.form.identifier.placeholder': 'Typ een naam of e-mailadres',
  'admin.siteMembers.form.identifier.help':
    'De lijst begint met de leden van deze groep, met de rol die ze daar al hebben. Vanaf twee letters zoeken we mee in de namen en adressen van iedereen die al eens ingelogd heeft, ook buiten de groep.',
  'admin.siteMembers.form.identifier.required': 'Iemand uit de lijst',
  'admin.siteMembers.form.searchFurther':
    'Typ twee letters om verder te zoeken, ook buiten deze groep.',
  'admin.siteMembers.form.tooShort': 'Typ twee letters om te zoeken',
  'admin.siteMembers.form.searching': 'Zoeken...',
  'admin.siteMembers.form.noSuggestions': 'Niemand gevonden',
  'admin.siteMembers.form.role': 'Rol',
  'admin.siteMembers.form.role.noEffect':
    'Dit verandert niets aan wat deze persoon hier mag: via de groep is dat al {role}.',
  'admin.siteMembers.form.submit': 'Rol geven',

  // -- Platform administration: the content volume --------
  'admin.volume.heading': 'Contentvolume',
  'admin.volume.loading': 'Contentvolume wordt geladen...',
  'admin.volume.summary.label': 'Vulling van het contentvolume',
  'admin.volume.used': 'In gebruik',
  'admin.volume.used.value': '{used} van {total}',
  'admin.volume.free': 'Vrij',
  'admin.volume.reserve': 'Reserve',
  'admin.volume.reserve.off': 'Uit',
  'admin.volume.low.title': 'Weinig ruimte over op het contentvolume',
  'admin.volume.low.detail':
    'Een deploy van de maximale omvang ({maxDeploy}) zou het volume onder de reserve van {reserve} brengen. Maak ruimte vrij of vergroot het volume.',
} as const;
