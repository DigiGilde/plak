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
  'page.privacy.title':
    'Privacy',
  'page.privacy.intro':
    'Deze verklaring beschrijft hoe Plak omgaat met persoonsgegevens en welke rechten je hebt. We werken volgens de Algemene verordening gegevensbescherming (AVG). Plak verwerkt persoonsgegevens om toegang tot gepubliceerde sites te regelen: een e-mailadres bij inloggen via je organisatie, en eventueel een e-mailadres van een genodigde bij toegang op uitnodiging.',
  'page.privacy.draft':
    'Dit is een concept. Deze verklaring is nog niet juridisch getoetst, ook niet door de Functionaris Gegevensbescherming.',
  'page.privacy.controller.heading':
    'Wie is verantwoordelijk',
  'page.privacy.controller.body':
    'Het Digi Gilde, dat Plak bouwt en beheert, is onderdeel van de Rijksorganisatie voor Ontwikkeling, Digitalisering en Innovatie (ODI), die valt onder het Ministerie van Binnenlandse Zaken en Koninkrijksrelaties (BZK). De minister en staatssecretaris van BZK zijn verwerkingsverantwoordelijke voor de persoonsgegevens die via Plak worden verwerkt.',
  'page.privacy.data.heading':
    'Welke gegevens',
  'page.privacy.data.account':
    'Accountgegevens: naam en e-mailadres, via inloggen, en je rol in een groep of site.',
  'page.privacy.data.invitees':
    'Genodigden: het e-mailadres of SSO-id van wie een beheerder op de lijst van een site zet, ook als die persoon nooit inlogt.',
  'page.privacy.data.audit':
    'Auditgegevens: wie wat wanneer publiceerde of wijzigde, en wie wanneer inlogde.',
  'page.privacy.data.visits':
    "Bezoekgegevens: welke pagina's van een afgeschermde site je bekeek. Van openbare sites wordt niets bijgehouden.",
  'page.privacy.data.content':
    'Gepubliceerde inhoud: kan persoonsgegevens bevatten, maar die zijn van de maker van de site. Plak leest, classificeert of filtert die inhoud niet; de publicerende organisatie is er zelf verantwoordelijk voor.',
  'page.privacy.purpose.heading':
    'Doel en grondslag',
  'page.privacy.purpose.body':
    'We verwerken deze gegevens om gepubliceerde sites alleen te tonen aan wie ze mag zien, en om het platform veilig te houden en misbruik te kunnen onderzoeken. De grondslag is artikel 6, lid 1, onder e van de AVG: de vervulling van een taak van algemeen belang.',
  'page.privacy.processors.heading':
    'Verwerkers en hosting',
  'page.privacy.processors.body':
    'Plak draait op ZAD, het hostingplatform van de Rijksoverheid, met de database en het bestandsvolume van dat platform. Inloggen loopt via de Keycloak van ZAD, die doorverwijst naar SSO Rijk. Plak gebruikt geen webstatistieken en deelt geen gegevens met andere partijen. De afspraken met het hostingplatform worden nog vastgelegd.',
  'page.privacy.retention.heading':
    'Bewaartermijn',
  'page.privacy.retention.body':
    'Bezoekgegevens en inlogmomenten worden na 90 dagen verwijderd. Overige auditgegevens, zoals geweigerde toegang en beheerhandelingen, blijven drie jaar bewaard, omdat ze deel kunnen worden van het onderzoek naar een beveiligingsincident. Alleen een platformbeheerder kan ze inzien, en elke inzage wordt zelf vastgelegd, met een verplichte reden die voor elke platformbeheerder leesbaar is. Je naam en e-mailadres staan er niet leesbaar in. Previews en hun toegangsgegevens vervallen automatisch na dertig dagen zonder nieuwe deploy.',
  'page.privacy.retention.ip':
    'Bij elke auditregel hoort ook het IP-adres van waaraf iets gebeurde: een afgekapt netwerk (niet het volledige adres) staat gewoon in het log, even lang bewaard als de regel zelf. Het volledige adres staat er versleuteld naast, even lang bewaard, en alleen een platformbeheerder kan het opvragen, met dezelfde verplichte, vastgelegde reden.',
  'page.privacy.retention.session':
    'Bekijk je afgeschermde content na inloggen met je organisatie, dan bewaart Plak je SSO-id en e-mailadres tot negentig dagen na je laatste keer inloggen. Dat is nodig om een regel in het auditlog bij een persoon te kunnen brengen, bijvoorbeeld bij het onderzoek naar een lek.',
  'page.privacy.visibility.heading':
    'Wie kan wat zien',
  'page.privacy.visibility.body':
    'Het auditlog is alleen in te zien door een platformbeheerder. In de regels staat geen naam en geen e-mailadres, maar een pseudoniem: een versleutelde weergave van je SSO-id. Een platformbeheerder kan opzoeken welk pseudoniem bij een persoon hoort, en omgekeerd wie achter een pseudoniem zit. Dat kan alleen met een opgegeven reden, die zelf wordt vastgelegd, en er geldt een maximum aantal opzoekingen per dag. De beheerder van een site ziet dit alles niet.',
  'page.privacy.rights.heading':
    'Je rechten',
  'page.privacy.rights.body':
    'Je hebt het recht om je gegevens in te zien, te laten corrigeren of verwijderen, en om bezwaar te maken tegen of beperking te vragen van de verwerking. Verwijderen kan niet altijd: het auditlog ligt vast zolang de bewaartermijn loopt, want het dient om misbruik te kunnen onderzoeken.',
  'page.privacy.rights.request.before':
    'Een verzoek stuur je naar het Ministerie van Binnenlandse Zaken en Koninkrijksrelaties via het ',
  'page.privacy.rights.request.link':
    'contactformulier van de Rijksoverheid',
  'page.privacy.rights.request.after':
    ', of per post: Ministerie van Binnenlandse Zaken en Koninkrijksrelaties, Postbus 20011, 2500 EA Den Haag.',
  'page.privacy.rights.register.before':
    'De verwerkingen van de Rijksoverheid staan in het ',
  'page.privacy.rights.register.link':
    'AVG-register van de Rijksoverheid',
  'page.privacy.rights.register.after':
    '.',
  'page.privacy.fg.heading':
    'Functionaris Gegevensbescherming',
  'page.privacy.fg.before':
    'Het Ministerie van Binnenlandse Zaken en Koninkrijksrelaties heeft een Functionaris Gegevensbescherming (FG) die toeziet op de naleving van de AVG. Je bereikt de FG via ',
  'page.privacy.fg.after':
    '.',
  'page.privacy.complaint.heading':
    'Een klacht indienen',
  'page.privacy.complaint.before':
    'Ben je het niet eens met hoe we met je gegevens omgaan? Je kunt een klacht indienen bij de ',
  'page.privacy.complaint.link':
    'Autoriteit Persoonsgegevens',
  'page.privacy.complaint.after':
    '.',
  'page.privacy.contact.heading':
    'Contact',
  'page.privacy.contact.before':
    'Heb je een vraag over deze verklaring of over Plak? Mail ons via ',
  'page.privacy.contact.after':
    '; je krijgt binnen tien werkdagen een reactie. Privacyverzoeken en klachten lopen via de adressen hierboven, zodat ze op de juiste plek binnen het ministerie terechtkomen.',

  // -- Accessibility --------------------------------------------------------
  'page.accessibility.title':
    'Toegankelijkheid',
  'page.accessibility.intro':
    "Deze verklaring beschrijft in hoeverre Plak voldoet aan de toegankelijkheidseisen voor overheidswebsites. De wettelijke norm is WCAG 2.1 niveau AA, via EN 301 549 en verplicht onder het Besluit digitale toegankelijkheid overheid. Het gaat om de beheeromgeving van Plak; pagina's die jij of je collega's publiceren zijn van de makers zelf, en Plak controleert de toegankelijkheid daarvan niet.",
  'page.accessibility.draft.before':
    'Dit is een concept. De status hieronder berust op eigen toetsen van het team. Een onderzoek door een onafhankelijke partij heeft niet plaatsgevonden. De definitieve verklaring moet nog worden opgesteld met de invulassistent op ',
  'page.accessibility.draft.link':
    'toegankelijkheidsverklaring.nl',
  'page.accessibility.draft.after':
    ' en worden gepubliceerd in het register daar. Tot dat rond is, is dit geen rechtsgeldige verklaring.',
  'page.accessibility.status.heading':
    'Nalevingsstatus',
  'page.accessibility.status.body':
    'Status C in het model van DigiToegankelijk: de toegankelijkheid van Plak is nog niet volledig onderzocht. Er is wel getoetst (zie hieronder), maar er ligt nog geen volledig, gedocumenteerd onderzoek tegen alle succescriteria van WCAG 2.1 AA.',
  'page.accessibility.tested.heading':
    'Hoe dit getoetst is',
  'page.accessibility.tested.components':
    'De beheeromgeving is opgebouwd uit de componenten van het NLDD Designsysteem. Die leveren het toetsenbordgedrag, de focusindicatie, de kleurcontrasten en de ARIA-kenmerken zelf mee. Elke pagina begint met een link naar de hoofdinhoud en de interface volgt je voorkeur voor een licht of donker scherm.',
  'page.accessibility.tested.automated':
    'Geautomatiseerd, bij elke wijziging: de toetsen van de beheeromgeving draaien axe-core op de groepspagina, de sitepagina en het profiel, in het Nederlands en het Engels, en een wijziging met een overtreding faalt. De taal van de pagina staat op het html-element, zodat een schermlezer de juiste uitspraak kiest.',
  'page.accessibility.tested.notDone':
    'Niet gedaan: een onderzoek door een onafhankelijke partij, een toets met schermlezers per pagina en een volledige doorloop van alle succescriteria van WCAG 2.1 AA.',
  'page.accessibility.limits.heading':
    'Bekende beperkingen',
  'page.accessibility.limits.contrast':
    'Kleurcontrast wordt niet in de geautomatiseerde toetsen gemeten, omdat de testomgeving geen weergave heeft. Het contrast berust op de kleuren van het designsysteem.',
  'page.accessibility.limits.pages':
    'Niet elke pagina heeft een eigen automatische toets; de dekking is nog niet volledig.',
  'page.accessibility.limits.shadow':
    'De componenten van het designsysteem tekenen onder meer landmarks en lijsten in hun schaduw-DOM. De ondersteuning daarvoor verschilt per schermlezer en is niet apart per schermlezer geverifieerd.',
  'page.accessibility.limits.published':
    'Gepubliceerde sites vallen buiten deze verklaring: de makers zijn zelf verantwoordelijk voor de toegankelijkheid van wat ze publiceren.',
  'page.accessibility.report.heading':
    'Een toegankelijkheidsprobleem melden',
  'page.accessibility.report.bodyBeforeEmail':
    'Kom je iets tegen dat niet werkt, of heb je een vraag over de toegankelijkheid? Laat het ons weten via',
  'page.accessibility.report.bodyAfterEmail':
    '. Je krijgt binnen tien werkdagen een reactie. Wij werken doorlopend aan het verbeteren van de toegankelijkheid.',
  'page.accessibility.enforcement.heading':
    'Handhaving',
  'page.accessibility.enforcement.before':
    'Ben je niet tevreden met hoe we je melding afhandelen, of krijg je geen reactie? Dan kun je een klacht indienen bij het ',
  'page.accessibility.enforcement.link':
    'College voor de Rechten van de Mens',
  'page.accessibility.enforcement.after':
    '.',
  'page.accessibility.prepared':
    'Deze verklaring is opgesteld op 7 oktober 2026.',

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
