/**
 * Dutch catalogue section: the site detail page and its lighter tabs (overview, previews, versions, members).
 *
 * A section per area rather than one file of many hundreds of keys: this is
 * the unit a reviewer can hold in their head, and the unit a screen change
 * touches. nl.ts folds the sections into one catalogue; the key union is
 * inferred from that, so nothing here is optional.
 */
export const siteNl = {
  // -- The page itself: loading, tab bar, unknown site ----------------------
  'site.loading': 'Site laden',
  'site.notFound.title': 'Onbekende site',
  'site.notFound.detail': 'Geen site "{site}" in groep "{group}".',
  'site.tabs.label': 'Siteonderdelen',
  'site.tabs.overview': 'Overzicht',
  'site.tabs.previews': 'Previews',
  'site.tabs.versions': 'Versies',
  'site.tabs.access': 'Toegang',
  'site.tabs.members': 'Leden',
  'site.tabs.deploy': 'Deploy',

  // -- Overzicht tab --------------------------------------------------------
  'site.overview.loading': 'Sitegegevens laden',
  'site.overview.status.heading': 'Status',
  'site.overview.live': 'Live',
  'site.overview.addressLabel.public': 'Publieke URL',
  'site.overview.addressLabel.restricted': 'Adres van je site',
  'site.overview.copyAddress': 'Kopieer adres',
  'site.overview.openSite': 'Open site',
  'site.overview.copied': 'Adres gekopieerd.',
  'site.overview.copyFailed':
    'Kopiëren lukte niet. Selecteer het adres hierboven en kopieer het zelf.',
  'site.overview.lastDeploy': 'Laatste deploy: {timestamp}',
  'site.overview.noLiveVersion': 'Nog geen live versie',
  'site.overview.noLiveVersion.hint':
    'Publiceer een eerste versie met het formulier hieronder of via het tabblad Deploy.',
  'site.overview.publish.heading': 'Nieuwe versie publiceren',
  'site.overview.published.title': 'Versie gepubliceerd',
  'site.overview.published.detail': 'De nieuwe versie staat nu live.',
  'site.overview.danger.heading': 'Gevarenzone',
  'site.overview.danger.body':
    'Site verwijderen haalt alle versies, previews, genodigden, geheime links en de gekoppelde repository definitief weg, inclusief de bestanden op de server.',
  'site.overview.danger.action': 'Site verwijderen',
  'site.overview.danger.confirm.title': 'Site {group}/{site} verwijderen?',
  'site.overview.danger.confirm.text':
    'Dit kan niet ongedaan gemaakt worden. Alle versies, previews, genodigden, geheime links en de gekoppelde repository verdwijnen definitief.',
  'site.overview.danger.confirm.keep': 'Behoud site',
  'site.overview.danger.confirm.confirm': 'Verwijder deze site',
  'site.overview.delete.failed': 'Site niet verwijderd',
  'site.overview.delete.failed.detail': 'Verwijderen is niet gelukt.',

  // -- Previews tab ---------------------------------------------------------
  'site.previews.loading': 'Previews laden',
  'site.previews.heading': 'Previews',
  'site.previews.listLabel': 'Previews',
  'site.previews.intro':
    'Elke preview leeft op een eigen ref (bv. pr-42) en vervalt 30 dagen na de laatste deploy. De toegang volgt de site, tenzij je hieronder een eigen toegang instelt; dat is dan de hele toegang, basis en uitzonderingen samen.',
  'site.previews.empty': 'Geen previews',
  'site.previews.empty.hint':
    'Publiceer een preview met een preview-ref via het tabblad Deploy of de CI-action.',
  'site.previews.updated': 'Bijgewerkt {timestamp}',
  'site.previews.expires': 'Vervalt {timestamp}',
  'site.previews.accessChoice': 'Toegang',
  'site.previews.accessOverline': 'Toegang: {access}',
  'site.previews.sameAsSite': 'Zelfde als site',
  'site.previews.keys.turnOn': 'Geheime links aanzetten',
  'site.previews.keys.turnOff': 'Geheime links uitzetten',
  'site.previews.invitees.turnOn': 'Genodigden aanzetten',
  'site.previews.invitees.turnOff': 'Genodigden uitzetten',
  'site.previews.remove': 'Preview verwijderen',
  'site.previews.accessFailed': 'Toegang tot {ref} niet opgeslagen',
  'site.previews.accessFailed.detail': 'Opslaan is niet gelukt.',
  'site.previews.removeFailed': 'Preview {ref} niet verwijderd',
  'site.previews.removeFailed.detail': 'Verwijderen is niet gelukt.',

  // -- Versies tab ----------------------------------------------------------
  'site.versions.loading': 'Versies laden',
  'site.versions.heading': 'Versies',
  'site.versions.listLabel': 'Live-versies',
  'site.versions.intro':
    'De live-historie van deze site. Bekijk een oude versie voordat je hem terugzet; die weergave is alleen toegankelijk voor wie een rol heeft op deze site of op haar groep.',
  'site.versions.empty': 'Nog geen live-versies',
  'site.versions.empty.hint': 'Publiceer een eerste versie via het tabblad Overzicht of Deploy.',
  'site.versions.marker.live': 'Staat nu live',
  'site.versions.marker.notLive': 'Staat niet live',
  'site.versions.view': 'Bekijken',
  'site.versions.view.details': 'nieuw tabblad',
  'site.versions.setLive': 'Zet deze versie live',
  'site.versions.origin.uploadBy': 'Geüpload door {name}',
  'site.versions.origin.upload': 'Handmatig geüpload',
  'site.versions.origin.repository': 'Gepubliceerd door {repository}',
  'site.versions.origin.ci': 'Gepubliceerd door CI',
  'site.versions.setLive.done': 'Versie live gezet',
  'site.versions.setLive.done.detail': 'De versie van {timestamp} staat nu live.',
  'site.versions.setLive.failed': 'Versie niet live gezet',
  'site.versions.setLive.failed.detail': 'Live zetten is niet gelukt.',

  // -- Leden tab ------------------------------------------------------------
  'site.members.loading': 'Leden laden',
  'site.members.heading': 'Leden',
  'site.members.intro':
    'Iemand kan bij deze site via de groep of via een rol op alleen deze site. De ruimste van die twee bepaalt wat diegene hier mag: een siterol verbreedt, en neemt nooit iets af.',

  // -- Secret link: the two ways to share one key ---------------------------
  'site.secretLink.withCode': 'Link met code',
  'site.secretLink.withCode.hint':
    'Iedereen die deze link heeft, kan de pagina bekijken. Handig voor een chat of mail aan mensen die het allemaal mogen zien.',
  'site.secretLink.copyLink': 'Kopieer link',
  'site.secretLink.copyLink.label': 'Kopieer de link met code',
  'site.secretLink.linkCopied': 'Link gekopieerd.',
  'site.secretLink.copyLinkFailed':
    'Kopiëren lukte niet. Selecteer de link hierboven en kopieer hem zelf.',
  'site.secretLink.withoutCode': 'Link zonder code',
  'site.secretLink.withoutCode.hint':
    'Stuur de link via het ene kanaal en de code via het andere. De lezer wordt één keer om de code gevraagd.',
  'site.secretLink.code': 'Code',
  'site.secretLink.copyLinkWithoutCode': 'Kopieer link zonder code',
  'site.secretLink.linkWithoutCodeCopied': 'Link zonder code gekopieerd.',
  'site.secretLink.copyCode': 'Kopieer code',
  'site.secretLink.codeCopied': 'Code gekopieerd.',
  'site.secretLink.copyCodeFailed':
    'Kopiëren lukte niet. Selecteer de code hierboven en kopieer hem zelf.',
} as const;
