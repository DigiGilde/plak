/**
 * Dutch catalogue section: the public pages and the flows around them (front page, About, Privacy, Accessibility, linking a CLI session, the result screen after publishing).
 *
 * A section per area rather than one file of many hundreds of keys: this is
 * the unit a reviewer can hold in their head, and the unit a screen change
 * touches. nl.ts folds the sections into one catalogue; the key union is
 * inferred from that, so nothing here is optional.
 */
export const pagesNl = {
  // -- Landing: access withdrawn -------------------------------------------
  'page.landing.withdrawn.title': 'Je toegang is ingetrokken',
  'page.landing.withdrawn.intro':
    'Je bent ingelogd met je organisatieaccount, maar Plak laat je niet meer binnen.',
  'page.landing.withdrawn.body':
    'Je account staat er nog, met alles wat eraan hangt; alleen de toegang is dicht. Weet je niet waarom, vraag het dan aan de platformbeheerder van je organisatie. Die kan je toegang ook teruggeven.',
  'page.landing.withdrawn.check': 'Controleer opnieuw',
  'page.landing.withdrawn.failed.title': 'Controle mislukt',
  'page.landing.withdrawn.failed.detail': 'Plak is even niet bereikbaar. Probeer het zo nog eens.',

  // -- Landing: no session --------------------------------------------------
  'page.landing.intro':
    'Snel en eenvoudig een HTML-pagina delen. Een rapport, analyse of overzicht, zelf gemaakt of met een AI-assistent: zet het op Plak en deel de link. Jij bepaalt wie het mag zien.',
  'page.landing.loginHint': 'Log in met je Rijksoverheid-account om te beginnen.',
  'page.landing.login': 'Inloggen',
  'page.landing.steps.heading': 'Zo deel je een pagina',
  'page.landing.steps.upload':
    'Zet je HTML-bestand op Plak door het te uploaden, of laat je AI-assistent dit doen.',
  'page.landing.steps.audience':
    "Kies wie het mag zien: iedereen, collega's die inloggen, alleen wie je uitnodigt, of wie de geheime link heeft.",
  'page.landing.steps.share': 'Deel de link.',
  'page.landing.steps.update':
    "Wil je iets wijzigen, dan upload je een nieuwe versie. Je collega's zien altijd de laatste, en oude versies blijven beschikbaar.",
  'page.landing.site.heading': 'Ook voor een hele site',
  'page.landing.site.body':
    "Werk je aan een site met meerdere pagina's in een repository? Publiceer dan vanuit GitHub of code.overheid.nl, met een preview per pull request.",

  // -- Start ----------------------------------------------------------------
  'page.start.loading': 'Bezig met laden...',

  // -- About ----------------------------------------------------------------
  'page.whatsNew.title': 'Wat is er nieuw in Plak',
  'page.whatsNew.empty': 'Er zijn nog geen releases.',
  'page.about.title': 'Over Plak',
  'page.about.intro':
    'Plak is er voor die ene pagina die je wilt delen. Een rapport, een analyse, een overzicht: je zet het online, kiest wie het mag zien en deelt de link. Geen bijlage die door mailboxen zwerft, maar een adres dat je kunt bijwerken en weer kunt intrekken.',
  'page.about.features.heading': 'Wat je ermee kunt',
  'page.about.features.share.term': 'Een pagina delen.',
  'page.about.features.share.body':
    'Zet je HTML-bestand op Plak en je hebt een adres om te delen.',
  'page.about.features.update.term': 'Bijwerken zonder nieuw adres.',
  'page.about.features.update.body':
    'Een nieuwe versie komt over de vorige heen; iedereen met de link ziet vanzelf de laatste. Oudere versies blijven bewaard.',
  'page.about.features.audience.term': 'Zelf bepalen wie meekijkt.',
  'page.about.features.audience.body':
    'Van openbaar tot alleen genodigden, en alles ertussenin. Een geheime link kun je in één keer delen, of de link en de code los van elkaar sturen.',
  'page.about.features.revoke.term': 'Intrekken.',
  'page.about.features.revoke.body':
    'Een link die te ver is rondgestuurd, trek je in. Daarna doet hij niets meer.',
  'page.about.features.site.term': 'Ook een hele site.',
  'page.about.features.site.body':
    'Werk je in een repository, dan publiceer je vanuit GitHub of code.overheid.nl, met een preview per pull request.',
  'page.about.features.cli.term': 'Vanaf je eigen computer.',
  'page.about.features.cli.body':
    'Met de Plak-CLI publiceer je vanaf je laptop, nadat je die eenmalig aan je account hebt gekoppeld.',
  'page.about.name.heading': 'Wist je dat',
  'page.about.name.body':
    'De naam komt van plakkaat: een aankondiging die je in de buurt ophangt zodat anderen hem kunnen zien. Een plakkaat kan in de openbare ruimte hangen, zichtbaar voor iedereen, of in een afgesloten ruimte waar alleen een kleine groep komt. Dat is precies wat Plak doet.',

  // -- Privacy --------------------------------------------------------------
  'page.privacy.title': 'Privacy',
  'page.privacy.intro':
    'Plak verwerkt persoonsgegevens om toegang tot gepubliceerde sites te regelen: een e-mailadres bij inloggen via je organisatie, en eventueel een e-mailadres van een genodigde bij toegang op uitnodiging.',
  'page.privacy.data.heading': 'Welke gegevens',
  'page.privacy.data.account': 'Accountgegevens: naam, e-mailadres en organisatie, via inloggen.',
  'page.privacy.data.audit':
    'Auditgegevens: wie wat wanneer publiceerde of wijzigde, en wie wanneer inlogde. Hernoem je een groep of site, dan staat ook de vorige naam of titel in die regel.',
  'page.privacy.data.visits':
    "Bezoekgegevens: welke pagina's van een afgeschermde site je bekeek. Van openbare sites wordt niets bijgehouden.",
  'page.privacy.retention.heading': 'Bewaartermijn',
  'page.privacy.retention.body':
    'Bezoekgegevens en inlogmomenten worden na 90 dagen verwijderd. Overige auditgegevens, zoals geweigerde toegang en beheerhandelingen, blijven drie jaar bewaard, omdat ze deel kunnen worden van het onderzoek naar een beveiligingsincident. Alleen een platformbeheerder kan ze inzien, en elke inzage wordt zelf vastgelegd, met een verplichte reden die voor elke platformbeheerder leesbaar is. Je naam en e-mailadres staan er niet leesbaar in. Previews en hun toegangsgegevens vervallen automatisch na dertig dagen zonder nieuwe deploy.',
  'page.privacy.retention.ip':
    'Bij elke auditregel hoort ook het IP-adres van waaraf iets gebeurde: een afgekapt netwerk (niet het volledige adres) staat gewoon in het log, even lang bewaard als de regel zelf. Het volledige adres staat er versleuteld naast, even lang bewaard, en alleen een platformbeheerder kan het opvragen, met dezelfde verplichte, vastgelegde reden.',
  'page.privacy.retention.session':
    'Bekijk je afgeschermde content na inloggen met je organisatie, dan bewaart Plak je SSO-id en e-mailadres tot negentig dagen na je laatste keer inloggen. Dat is nodig om een regel in het auditlog bij een persoon te kunnen brengen, bijvoorbeeld bij het onderzoek naar een lek.',
  'page.privacy.visibility.heading': 'Wie kan wat zien',
  'page.privacy.visibility.body':
    'Het auditlog is alleen in te zien door een platformbeheerder. In de regels staan jouw naam en e-mailadres niet, maar een pseudoniem: een versleutelde weergave van je SSO-id. Een uitzondering is de vorige naam van een groep of de vorige titel van een site: die tekst staat er leesbaar in. Een platformbeheerder kan opzoeken welk pseudoniem bij een persoon hoort, en omgekeerd wie achter een pseudoniem zit. Dat kan alleen met een opgegeven reden, die zelf wordt vastgelegd, en er geldt een maximum aantal opzoekingen per dag. De beheerder van een site ziet dit alles niet.',
  'page.privacy.rights.heading': 'Je rechten',
  // Split around the mailto link in the sentence: the address itself is the
  // link text, so the two halves sit on either side of the anchor.
  'page.privacy.rights.bodyBeforeEmail':
    'Je mag opvragen welke gegevens Plak over je verwerkt, ze laten corrigeren, en bezwaar maken tegen de verwerking. Verwijderen kan niet altijd: het auditlog ligt vast zolang de bewaartermijn loopt, want het dient om misbruik te kunnen onderzoeken. Stel je vraag via',
  'page.privacy.rights.bodyAfterEmail':
    '; je krijgt binnen tien werkdagen een reactie. Ben je het niet eens met wat wij doen, dan kun je een klacht indienen bij de Autoriteit Persoonsgegevens.',

  // -- Accessibility --------------------------------------------------------
  'page.accessibility.title': 'Toegankelijkheid',
  'page.accessibility.intro':
    'Wij vinden het belangrijk dat Plak voor iedereen goed te gebruiken is. Plak streeft naar WCAG 2.1, niveau AA, zoals het Besluit digitale toegankelijkheid overheid (EN 301 549) vraagt. De beheeromgeving is gebouwd met het NLDD Design System, dat toegankelijkheid in de componenten inbouwt: ARIA, focusvolgorde en toetsenbordbediening.',
  'page.accessibility.works.heading': 'Wat al werkt',
  'page.accessibility.works.keyboard': 'Alles is met het toetsenbord te bedienen.',
  'page.accessibility.works.skipLink': 'Elke pagina begint met een link naar de hoofdinhoud.',
  'page.accessibility.works.colorScheme':
    'De interface volgt je voorkeur voor een licht of donker scherm.',
  'page.accessibility.works.checks':
    'Automatische controles op toegankelijkheid draaien mee bij elke wijziging.',
  'page.accessibility.todo.heading': 'Wat nog niet af is',
  'page.accessibility.todo.body':
    "Plak is een bètaversie en is nog niet onderzocht. Er staat dan ook nog geen verklaring in het landelijke register op toegankelijkheidsverklaring.nl; die volgt zodra Plak in productie gaat. Pagina's die jij of je collega's publiceren, zijn van de makers zelf: Plak controleert de toegankelijkheid daarvan niet.",
  'page.accessibility.report.heading': 'Een probleem melden',
  // Same split as the privacy page: the e-mail address is the link text.
  'page.accessibility.report.bodyBeforeEmail':
    'Kom je iets tegen dat niet werkt, of heb je een idee voor verbetering? Neem contact op via',
  'page.accessibility.report.bodyAfterEmail':
    '. Je krijgt binnen tien werkdagen een reactie. Wij werken doorlopend aan het verbeteren van de toegankelijkheid.',

  // -- Linking a CLI session (plak login) -----------------------------------
  'page.cliPair.loading': 'Bezig met laden',
  'page.cliPair.error.unknownCode':
    'Deze code is onbekend, verlopen of al gebruikt. Start plak login opnieuw.',
  'page.cliPair.error.tooManyAttempts': 'Te veel pogingen. Probeer het zo nog eens.',
  'page.cliPair.unknownClient': 'Onbekend programma',
  'page.cliPair.login.intro': 'Een CLI-sessie koppelen kan alleen kort nadat je zelf bent ingelogd.',
  'page.cliPair.login.afterwards':
    'Log in en vul daarna de code over die plak login in je terminal toont.',
  'page.cliPair.login.action': 'Inloggen',
  'page.cliPair.codeEntry.intro':
    'Vul de code over die je terminal toont om plak login te koppelen aan je account.',
  'page.cliPair.codeEntry.install.before': 'Nog geen Plak-CLI? ',
  'page.cliPair.codeEntry.install.link': 'Zo installeer en gebruik je hem.',
  'page.cliPair.codeEntry.label': 'Code uit je terminal',
  'page.cliPair.codeEntry.placeholder': 'ABCD-EFGH',
  'page.cliPair.codeEntry.required': 'De code uit je terminal, in de vorm ABCD-EFGH',
  'page.cliPair.codeEntry.submit': 'Zoek code op',
  'page.cliPair.confirm.question': 'Staat in je terminal dezelfde code?',
  'page.cliPair.confirm.account': 'Je koppelt dit programma aan {account}',
  'page.cliPair.confirm.accountHint':
    'Het programma kan daarna publiceren met al jouw rollen, tot je het ontkoppelt.',
  'page.cliPair.confirm.client': 'Programma, zoals het zichzelf noemt: "{name}"',
  'page.cliPair.confirm.requested': 'Aangevraagd: {time}',
  'page.cliPair.confirm.network': 'Vanaf netwerk {network}',
  'page.cliPair.confirm.otherNetwork.title':
    'Deze aanvraag komt van een ander netwerk dan waar jij nu bent',
  'page.cliPair.confirm.otherNetwork.detail':
    'Draait plak login op een andere machine of via een VPN, dan kan dat kloppen. Twijfel je, klik dan op Weigeren.',
  'page.cliPair.confirm.warning.title':
    'Alleen koppelen als je zelf zojuist plak login hebt gestart',
  'page.cliPair.confirm.warning.detail':
    'Heeft iemand je deze link of code gestuurd? Klik dan op Weigeren.',
  'page.cliPair.confirm.approve': 'Koppelen',
  'page.cliPair.confirm.deny': 'Weigeren',
  'page.cliPair.approved.title': 'Gekoppeld',
  'page.cliPair.approved.detail': 'Je kunt terug naar je terminal.',
  'page.cliPair.denied.title': 'Geweigerd',
  'page.cliPair.denied.detail': 'Het programma krijgt geen toegang.',

  // -- Done: the result screen after publishing -----------------------------
  'page.done.loading': 'Resultaat wordt geladen…',
  'page.done.heading.online': 'Je site staat online',
  'page.done.heading.offline': 'Je site staat nog niet online',
  'page.done.error.unknownSite.title': 'Onbekende site',
  'page.done.error.unknownSite.detail': 'Geen site "{site}" in groep "{group}".',
  'page.done.address.site': 'Adres van je site',
  'page.done.copy.button': 'Kopieer adres',
  'page.done.copy.ok': 'Adres gekopieerd.',
  'page.done.copy.failed':
    'Kopiëren lukte niet. Selecteer het adres hierboven en kopieer het zelf.',
  'page.done.open': 'Open site',
  'page.done.published.title': 'Gepubliceerd: {title}',
  'page.done.published.detail': 'Laatste publicatie: {time}',
  'page.done.noVersion.title': 'Er staat nog geen versie online',
  'page.done.noVersion.detail':
    'De site bestaat, maar er is nog niets gepubliceerd. Publiceer een versie op de sitepagina.',
  'page.done.noVersion.action': 'Naar de site',
  'page.done.visibility.heading': 'Wie kan dit zien',
  'page.done.visibility.change': 'Wijzig wie dit kan zien',
  'page.done.key.created.title': 'Geheime link aangemaakt',
  'page.done.key.created.detail':
    'Deze link zie je maar één keer. Kopieer hem nu: iedereen met deze link kan de site zien, ook zonder in te loggen. Je kunt de link intrekken op het tabblad Toegang.',
  'page.done.key.failed.title': 'Geheime link niet aangemaakt',
  'page.done.key.failed.fallback': 'Aanmaken is niet gelukt.',
  'page.done.key.failed.action': 'Naar Toegang',
  'page.done.next.heading': 'Verder',
  'page.done.next.site': 'Nieuwe versie publiceren of deze site beheren',
  'page.done.next.overview': 'Terug naar het overzicht',
} as const;
