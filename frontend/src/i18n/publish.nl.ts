/**
 * Dutch catalogue section: publishing: the publish sheet, the upload zone, the deploy tab and the access tab.
 *
 * A section per area rather than one file of many hundreds of keys: this is
 * the unit a reviewer can hold in their head, and the unit a screen change
 * touches. nl.ts folds the sections into one catalogue; the key union is
 * inferred from that, so nothing here is optional.
 */
export const publishNl = {
  // -- Publish sheet: the flow from file to published site --------------------
  'publish.sheet.heading': 'Zet een site online',
  'publish.sheet.close': 'Sluiten',
  'publish.sheet.drop': 'Laat los om te uploaden',
  'publish.sheet.halfway.title': 'De site bestaat al',
  'publish.sheet.halfway.detail': "Alleen het bestand moet er nog op. Kies opnieuw 'Zet online'.",
  'publish.sheet.submit': 'Zet online',

  // -- Publish sheet: the fields ----------------------------------------------
  'publish.sheet.file.label': 'Bestand',
  'publish.sheet.file.hint': 'Een .zip of .tar.gz van je map, of een los HTML-bestand.',
  'publish.sheet.file.clear': 'Verwijder het gekozen bestand',
  'publish.sheet.file.required': 'Een archief (.zip, .tar.gz) of een los HTML-bestand',
  'publish.sheet.title.label': 'Titel',
  'publish.sheet.title.required': 'Een titel',
  'publish.sheet.group.label': 'Groep',
  'publish.sheet.groupName.label': 'Naam van je groep',
  'publish.sheet.groupName.hintNone':
    'Je hebt er nog geen; deze maak je nu aan en je bent er meteen lid van.',
  'publish.sheet.groupName.hintNoRole':
    'Je hebt in geen van je groepen de rol editor of beheerder; maak een nieuwe groep aan om hier te publiceren.',
  'publish.sheet.groupName.required': 'Een naam voor je groep',
  'publish.sheet.address.label': 'Adres',
  'publish.sheet.address.hint': 'Het laatste stuk van de URL.',
  'publish.sheet.address.required': 'Een adres',
  'publish.sheet.address.form':
    'Alleen kleine letters, cijfers en koppeltekens, geen koppelteken aan begin of eind',
  'publish.sheet.address.known': 'Je site komt op {address}',
  'publish.sheet.address.empty': 'Het adres van je site volgt uit de titel.',

  // -- Publish sheet: who may look --------------------------------------------
  'publish.sheet.visibility.label': 'Wie kan de site bekijken?',
  'publish.sheet.extras.heading': 'Uitzonderingen',
  'publish.sheet.extras.hint': 'Deze laten er mensen bij, en nemen nooit iemand weg.',
  'publish.sheet.extras.keysAfter': 'De eerste geheime link krijg je meteen na het publiceren.',
  'publish.sheet.extras.inviteesAfter':
    'Genodigden zet je daarna op de lijst, op het tabblad Toegang van de site.',

  // -- Upload zone ------------------------------------------------------------
  'publish.drop.oneFile': 'Sleep één bestand: een .zip, .tar.gz, .tgz of los HTML-bestand.',
  'publish.drop.noFolder': 'Sleep geen map. {hint}',
  'publish.upload.field.label': 'Bestand of map',
  'publish.upload.field.hint': 'Sleep het hierheen, of kies het.',
  'publish.upload.field.required': 'Een archief, een los HTML-bestand of een map',
  'publish.upload.field.help':
    'Er kan een archief in (.zip, .tar.gz of .tgz), een los HTML-bestand, of een hele map die je hierheen sleept of kiest met de knop "Kies een map". Een los HTML-bestand wordt de startpagina van je site; een map pakt Plak in je browser in, zonder de metadata van je besturingssysteem.',
  'publish.upload.folder': 'Kies een map',
  'publish.upload.progress.reading': 'Map uitlezen',
  'publish.upload.progress.packing': 'Map inpakken',
  'publish.upload.ready.title': 'Klaar om te publiceren',
  'publish.upload.ready.file': '"{name}" staat klaar, {size}.',
  'publish.upload.ready.bundleOne': 'Map "{name}" ingepakt: {count} bestand, {size}.',
  'publish.upload.ready.bundle': 'Map "{name}" ingepakt: {count} bestanden, {size}.',
  'publish.upload.error.title': 'Dit kan zo niet gepubliceerd worden',
  'publish.upload.error.cannotPack':
    'Deze browser kan een map niet zelf inpakken. Maak er een .zip van en kies die met "Bestand kiezen".',
  'publish.upload.error.packFailed':
    'Inpakken is niet gelukt. Maak zelf een .zip van je map en kies die met "Bestand kiezen".',
  'publish.upload.submit': 'Publiceer versie',

  // -- Packing: what is refused before a byte goes over the wire ---------------
  'publish.packing.secret':
    '"{path}" hoort niet op een website, dus Plak publiceert deze map niet. Kies de map met de gebouwde site, meestal "dist" of "build", in plaats van de hele projectmap.',
  'publish.packing.nothing': 'Er is niets neergezet dat gepubliceerd kan worden.',
  'publish.packing.notASite':
    'Van "{name}" alleen kan Plak geen site maken. Sleep de hele map, een archief (.zip, .tar.gz of .tgz), of een los HTML-bestand.',
  'publish.packing.folderEmpty': 'In deze map staat niets dat gepubliceerd kan worden.',
  'publish.packing.bundleEmpty': 'In "{name}" staat niets dat gepubliceerd kan worden.',
  'publish.packing.tooManyFiles':
    '"{name}" heeft meer dan {count} bestanden; dat is meer dan een publicatie mag bevatten.',
  'publish.packing.tooDeep':
    '"{path}" zit dieper dan {depth} mappen; maak de mappenstructuur ondieper.',
  'publish.packing.pathTooLong':
    'Het pad "{path}" is te lang voor een archief; kort de map- of bestandsnamen in.',
  'publish.packing.fileTooLarge':
    '"{path}" is {size}; een los bestand mag hoogstens {max} zijn.',
  'publish.packing.totalTooLarge':
    '"{name}" is samen {size}; een publicatie mag hoogstens {max} zijn.',
  'publish.packing.indexCase':
    'De startpagina heet "{found}"; hij moet "{index}" heten, met kleine letters.',
  'publish.packing.indexInFolder':
    'Er staat geen {index} in de hoofdmap van "{name}", wel op "{path}". Sleep de map "{folder}" zelf.',
  'publish.packing.indexMissing':
    'Er staat geen {index} in "{name}". Zonder startpagina kan niemand de site openen.',

  // -- Deploy tab: the linked repository ---------------------------------------
  'publish.deploy.loading': 'Deploy-instellingen laden',
  'publish.deploy.heading': 'Publiceren vanuit GitHub of Forgejo',
  'publish.deploy.intro':
    'Koppel de repository waarin je site staat. Een push naar de live-branch publiceert daarna automatisch een nieuwe versie.',
  'publish.deploy.repo.liveBranch': 'Live-branch: {branch}',
  'publish.deploy.repo.anyBranch': 'elke branch',
  'publish.deploy.repo.linkedBy': 'Gekoppeld door {who}',
  'publish.deploy.repo.unknownWho': 'onbekend',
  'publish.deploy.repo.unconfirmed': 'Nog niet bevestigd',
  'publish.deploy.repo.unconfirmedDetail':
    'Plak kon deze repository niet opzoeken, dus de naam en de ids staan zoals ze zijn ingevuld. De eerste publicatie vanuit de repository bevestigt de ids en zet de naam goed.',
  'publish.deploy.repo.change': 'Wijzigen',
  'publish.deploy.repo.unlink': 'Ontkoppelen',
  'publish.deploy.repo.empty': 'Nog geen repository gekoppeld',
  'publish.deploy.repo.emptyAdmin':
    'Koppel een repository; een push naar de live-branch publiceert daarna automatisch.',
  'publish.deploy.repo.emptyReader':
    'Vraag een beheerder van deze site om een repository te koppelen; daarna publiceert een push naar de live-branch automatisch.',
  'publish.deploy.repo.link': 'Repository koppelen',
  'publish.deploy.repo.linked': 'Repository gekoppeld',
  'publish.deploy.repo.linkedDetail': '{repo} mag nu publiceren naar deze site.',
  'publish.deploy.repo.linkFailed': 'Koppelen is niet gelukt.',
  'publish.deploy.repo.unlinkFailed': 'Repository niet ontkoppeld',
  'publish.deploy.repo.unlinkFailedDetail': 'Ontkoppelen is niet gelukt.',

  // -- Deploy tab: the link form ------------------------------------------------
  'publish.deploy.form.provider': 'Provider',
  'publish.deploy.form.host': 'Forgejo-host',
  'publish.deploy.form.repo': 'Repository',
  'publish.deploy.form.repoPlaceholder': 'https://github.com/minbzk/website of minbzk/website',
  'publish.deploy.form.repoHelp':
    'Plak een URL van GitHub of Forgejo ("https://github.com/minbzk/website", "git@code.overheid.nl:minbzk/website.git"), of typ "eigenaar/repo".',
  'publish.deploy.form.repoRequired':
    'Een repository-URL of "eigenaar/repo", bijvoorbeeld "minbzk/website"',
  'publish.deploy.form.branch': 'Live-branch',
  'publish.deploy.form.branchPlaceholder': 'bijv. main',
  'publish.deploy.form.branchHelp':
    'Leeg: elke branch van deze repository mag live publiceren, zolang de run een push, een handmatige run (workflow_dispatch) of een schedule is, nooit een pull request. Vul een branch in (bijvoorbeeld "main") om alleen die branch live te laten publiceren; previews blijven dan mogelijk vanaf elke branch.',
  'publish.deploy.form.cancel': 'Annuleren',
  'publish.deploy.form.submit': 'Koppelen',
  'publish.deploy.form.recognized': 'Herkend: {label}, {repo}',
  'publish.deploy.form.forgejoLabel': 'Forgejo ({host})',
  'publish.deploy.form.urlNoOwner': 'De URL bevat geen eigenaar en repository.',
  'publish.deploy.form.unknownHost': 'Onbekende host "{host}". Toegestaan: {allowed}.',
  'publish.deploy.form.invalidUrl': 'Dit is geen geldige URL.',
  'publish.deploy.form.invalidReference':
    'Eigenaar en repository gescheiden door een schuine streep (bijvoorbeeld "minbzk/website"), of een repository-URL.',
  'publish.deploy.form.idsIntro':
    'Plak kan deze repository niet zelf opzoeken, bijvoorbeeld omdat ze privé is. Vul dan de twee ids hieronder zelf in. Dit geeft eerst het repository-id, dan het eigenaar-id:',
  'publish.deploy.form.cliHint':
    'Liever vanuit de terminal? Draai dit in een checkout van de repository, na plak login. Bij een privé GitHub-repository haalt de CLI de ids zelf op via gh.',
  'publish.deploy.form.idsIntroGithub':
    'Plak kan deze repository niet zelf opzoeken, bijvoorbeeld omdat ze privé is. Het makkelijkst koppel je haar vanuit een checkout, na plak login: de CLI haalt de ids dan zelf op met je eigen gh-login.',
  'publish.deploy.form.idsManual':
    'Of vul de ids hieronder zelf in. Dit geeft eerst het repository-id, dan het eigenaar-id:',
  'publish.deploy.form.idsWrong':
    'Een verkeerd id koppelt niets anders, het weigert alleen elke deploy.',
  'publish.deploy.form.repositoryId': 'Repository-id',
  'publish.deploy.form.ownerId': 'Eigenaar-id',
  'publish.deploy.form.idsInvalid': 'Vul het repository-id en het eigenaar-id allebei in, alleen met cijfers.',

  // -- Deploy tab: the ready-made workflow ---------------------------------------
  'publish.deploy.workflow.hint':
    'Kant-en-klare workflow voor {provider} Actions. Vervang {code} door de vastgepinde commit van de action.',
  'publish.deploy.workflow.path':
    'Zet dit bestand op {path} in de repository. Na een push naar de live-branch staat de nieuwe versie in {link}.',

  // -- Deploy tab: why this is safe ------------------------------------------------
  'publish.deploy.safety.summary': 'Waarom is dit veilig?',
  'publish.deploy.safety.noSecret':
    'Er komt geen geheim in de repository: de forge (GitHub of Forgejo) ondertekent zelf een kort geldig token voor precies deze workflow-run, en Plak controleert die handtekening.',
  'publish.deploy.safety.scoped':
    'Dat token bewijst alleen welke repository de run start; Plak accepteert het hier omdat jij precies die repository aan deze site gekoppeld hebt.',
  'publish.deploy.safety.liveRestricted':
    'Live publiceren kan alleen vanaf de ingestelde live-branch, en alleen via een push, een handmatige run of een schedule, nooit vanuit een pull request.',
  'publish.deploy.safety.previews':
    'Previews mogen vanaf elke branch, ook vanuit een pull request, en raken de live site nooit.',
  'publish.deploy.safety.audited': 'Elke publicatie staat in het auditlog.',
  'publish.deploy.safety.unlink':
    'Ontkoppel je de repository, dan stopt publiceren vanuit CI meteen.',

  // -- Deploy tab: publishing from your own computer -------------------------------
  'publish.deploy.cli.heading': 'Vanaf je eigen computer',
  'publish.deploy.cli.install':
    'Met de Plak-CLI publiceer je zonder CI, met je eigen toegang. Heb je hem nog niet: {repo} bevat hem in {folder}. Installeren en later bijwerken:',
  'publish.deploy.cli.run':
    'Werk je uit een checkout, dan draait {run} hem zonder installatie.',
  'publish.deploy.cli.login':
    '{login} opent je browser op {link} om de CLI-sessie te koppelen; daarna bewaart de CLI die sessie voor volgende {publish}-aanroepen.',

  // -- Deploy tab: unlinking ---------------------------------------------------------
  'publish.deploy.unlink.title': 'Repository ontkoppelen?',
  'publish.deploy.unlink.text':
    'Deze site kan hierna niet meer zonder geheim vanuit CI publiceren, tot een nieuwe repository gekoppeld is.',
  'publish.deploy.unlink.keep': 'Behoud koppeling',
  'publish.deploy.unlink.confirm': 'Ontkoppel repository',

  // -- Access tab: the base ------------------------------------------------------
  'publish.access.loading': 'Toegangsinstellingen laden',
  'publish.access.base.heading': 'Wie kan deze site bekijken?',
  'publish.access.saved': 'Toegang opgeslagen',
  'publish.access.saveFailed': 'Toegang niet opgeslagen',
  'publish.access.saveFailedDetail': 'Opslaan is niet gelukt.',
  'publish.access.column.actions': 'Acties',

  // -- Access tab: the two exceptions ---------------------------------------------
  'publish.access.extras.heading': 'Uitzonderingen',
  'publish.access.extras.intro':
    'Deze twee staan los van de keuze hierboven en los van elkaar. Ze laten er mensen bij, en nemen nooit iemand weg.',
  'publish.access.extras.moot': 'De uitzonderingen voegen nu niets toe',
  'publish.access.extras.mootDetail':
    'De site is publiek, dus iedereen mag toch al kijken. Zet de basis hierboven strenger om deze uitzonderingen te laten gelden; wat je hier instelt blijft staan.',

  // -- Access tab: secret links ------------------------------------------------------
  'publish.access.keys.on': 'Geheime links staan aan',
  'publish.access.keys.off': 'Geheime links staan uit',
  'publish.access.keys.intro':
    'De volledige link zie je maar één keer, direct na het aanmaken. Intrekken werkt meteen, ook voor wie de pagina al open heeft staan.',
  'publish.access.keys.created': 'Geheime link aangemaakt',
  'publish.access.keys.createdDetail':
    'Deze link zie je maar één keer. Kopieer hem nu: iedereen met deze link kan de site zien, ook zonder in te loggen.',
  'publish.access.keys.open': 'Open site',
  'publish.access.keys.column.label': 'Label',
  'publish.access.keys.column.created': 'Aangemaakt',
  'publish.access.keys.column.expires': 'Vervalt',
  'publish.access.keys.column.status': 'Status',
  'publish.access.keys.empty': 'Nog geen geheime links',
  'publish.access.keys.emptyDetail':
    'Maak er hieronder een; de link is daarna eenmalig te kopiëren.',
  'publish.access.keys.never': 'nooit',
  'publish.access.keys.active': 'Actief',
  'publish.access.keys.revoked': 'Ingetrokken',
  'publish.access.keys.revoke': 'Intrekken',
  'publish.access.keys.revokeHint': 'De link werkt daarna niet meer',
  'publish.access.keys.revokeFailed': 'Geheime link {label} niet ingetrokken',
  'publish.access.keys.revokeFailedDetail': 'Intrekken is niet gelukt.',
  'publish.access.keys.createFailed': 'Aanmaken is niet gelukt.',
  'publish.access.keys.form.heading': 'Geheime link maken',
  'publish.access.keys.form.hint':
    'Het label is voor jezelf: het zegt met wie je de link deelde, zodat je weet wat je intrekt.',
  'publish.access.keys.form.label': 'Label',
  'publish.access.keys.form.labelHelp':
    'Handig als je meerdere links uitdeelt; laat je het leeg, dan krijgt de link een naam met de datum.',
  'publish.access.keys.form.expiry': 'Vervalt na',
  'publish.access.keys.form.days': '{days} dagen',
  'publish.access.keys.form.submit': 'Maak geheime link',

  // -- Access tab: invitees -------------------------------------------------------
  'publish.access.invitees.on': 'Genodigden staan aan',
  'publish.access.invitees.off': 'Genodigden staan uit',
  'publish.access.invitees.intro':
    'Genodigden loggen in met SSO Rijk; hun geverifieerde e-mailadres moet op deze lijst staan. Plak stuurt zelf geen uitnodiging: de link deel je zelf.',
  'publish.access.invitees.column.email': 'E-mailadres',
  'publish.access.invitees.column.added': 'Toegevoegd',
  'publish.access.invitees.empty': 'Nog geen genodigden',
  'publish.access.invitees.emptyDetail': 'Voeg hieronder een e-mailadres toe.',
  'publish.access.invitees.remove': 'Verwijderen',
  'publish.access.invitees.removeHint': 'Kan daarna niet meer bij deze site',
  'publish.access.invitees.removeFailed': '{invitee} niet verwijderd',
  'publish.access.invitees.removeFailedDetail': 'Verwijderen is niet gelukt.',
  'publish.access.invitees.addFailed': 'Toevoegen is niet gelukt.',
  'publish.access.invitees.form.heading': 'Genodigde toevoegen',
  'publish.access.invitees.form.hint':
    'Het adres waarmee diegene bij SSO Rijk inlogt. Hoofdletters maken niet uit.',
  'publish.access.invitees.form.email': 'E-mailadres',
  'publish.access.invitees.form.required': 'Een e-mailadres',
  'publish.access.invitees.form.submit': 'Voeg genodigde toe',

  // -- Access tab: external sources --------------------------------------------------
  'publish.access.external.heading': 'Externe bronnen',
  'publish.access.external.intro':
    'Staat aan, tenzij je het uitzet: de pagina mag scripts en stijlen laden van cdnjs, jsDelivr en unpkg, scripts van de Tailwind-CDN, en lettertypen van Google Fonts. Die partijen zien dan het IP-adres van iedereen die de pagina bekijkt, en hun code draait in de pagina. Uitzetten is de veiligere keuze voor een vertrouwelijke pagina. In beide standen geblokkeerd: gegevens ophalen bij of sturen naar andere hosts, afbeeldingen van elders, een iframe om de pagina heen, en een formulier dat ergens anders post.',
  'publish.access.external.label': 'Externe bronnen toestaan',
  'publish.access.external.saved': 'Externe bronnen opgeslagen',
  'publish.access.external.on': 'Externe bronnen staan aan.',
  'publish.access.external.off': 'Externe bronnen staan uit.',
  'publish.access.external.failed': 'Externe bronnen niet opgeslagen',

  // -- Access tab: shielding from other sites ----------------------------------------
  'publish.access.sandbox.heading': 'Afscherming van andere sites',
  'publish.access.sandbox.intro':
    'Staat aan, tenzij je het uitzet. Alle sites staan op hetzelfde webadres. Zolang de afscherming aanstaat, kan de code op jouw pagina\'s niets zien van de andere sites op dat adres, en kan diezelfde code ook niets bewaren in de browser van de bezoeker: een onthouden voorkeur, een half ingevuld formulier, alles wat via localStorage, sessionStorage of een cookie gaat, werkt dan niet. Eigen stijlen, gewone scripts en afbeeldingen laden gewoon. Weblettertypen laden niet, en een script dat zelf een bestand van je site ophaalt werkt ook niet. Hetzelfde geldt voor scripts die als module laden (<script type="module">): Astro, Vite en de meeste andere bouwtools maken hun scripts zo, dus een site die daarmee gebouwd is verliest met de afscherming aan al zijn scripts. Zet je de afscherming uit, dan kan jouw site weer bewaren in de browser, maar deelt hij het webadres ook weer met alle andere sites: de code op jouw pagina\'s kan dan bij alles wat de bezoeker daar mag zien. Doe dat alleen voor een site waarvan je de inhoud vertrouwt.',
  'publish.access.sandbox.label': 'Afschermen van andere sites',
  'publish.access.sandbox.saved': 'Afscherming opgeslagen',
  'publish.access.sandbox.on': 'De afscherming staat aan.',
  'publish.access.sandbox.off': 'De afscherming staat uit.',
  'publish.access.sandbox.failed': 'Afscherming niet opgeslagen',
} as const;
