/**
 * Dutch catalogue section: the address section of the settings tab of a group
 * and of a site, where the address can be changed.
 *
 * A section per area rather than one file of many hundreds of keys: this is
 * the unit a reviewer can hold in their head, and the unit a screen change
 * touches. nl.ts folds the sections into one catalogue; the key union is
 * inferred from that, so nothing here is optional.
 */
export const addressNl = {
  // -- Headings ---------------------------------------------------------------
  'address.group.heading': 'Adres van de groep',
  'address.site.heading': 'Adres van de site',

  // -- Where it is, and where it used to be -----------------------------------
  'address.current.site': 'Het adres van deze site is {address}.',
  'address.current.group':
    'Het adres van deze groep is {slug}. Het staat in het adres van elke site, bijvoorbeeld {example}.',
  'address.current.group.empty':
    'Het adres van deze groep is {slug}. Het staat in het adres van elke site in deze groep.',
  'address.previous': '{address} stuurt door tot en met {date}.',
  'address.restore': 'Zet terug',
  'address.restore.label': 'Zet terug naar {address}',

  // -- Who may change it ------------------------------------------------------
  'address.readOnly.group': 'Alleen een beheerder van de groep kan het adres wijzigen.',
  'address.readOnly.site':
    'Alleen een beheerder van de site die ook lid is van de groep kan het adres wijzigen.',
  'address.readOnly.siteOnly':
    'Je beheert deze site, maar je bent geen lid van de groep. Het adres wijzigen kan alleen een sitebeheerder die ook lid is van de groep.',

  // -- The warning for an address that has been shared ------------------------
  'address.public.site': 'Deze site is openbaar.',
  'address.public.site.detail':
    'Mensen kunnen het adres hebben opgeslagen, in een document hebben gezet of hebben afgedrukt. Wijzig het adres alleen als het echt moet.',
  'address.public.group': 'In deze groep staan openbare sites.',
  'address.public.group.detail':
    'Hun adressen kunnen in documenten, e-mails of op papier staan. Wijzig het adres alleen als het echt moet.',

  // -- The field ----------------------------------------------------------------
  'address.field.label': 'Nieuw adres',
  'address.field.required': 'Een adres is nodig',
  'address.field.rule':
    'Een adres bevat alleen kleine letters, cijfers en koppeltekens, begint en eindigt niet met een koppelteken en is hoogstens 63 tekens lang',
  'address.field.differs': 'Het nieuwe adres verschilt van het huidige adres',
  'address.field.preview': 'Het nieuwe adres wordt {address}',
  'address.field.preview.empty': 'Het nieuwe adres verschijnt hier zodra je het invult.',
  // Stands where the new address will be in the consequences below, until it is typed.
  'address.placeholder.new': '<nieuw adres>',
  'address.placeholder.site': '<site>',

  // -- What it comes to, said before the button -------------------------------
  'address.consequences.self': 'Dit pas je zelf aan:',
  'address.consequences.workflow.site':
    'Automatisch publiceren stopt meteen. Pas {site} in je workflow en {flag} bij {command} aan naar {address}, en zet er {siteId} bij als dat er nog niet staat.',
  'address.consequences.workflow.group':
    'Automatisch publiceren stopt meteen, voor elke site in de groep. Pas {site} in je workflow en {flag} bij {command} aan naar {address}, en zet bij elke site haar site-ID erbij als dat er nog niet staat.',
  'address.consequences.content':
    'Staat het oude adres in je site, bijvoorbeeld in {canonical}, {ogUrl}, een sitemap of een vast pad? Publiceer de site dan vóór {date} opnieuw met het nieuwe adres. Tot en met {date} laden stijlen en scripts nog via het oude adres; daarna niet meer.',
  'address.consequences.secretLink':
    'Heb je een geheime link gedeeld? Na {date} vervang je het adres in de link door het nieuwe adres. Alles na {key} blijft hetzelfde.',
  'address.consequences.changes': 'Dit verandert er:',
  'address.consequences.sites.many':
    'Alle {count} sites in deze groep krijgen een nieuw adres. Bijvoorbeeld: {from} wordt {to}.',
  'address.consequences.sites.one': 'De site in deze groep krijgt een nieuw adres.',
  'address.consequences.redirect':
    'Het oude adres stuurt bezoekers door naar het nieuwe adres, tot en met {date}. Dat geldt alleen voor wie de site mag bekijken.',
  'address.consequences.expiry.site':
    'Na die datum werkt het oude adres niet meer. Dan kan een andere site dit adres krijgen, en leidt een oude link naar andere inhoud.',
  'address.consequences.expiry.group':
    'Na die datum werkt het oude adres niet meer. Dan kan een andere groep dit adres krijgen, en leidt een oude link naar andere inhoud.',
  'address.consequences.login': 'Bezoekers van een afgeschermde site moeten misschien opnieuw inloggen.',
  'address.consequences.previews':
    'Previews krijgen ook een nieuw adres. Links naar previews, bijvoorbeeld in een pull request, werken nog tot en met {date}.',
  'address.consequences.undo': 'Tot en met {date} kun je het oude adres terugzetten.',

  // -- The button and the confirmation ----------------------------------------
  'address.change': 'Adres wijzigen',
  'address.confirm.title.site': 'Adres van site {from} wijzigen in {to}?',
  'address.confirm.title.group': 'Adres van groep {from} wijzigen in {to}?',
  'address.confirm.text':
    'Oude links sturen door tot en met {date}. Publiceren vanuit een workflow werkt pas weer nadat je het nieuwe adres instelt.',
  'address.confirm.text.group.many':
    'Alle {count} sites krijgen een nieuw adres. Oude links sturen door tot en met {date}. Publiceren vanuit een workflow werkt pas weer nadat je het nieuwe adres instelt.',
  'address.confirm.text.group.one':
    'De site krijgt een nieuw adres. Oude links sturen door tot en met {date}. Publiceren vanuit een workflow werkt pas weer nadat je het nieuwe adres instelt.',
  'address.confirm.keep': 'Behoud huidig adres',
  'address.confirm.confirm': 'Wijzig adres',

  // -- What came of it --------------------------------------------------------
  'address.changed':
    'Het adres is gewijzigd. {from} stuurt tot en met {date} door naar {to}. Gebruik je automatisch publiceren of {command}? Pas het adres daar nu aan.',
  'address.error.invalid': 'Dit adres kan niet worden gebruikt. Kies een ander adres.',
  'address.error.exists.site':
    'Dit adres is al in gebruik of was kort geleden van een andere site. Kies een ander adres.',
  'address.error.exists.group':
    'Dit adres is al in gebruik of was kort geleden van een andere groep. Kies een ander adres.',
  'address.error.tooMany.site':
    'Deze site heeft al vijf oude adressen die nog doorsturen. Zet een oud adres terug, of wacht tot er een is vrijgekomen.',
  'address.error.tooMany.group':
    'Deze groep heeft al vijf oude adressen die nog doorsturen. Zet een oud adres terug, of wacht tot er een is vrijgekomen.',
  'address.error.failed': 'Adres niet gewijzigd',
  'address.error.failed.detail': 'Wijzigen is niet gelukt.',
} as const;
