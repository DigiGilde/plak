/**
 * Dutch catalogue section: the overview, the groups page and everything on one group (sites, members, settings).
 *
 * A section per area rather than one file of many hundreds of keys: this is
 * the unit a reviewer can hold in their head, and the unit a screen change
 * touches. nl.ts folds the sections into one catalogue; the key union is
 * inferred from that, so nothing here is optional.
 */
export const groupNl = {
  // -- Actions and states shared by more than one of these screens ---------
  'group.action.publishSite': 'Zet een site online',
  'group.action.newGroup': 'Groep aanmaken',
  'group.drop.release': 'Laat los om te uploaden',

  // -- Overview ------------------------------------------------------------
  'group.overview.heading': 'Overzicht',
  'group.overview.loading': 'Overzicht wordt geladen...',
  'group.overview.error': 'Overzicht laden mislukt',
  'group.overview.errorFallback': 'Onbekende fout',
  'group.overview.empty': 'Er staat hier nog niets online.',
  'group.overview.empty.supportingText':
    'Kies een bestand of een archief van je map, geef het een titel, en zet het online. Een groep maak je in dezelfde stap aan.',
  'group.overview.table.label': 'Sites in {group}',
  'group.overview.table.empty': 'Nog geen sites in deze groep.',

  // -- The site table, shared by the overview and the group's sites tab ----
  'group.sites.column.live': 'Live',
  'group.sites.column.site': 'Site',
  'group.sites.column.access': 'Toegang',
  'group.sites.column.lastPublished': 'Laatste publicatie',
  'group.siteRow.live': 'Heeft een live versie',
  'group.siteRow.notLive': 'Geen live versie',
  'group.siteRow.neverPublished': 'Nog niet gepubliceerd',

  // -- Groups page ---------------------------------------------------------
  'group.groups.heading': 'Groepen',
  'group.groups.loading': 'Groepen worden geladen...',
  'group.groups.list.label': 'Groepen',
  'group.groups.empty': 'Je zit nog in geen enkele groep.',
  'group.groups.empty.supportingText': 'Maak er een aan; daarna zet je er sites in.',
  'group.groups.sites.none': 'Nog geen sites',
  'group.groups.sites.one': '1 site',
  'group.groups.sites.many': '{count} sites',
  'group.groups.online.none': 'Niets online',
  'group.groups.online.one': '1 site online',
  'group.groups.online.many': '{count} sites online',

  // -- Group page: header and tab bar --------------------------------------
  'group.page.loading': 'Groep wordt geladen…',
  'group.page.tabs.label': 'Groepsonderdelen',
  'group.page.tab.sites': 'Sites',
  'group.page.tab.members': 'Leden',
  'group.page.tab.settings': 'Instellingen',

  // -- New group sheet -----------------------------------------------------
  'group.new.title': 'Nieuwe groep',
  'group.new.name.label': 'Naam',
  'group.new.name.required': 'Een naam',
  'group.new.slug.label': 'Slug (in de URL)',
  'group.new.slug.required': 'Een slug',
  'group.new.slug.pattern':
    'Alleen kleine letters, cijfers en koppeltekens, geen koppelteken aan begin of eind',
  'group.new.cancel': 'Annuleren',

  // -- Group page: sites tab -----------------------------------------------
  'group.sites.heading': 'Sites',
  'group.sites.intro':
    'De sites van deze groep. Open een site om te publiceren, de toegang te regelen of de versies te bekijken.',
  'group.sites.table.label': 'Sites in deze groep',
  'group.sites.table.empty': 'Nog geen sites in deze groep',
  'group.sites.table.empty.supportingText':
    "Kies 'Zet een site online' om de eerste erin te zetten.",

  // -- Group page: members tab ---------------------------------------------
  'group.members.heading': 'Leden',
  'group.members.intro':
    'De rol bepaalt wat iemand in deze groep mag: een lezer kijkt mee, een redacteur zet sites online, een beheerder bepaalt wie erbij mag. Iedereen op deze lijst ziet ook wat is ingesteld op "Alleen het siteteam". Toevoegen gaat op het geverifieerde e-mailadres waarmee iemand met SSO Rijk inlogt.',
  'group.members.table.label': 'Leden van deze groep',
  'group.members.column.member': 'Lid',
  'group.members.column.role': 'Rol',
  'group.members.column.actions': 'Acties',
  'group.members.empty': 'Nog geen groepsleden',
  'group.members.empty.supportingText': 'Voeg het eerste groepslid hieronder toe.',
  'group.members.suggestion.alreadyMember': 'al lid',
  'group.members.action.setRole': 'Maak {role}',
  'group.members.action.remove': 'Uit de groep halen',
  'group.members.confirm.remove.title': '{name} uit de groep halen?',
  'group.members.confirm.remove.text':
    '{name} is dan geen {role} meer in deze groep en verliest die rol op elke site erin.',
  'group.members.confirm.remove.noSiteRoles':
    '{name} heeft in deze groep geen eigen siterol, dus verder verandert er niets.',
  'group.members.confirm.remove.siteRoles.one':
    'Op 1 site in deze groep heeft {name} daarnaast een eigen siterol:',
  'group.members.confirm.remove.siteRoles.many':
    'Op {count} sites in deze groep heeft {name} daarnaast een eigen siterol:',
  'group.members.confirm.remove.siteRoles.list': 'Sites in deze groep met een eigen siterol',
  'group.members.confirm.remove.siteRoles.more.one': 'En nog 1 site',
  'group.members.confirm.remove.siteRoles.more.many': 'En nog {count} sites',
  'group.members.confirm.remove.siteRoles.also': 'Haal deze siterollen ook weg',
  'group.members.confirm.remove.siteRoles.keeps':
    'Laat je dit uit staan, dan houdt {name} die siterollen en kan daarmee nog steeds bij die sites.',
  'group.members.confirm.remove.keep': 'Behoud het lidmaatschap',
  'group.members.confirm.remove.confirm': 'Uit de groep halen',
  'group.members.confirm.remove.confirmWithSiteRoles': 'Uit de groep halen en siterollen weghalen',
  'group.members.addFailed': 'Groepslid {name} toevoegen is niet gelukt',
  'group.members.roleChangeFailed': 'De rol van {name} wijzigen is niet gelukt',
  'group.members.removeFailed': '{name} uit de groep halen is niet gelukt',
  'group.members.retry': 'Opnieuw proberen',
  'group.members.add.legend': 'Lid toevoegen',
  'group.members.add.supportingText':
    'Iemand moet eerst zelf op het beheer ingelogd hebben; Plak maakt hier geen account aan.',
  'group.members.add.identifier.label': 'Naam of e-mailadres',
  'group.members.add.identifier.placeholder': 'Typ een naam of e-mailadres',
  'group.members.add.identifier.help':
    'Vanaf twee letters zoeken we mee in de namen en adressen van iedereen die al eens ingelogd heeft. Wie al lid is, staat er met die vermelding bij.',
  'group.members.add.identifier.required': 'Iemand uit de lijst',
  'group.members.add.suggestions.tooShort': 'Typ twee letters om te zoeken',
  'group.members.add.suggestions.searching': 'Zoeken...',
  'group.members.add.suggestions.empty': 'Niemand gevonden',
  'group.members.add.role.label': 'Rol',
  'group.members.add.submit': 'Lid toevoegen',

  // -- Group page: settings tab --------------------------------------------
  'group.settings.heading': 'Standaardtoegang voor nieuwe sites',
  'group.settings.intro':
    'Hiermee begint een nieuwe site in deze groep. Per site blijft de toegang daarna los instelbaar, op het tabblad Toegang van die site. Bestaande sites veranderen niet mee.',
  'group.settings.list.label': 'Standaardtoegang',
  'group.settings.saved': 'Standaardtoegang opgeslagen',
  'group.settings.saveFailed': 'Standaardtoegang niet opgeslagen',
  'group.settings.saveFailed.detail': 'Opslaan is niet gelukt.',
  'group.settings.danger.heading': 'Gevarenzone',
  'group.settings.danger.body':
    'Groep verwijderen haalt de groep, haar leden en al haar sites definitief weg, met alle versies, previews, genodigden, geheime links en de bestanden op de server.',
  'group.settings.danger.action': 'Groep verwijderen',
  'group.settings.danger.confirm.title': 'Groep {group} verwijderen?',
  'group.settings.danger.confirm.text':
    'Dit kan niet ongedaan gemaakt worden. De groep en alle lidmaatschappen verdwijnen definitief.',
  'group.settings.danger.confirm.text.withSites':
    'Dit kan niet ongedaan gemaakt worden. De groep, alle lidmaatschappen en deze sites verdwijnen definitief, met alles wat erop staat:',
  'group.settings.danger.confirm.sites.list': 'Sites die meegaan',
  'group.settings.danger.confirm.sites.more.one': 'En nog 1 site',
  'group.settings.danger.confirm.sites.more.many': 'En nog {count} sites',
  'group.settings.danger.confirm.keep': 'Behoud groep',
  'group.settings.danger.confirm.confirm': 'Verwijder deze groep',
  'group.settings.delete.failed': 'Groep niet verwijderd',
  'group.settings.delete.failed.detail': 'Verwijderen is niet gelukt.',
} as const;
