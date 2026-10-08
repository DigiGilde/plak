"""The Dutch OpenAPI schema: every English text of the schema, in Dutch.

Keyed by the English text exactly as it stands in the generated schema, the
way gettext keys a translation by its msgid; api/openapi_i18n.py swaps the
texts through this table. Change an English sentence in a route, a model or
api/docs.py and its old entry here stops matching: test_openapi_i18n.py names
the new sentence that lacks a translation and the old entry nobody uses any
more, so the two languages cannot drift apart unnoticed.

The entries follow the schema: the API description and the tags first,
then the paths, then the components. A text that occurs in several places
has one entry, where it first occurs.
"""

from __future__ import annotations

from typing import Final

NL: Final[dict[str, str]] = {
    "Publish and share static sites, with previews per pull request.": (
        "Statische sites publiceren en delen, met previews per pull request."
    ),
    (
        "\n"
        "Plak publishes static sites per group and site, with a preview for every pull request.\n"
        "This API serves two kinds of client: the admin SPA on the admin host, and the CI that runs deploys.\n"
        "\n"
        "## Authentication\n"
        "\n"
        "There are two ways to identify yourself, and every endpoint accepts exactly one or two of them:\n"
        "\n"
        "* **Admin session** - a `Secure`/`HttpOnly`/`SameSite=Strict` cookie you get after SSO sign-in on the admin\n"
        "  host. Every session route also requires an **active** member: a new or deactivated member gets 403.\n"
        "  Mutations (POST, PUT, DELETE) additionally require the CSRF double-submit header `X-CSRF-Token`, with the\n"
        "  same value as the CSRF cookie.\n"
        "* **Bearer token** - `Authorization: Bearer <token>`, only on the two deploy endpoints, on\n"
        "  `DELETE /cli/session` and `GET /cli/whoami`, and, with a CLI token only, on creating a group or a site\n"
        "  and on linking a repository. The token is one of two kinds:\n"
        "  * a **CI ID token** (JWT) from GitHub Actions or Forgejo Actions, whose audience is exactly the admin\n"
        "    URL of Plak (`PLAK_BASE_URL`) followed by `/-/sites/` and the `id` of one site. It is valid only\n"
        "    for that site, and only while the repository of that workflow is linked to it\n"
        "    (`PUT /sites/{groupSlug}/{siteSlug}/repository`); there is no secret to keep. A link made before\n"
        "    the site id existed, of a repository linked to that one site, also takes a token whose audience is\n"
        "    the admin URL itself, until another repository is linked or a site admin requires the site id;\n"
        "  * a **CLI token** `plakcli_...` from `plak login` (see the CLI login endpoints). It acts as the member who\n"
        "    signed in, with exactly their roles.\n"
        "\n"
        "  A Bearer header on any other endpoint yields 401, even when the token is valid.\n"
        "\n"
        "All endpoints live on the admin origin and guard where a request comes from: an `Origin` or\n"
        "`Sec-Fetch-Site` that does not belong to the admin origin yields 403. CI and the CLI send neither header\n"
        "and so pass that guard unhindered.\n"
        "\n"
        "## Authorization\n"
        "\n"
        "Who may do what depends on the group: **group members** manage the sites of their own group according to\n"
        "their role, and any active member may create a group. A **platform administrator** also activates and\n"
        "deactivates members and reads the audit log. With a CI ID token what counts is the repository linked to the\n"
        "site, the site id the workflow names, and for a live deploy the live branch.\n"
        "\n"
        "## Errors\n"
        "\n"
        "Errors are `application/problem+json` following RFC 9457: `{type, title, status, detail}`, extended with\n"
        "the extension member `code` holding a stable, machine-readable error code (`NOT_GROUP_MEMBER`, "
        "`SLUG_EXISTS`,\n"
        "`BODY_TOO_LARGE`, ...). Each endpoint below lists the status codes it can return. Codes are the contract\n"
        "for clients; the `detail` text is meant for people and may change.\n"
        "\n"
        "## Language\n"
        "\n"
        "This document and the `title` and `detail` of every error come in English and in Dutch. Send\n"
        "`Accept-Language: nl` for Dutch; a client that asks for nothing gets English. For this document `?lang=nl`\n"
        "or `?lang=en` overrides the header.\n"
        "\n"
        "## Conventions\n"
        "\n"
        "Fields on the wire are lowerCamelCase. Timestamps are RFC 3339 in UTC with a `Z` suffix\n"
        "(`2026-09-12T09:30:00Z`). The full version of this API is in the `API-Version` header of every response;\n"
        "the major version is in the path (`/-/api/v1`). Requests are rate limited: exceeding the limit yields 429.\n"
    ): (
        "\n"
        "Plak publiceert statische sites per groep en site, met previews per pull request.\n"
        "Deze API bedient twee soorten clients: de beheer-SPA op de beheer-host en de CI die deploys uitvoert.\n"
        "\n"
        "## Authenticatie\n"
        "\n"
        "Er zijn twee manieren om je te identificeren, en elk endpoint accepteert er precies één of twee van:\n"
        "\n"
        "* **Beheersessie** - een `Secure`/`HttpOnly`/`SameSite=Strict`-cookie die je na SSO-login op de beheer-host\n"
        "  krijgt. Elke sessieroute eist daarnaast een **actief** lid: een nieuw of gedeactiveerd lid krijgt 403.\n"
        "  Mutaties (POST, PUT, DELETE) eisen bovendien de CSRF-double-submit-header `X-CSRF-Token`, met dezelfde\n"
        "  waarde als het CSRF-cookie.\n"
        "* **Bearer-token** - `Authorization: Bearer <token>`, uitsluitend op de twee deploy-endpoints, op\n"
        "  `DELETE /cli/session` en `GET /cli/whoami`, en, alleen met een CLI-token, op het aanmaken van een groep\n"
        "  of site en het koppelen van een repository. Het token is een van twee soorten:\n"
        "  * een **CI-ID-token** (JWT) van GitHub Actions of Forgejo Actions, met als audience precies de\n"
        "    beheer-URL van Plak (`PLAK_BASE_URL`) gevolgd door `/-/sites/` en het `id` van één site. Het geldt\n"
        "    alleen voor die site, en alleen zolang de repository van die workflow eraan gekoppeld is\n"
        "    (`PUT /sites/{groupSlug}/{siteSlug}/repository`); er is geen geheim om te bewaren. Een koppeling\n"
        "    van vóór het site-ID, van een repository die alleen aan die site gekoppeld was, accepteert ook een\n"
        "    token met de beheer-URL zelf als audience, tot er een andere repository gekoppeld wordt of een\n"
        "    sitebeheerder het site-ID verplicht maakt;\n"
        "  * een **CLI-token** `plakcli_...` uit `plak login` (zie de CLI-login-endpoints). Dat handelt als het lid\n"
        "    dat inlogde, met precies diens rollen.\n"
        "\n"
        "  Een Bearer-header op elk ander endpoint levert 401, ook als het token geldig is.\n"
        "\n"
        "Alle endpoints staan op de beheer-origin en bewaken de herkomst van het verzoek: een `Origin` of\n"
        "`Sec-Fetch-Site` die niet bij de beheer-origin hoort levert 403. CI en de CLI sturen geen van beide\n"
        "headers en passeren die bewaking dus ongehinderd.\n"
        "\n"
        "## Autorisatie\n"
        "\n"
        "Wie wat mag, hangt af van de groep: **groepsleden** beheren de sites van hun eigen groep volgens hun\n"
        "rol, en elk actief lid mag een groep aanmaken. Een **platformbeheerder** activeert en deactiveert\n"
        "daarnaast leden en leest het auditlog. Bij een CI-ID-token tellen de gekoppelde repository van de site,\n"
        "het site-ID dat de workflow noemt, en voor een live-deploy de live-branch.\n"
        "\n"
        "## Fouten\n"
        "\n"
        "Fouten zijn `application/problem+json` volgens RFC 9457: `{type, title, status, detail}`, aangevuld met\n"
        "het extensielid `code` met een stabiele, machineleesbare foutcode (`NOT_GROUP_MEMBER`, `SLUG_EXISTS`,\n"
        "`BODY_TOO_LARGE`, ...). Per endpoint staat hieronder welke statuscodes kunnen voorkomen. Codes zijn het\n"
        "contract voor clients; de `detail`-tekst is voor mensen en kan wijzigen.\n"
        "\n"
        "## Taal\n"
        "\n"
        "Dit document en de `title` en `detail` van elke fout zijn er in het Engels en in het Nederlands. Stuur\n"
        "`Accept-Language: nl` voor Nederlands; een client die niets vraagt krijgt Engels. Voor dit document gaat\n"
        "`?lang=nl` of `?lang=en` voor de header.\n"
        "\n"
        "## Conventies\n"
        "\n"
        "Velden op de draad zijn lowerCamelCase. Tijdstippen zijn RFC 3339 in UTC met een `Z`-achtervoegsel\n"
        "(`2026-09-12T09:30:00Z`). De volledige versie van deze API staat op elk antwoord in de `API-Version`-header;\n"
        "de majorversie staat in het pad (`/-/api/v1`). Verzoeken zijn ratelimited: bij overschrijding volgt 429.\n"
    ),
    "Session": "Sessie",
    "Who am I, and where does my content live.": "Wie ben ik, en waar staat mijn content.",
    "Overview": "Overzicht",
    "Home screen of the SPA: your own groups with their sites.": (
        "Startscherm van de SPA: de eigen groepen met hun sites."
    ),
    "Groups": "Groepen",
    "Create, view and delete groups, and set their default access.": (
        "Groepen aanmaken, bekijken, verwijderen en hun standaardtoegang zetten."
    ),
    "Group members": "Groepsleden",
    "Who may manage the sites of a group.": "Wie mag de sites van een groep beheren.",
    "Sites": "Sites",
    "Sites within a group, including who may view them.": "Sites binnen een groep, inclusief wie ze mag bekijken.",
    "Site members": "Siteleden",
    (
        "Who may manage this one site: the group members who reach it through the group, plus the site roles granted "
        "on top of that."
    ): (
        "Wie deze ene site mag beheren: de groepsleden die er via de groep bij kunnen, plus de siterollen die daar "
        "bovenop gegeven zijn."
    ),
    "Deploys": "Deploys",
    "Publishing from CI or from the SPA: live deploy, preview deploy and teardown.": (
        "Publiceren vanuit CI of vanuit de SPA: live-deploy, preview-deploy en teardown."
    ),
    "Versions": "Versies",
    "The deploy history of a site, and rolling back to an earlier version.": (
        "De deployhistorie van een site en terugrollen naar een eerdere versie."
    ),
    "Previews": "Previews",
    "Previews per pull request and their own access.": "Previews per pull request en hun afwijkende toegang.",
    "Invitees": "Genodigden",
    "Individual addresses that may view a site with the 'invitees' exception.": (
        "Individuele adressen die een site met de uitzondering 'genodigden' mogen zien."
    ),
    "Secret links": "Geheime links",
    "Secret links through which a site can be viewed without signing in.": (
        "Geheime links waarmee een site zonder inloggen te zien is."
    ),
    "Publishing from CI": "Publiceren vanuit CI",
    (
        "Linking the repository from which CI may publish to a site with an OIDC ID token (GitHub or Forgejo Actions), "
        "without a secret."
    ): (
        "De repository koppelen waaruit CI met een OIDC-ID-token naar een site mag publiceren (GitHub of Forgejo "
        "Actions), zonder geheim."
    ),
    "CLI login": "CLI-login",
    (
        "Signing in with the CLI (`plak login`) through the device flow: a code in the terminal, approval in the admin "
        "interface, and then tokens the CLI refreshes by itself."
    ): (
        "Inloggen met de CLI (`plak login`) via de device-flow: een code in de terminal, goedkeuren in het beheer, en "
        "daarna tokens die de CLI zelf ververst."
    ),
    "Platform administration": "Platformbeheer",
    "Platform administration: activating or deactivating members. Platform administrators only.": (
        "Platformbeheer: leden activeren of deactiveren. Alleen voor platformbeheerders."
    ),
    "Audit log": "Auditlog",
    "Reading the audit log: who did what, when, and with what outcome. Platform administrators only.": (
        "Het auditlog teruglezen: wie deed wat, wanneer en met welke uitkomst. Alleen voor platformbeheerders."
    ),
    "Publish a bundle, live or as a preview": "Een bundel publiceren, live of als preview",
    (
        "The endpoint CI uses. Send the built site as `multipart/form-data` with the `file` field; without the "
        "`preview` field the bundle replaces the live site, with `preview` it ends up under "
        "`/{groupSlug}/{siteSlug}/_preview/{ref}/`.\n"
        "\n"
        "**Who can call this:** any of three callers. (1) A CI ID token from GitHub or Forgejo Actions "
        "(`Authorization: Bearer <JWT>`) from the repository linked to this site, whose audience is exactly Plak's "
        "admin URL followed by `/-/sites/` and the `id` of this site, or, while the link still accepts it, exactly "
        "the admin URL itself; a live deploy must then come from a `push`, `workflow_dispatch` or `schedule`, and "
        "from the live branch if one is set; a preview may come from any branch. A token bound to another site is "
        "never used here: its own repository gets 409 `SITE_MOVED` with the address of that site. (2) A CLI token "
        "from `plak login` (`Authorization: "
        "Bearer plakcli_...`): it acts as the member who signed in, with exactly their roles. (3) An admin session "
        "plus CSRF header. With (2) and (3) the member must be active and have at least the `editor` role on this "
        "site. This endpoint and the preview teardown are, together with the CLI session endpoints, the creation of a "
        "group or site and the linking of a repository, the only ones that accept a Bearer token; elsewhere that "
        "header yields 401.\n"
        "\n"
        "**Flow:** authorization comes first, only then is the body read, so a refused request costs no upload. The "
        "upload streams to disk and is unpacked against the limits below. Every deploy, whether it succeeds or is "
        "refused, ends up in the audit log.\n"
        "\n"
        "**Site root:** enclosing directories are repeatedly unwrapped while the root directory contains exactly one "
        "directory and nothing else, so an archive with only `mysite/dist/index.html` simply lands on the site root. "
        "Operating system metadata does not count and is not published either: the `__MACOSX` directory, `.DS_Store` "
        "and the AppleDouble files that start with `._`. A zip you make with a right-click in Finder therefore just "
        "works. After that there must be an `index.html` in the root directory; otherwise the response is 422 "
        "`NO_INDEX`, with the index paths found in `indexCandidates`. If the site is in a directory next to other "
        "things (a zipped project directory), send that directory as the `basePath` field: this confirms one of the "
        "suggestions. Plak never picks one of several candidates itself, because it would silently leave out files you "
        "thought you were publishing. The `basePath` is relative to the root directory after unwrapping, but a path "
        "that includes the unwrapped prefix works too, and a fixed value keeps working if unwrapping has already taken "
        "that directory away.\n"
        "\n"
        "**Limits** (configurable; these are the defaults): the request body is at most 100 MB "
        "(`PLAK_INGEST_MAX_BODY`), a single unpacked file 50 MB (`PLAK_INGEST_MAX_FILE`), the entire unpacked site 200 "
        "MB (`PLAK_INGEST_MAX_TOTAL`), with at most 1000 entries (`PLAK_INGEST_MAX_FILES`) and 10 levels of directory "
        "depth (`PLAK_INGEST_MAX_DEPTH`). These apply to what is published: what falls outside the `basePath` does not "
        "count. The archive as a whole may contain at most fifty times as many entries, and for a `.tar.gz` the "
        "unpacked size of all members counts, including the unpublished ones: a tar has no index, so getting to the "
        "next header means decompressing everything in between. The limits are enforced during unpacking and the "
        "headers are checked against the same limits beforehand, so a zip or tar bomb does not get past either.\n"
        "\n"
        "**Example** (`$PLAK_TOKEN` is a CI ID token or a CLI token):\n"
        "\n"
        "```\n"
        "curl --fail --silent --show-error \\\n"
        "  --header \"Authorization: Bearer $PLAK_TOKEN\" \\\n"
        "  --form file=@dist.zip \\\n"
        "  --form preview=pr-42 \\\n"
        "  \"$PLAK_ADMIN_URL/-/api/v1/sites/aurora/docs/deploys\"\n"
        "```\n"
        "\n"
        "Response: `201` with `{\"versionId\": \"...\"}`. Omit `--form preview=...` for a live deploy."
    ): (
        "Het endpoint waar CI op bouwt. Stuur de gebouwde site als `multipart/form-data` met het veld `file`; zonder "
        "het veld `preview` vervangt de bundel de live site, met `preview` komt hij onder "
        "`/{groupSlug}/{siteSlug}/_preview/{ref}/` te staan.\n"
        "\n"
        "**Mag:** drie manieren. (1) Een CI-ID-token van GitHub of Forgejo Actions (`Authorization: Bearer <JWT>`) uit "
        "de repository die aan deze site gekoppeld is, met als audience precies de beheer-URL van Plak gevolgd door "
        "`/-/sites/` en het `id` van deze site, of, zolang de koppeling dat nog accepteert, precies de beheer-URL "
        "zelf; een live-deploy moet dan uit een `push`, `workflow_dispatch` of `schedule` komen, en van de "
        "live-branch als die is ingesteld; een preview mag vanaf elke branch. Een token voor een andere site wordt "
        "hier nooit gebruikt: de eigen repository van dat token krijgt 409 `SITE_MOVED` met het adres van die "
        "site. (2) Een CLI-token uit `plak login` (`Authorization: Bearer "
        "plakcli_...`): dat handelt als het lid dat inlogde, met precies diens rollen. (3) Een beheersessie plus "
        "CSRF-header. Bij (2) en (3) moet het lid actief zijn en op deze site minstens de rol `editor` hebben. Dit "
        "endpoint en de preview-teardown zijn samen met de CLI-sessie-endpoints, het aanmaken van een groep of site en "
        "het koppelen van een repository de enige die een Bearer-token accepteren; elders levert die header 401.\n"
        "\n"
        "**Verloop:** eerst wordt geautoriseerd, pas daarna wordt het lichaam gelezen, zodat een geweigerd verzoek "
        "geen upload kost. De upload streamt naar schijf en wordt uitgepakt tegen de limieten hieronder. Elke deploy, "
        "geslaagd of geweigerd, komt in het auditlogboek.\n"
        "\n"
        "**Hoofdmap van de site:** omhullende mappen worden afgepeld zolang de hoofdmap precies één map bevat en "
        "verder niets, dus een archief met alleen `mijnsite/dist/index.html` landt gewoon op de siteroot. Metadata van "
        "het besturingssysteem telt daarbij niet mee en wordt ook niet gepubliceerd: de map `__MACOSX`, `.DS_Store` en "
        "de AppleDouble-bestanden die met `._` beginnen. Een zip die je met rechtsklik in de Finder maakt werkt "
        "daardoor gewoon. Daarna moet er een `index.html` in de hoofdmap staan; zo niet, dan volgt 422 `NO_INDEX` met "
        "de gevonden index-paden in `indexCandidates`. Staat de site in een map naast andere dingen (een gezipte "
        "projectmap), stuur die map dan als veld `basePath` mee: dat is de bevestiging van het voorstel. Plak kiest "
        "nooit zelf een van meerdere kandidaten, want dan zou het stilzwijgend bestanden weglaten die je dacht te "
        "publiceren. Het `basePath` staat relatief aan de hoofdmap ná het afpellen, maar de spelling mét het afgepelde "
        "voorvoegsel werkt net zo goed, en een vaste waarde blijft werken als het afpellen die map al weggenomen "
        "heeft.\n"
        "\n"
        "**Limieten** (instelbaar; dit zijn de standaardwaarden): het verzoeklichaam is hoogstens 100 MB "
        "(`PLAK_INGEST_MAX_BODY`), een los uitgepakt bestand 50 MB (`PLAK_INGEST_MAX_FILE`), de hele uitgepakte site "
        "200 MB (`PLAK_INGEST_MAX_TOTAL`), met hoogstens 1000 entries (`PLAK_INGEST_MAX_FILES`) en 10 niveaus "
        "mapdiepte (`PLAK_INGEST_MAX_DEPTH`). Die gelden op wat gepubliceerd wordt: wat buiten het `basePath` valt "
        "telt niet mee. Het archief als geheel mag hoogstens vijftig keer zoveel entries bevatten, en bij een "
        "`.tar.gz` telt de uitgepakte omvang van alle leden mee, ook de niet-gepubliceerde: een tar heeft geen index, "
        "dus bij de volgende header komen betekent alles ertussen decomprimeren. De limieten worden tijdens het "
        "uitpakken bewaakt en de headers worden vooraf al tegen dezelfde grenzen gehouden, dus ook een zip- of tar-bom "
        "komt er niet langs.\n"
        "\n"
        "**Voorbeeld** (`$PLAK_TOKEN` is een CI-ID-token of een CLI-token):\n"
        "\n"
        "```\n"
        "curl --fail --silent --show-error \\\n"
        "  --header \"Authorization: Bearer $PLAK_TOKEN\" \\\n"
        "  --form file=@dist.zip \\\n"
        "  --form preview=pr-42 \\\n"
        "  \"$PLAK_ADMIN_URL/-/api/v1/sites/aurora/docs/deploys\"\n"
        "```\n"
        "\n"
        "Antwoord: `201` met `{\"versionId\": \"...\"}`. Laat `--form preview=...` weg voor een live-deploy."
    ),
    "Slug of the group; the first path segment of every site URL.": (
        "Slug van de groep; het eerste padsegment van elke site-URL."
    ),
    "Slug of the site within that group.": "Slug van de site binnen die groep.",
    "The bundle was unpacked and published; the ID of the new version is returned.": (
        "De bundel is uitgepakt en gepubliceerd; de id van de nieuwe versie komt terug."
    ),
    "Bad request. The client aborted the upload before the body was complete (`CLIENT_ABORTED`).": (
        "Ongeldig verzoek. De client brak de upload af voordat het lichaam compleet was (`CLIENT_ABORTED`)."
    ),
    (
        "Not authenticated. Neither a bearer token nor a valid admin session was sent (`NO_AUTHENTICATION`), the CLI "
        "token is invalid, revoked or expired (`TOKEN_INVALID`), or the CI token is refused: it does not come from "
        "GitHub or a configured Forgejo (`CI_ISSUER_UNKNOWN`), the signature is invalid or the token is expired or not "
        "yet valid (`CI_TOKEN_INVALID`), or the audience is neither exactly the admin URL nor exactly the admin URL "
        "followed by `/-/sites/` and a site id, or it names a site that the token's repository is not linked to, "
        "whatever the address (`CI_AUDIENCE_MISMATCH`). The response then carries `WWW-Authenticate: Bearer`."
    ): (
        "Niet geauthenticeerd. Er is noch een bearer-token noch een geldige beheersessie meegestuurd "
        "(`NO_AUTHENTICATION`), het CLI-token is ongeldig, ingetrokken of verlopen (`TOKEN_INVALID`), of het CI-token "
        "wordt geweigerd: het komt niet van GitHub of een geconfigureerde Forgejo (`CI_ISSUER_UNKNOWN`), de "
        "handtekening of geldigheid klopt niet (`CI_TOKEN_INVALID`), of de audience is noch precies de beheer-URL "
        "noch precies de beheer-URL gevolgd door `/-/sites/` en een site-ID, of hij noemt een site waaraan de "
        "repository van het token niet gekoppeld is, op welk adres ook (`CI_AUDIENCE_MISMATCH`). Het antwoord "
        "draagt dan `WWW-Authenticate: Bearer`."
    ),
    (
        "Forbidden. With a CI token: the repository is not linked to this site (`CI_REPOSITORY_NOT_TRUSTED`), the "
        "token names no site id while the link of this site requires one (`CI_SITE_ID_REQUIRED`), or a "
        "live deploy does not come from `push`, `workflow_dispatch` or `schedule`, or not from the live branch "
        "(`CI_BRANCH_NOT_ALLOWED`). With a CLI token or session: the member does not have at least the `editor` role "
        "on this site (`INSUFFICIENT_ROLE`) or is not active (`MEMBER_NOT_ACTIVE`); with a session also: the CSRF "
        "header is missing or wrong (`CSRF_INVALID`). A request from an origin other than the admin host is refused as "
        "well; CI and the CLI send no `Origin` and pass that check."
    ): (
        "Geen toegang. Bij een CI-token: de repository is niet aan deze site gekoppeld (`CI_REPOSITORY_NOT_TRUSTED`), "
        "het token noemt geen site-ID terwijl de koppeling van deze site dat eist (`CI_SITE_ID_REQUIRED`), "
        "of een live-deploy komt niet uit `push`, `workflow_dispatch` of `schedule`, of niet van de live-branch "
        "(`CI_BRANCH_NOT_ALLOWED`). Bij een CLI-token of sessie: het lid heeft op deze site niet minimaal de rol "
        "`editor` (`INSUFFICIENT_ROLE`) of is niet actief (`MEMBER_NOT_ACTIVE`); bij een sessie ook: de CSRF-header "
        "ontbreekt of klopt niet (`CSRF_INVALID`). Een verzoek van een andere origin dan de beheer-host wordt eveneens "
        "geweigerd; CI en de CLI sturen geen `Origin` en passeren die bewaking."
    ),
    "Not found. Unknown group or unknown site (`UNKNOWN_SITE`).": (
        "Niet gevonden. Onbekende groep of onbekende site (`UNKNOWN_SITE`)."
    ),
    (
        "Conflict. The CI token is bound to a site that is not at this address, and the token's repository is linked "
        "to that site: the `site:` of the workflow is old or mistyped (`SITE_MOVED`). The `detail` names the current "
        "address of the site the token is bound to; nothing is published."
    ): (
        "Conflict. Het CI-token is gebonden aan een site die niet op dit adres staat, en de repository van het token "
        "is aan die site gekoppeld: de `site:` van de workflow is oud of verkeerd getypt (`SITE_MOVED`). De `detail` "
        "noemt het huidige adres van de site waaraan het token gebonden is; er wordt niets gepubliceerd."
    ),
    (
        "Content too large. The upload is larger than the body limit (`BODY_TOO_LARGE`), or the bundle unpacks too "
        "large: `FILE_TOO_LARGE`, `TOTAL_TOO_LARGE` or `TOO_MANY_FILES` (the last one also when the archive as a whole "
        "has too many entries; the message says which of the two, and carries `indexCandidates` where possible, "
        "because a zipped project directory runs into this first)."
    ): (
        "Inhoud te groot. De upload is groter dan de bodylimiet (`BODY_TOO_LARGE`), of de bundel wordt uitgepakt te "
        "groot: `FILE_TOO_LARGE`, `TOTAL_TOO_LARGE` of `TOO_MANY_FILES` (die laatste ook wanneer het archief als "
        "geheel te veel entries heeft; de melding zegt welke van de twee, en draagt waar mogelijk `indexCandidates`, "
        "want een gezipte projectmap loopt hier als eerste op vast)."
    ),
    (
        "Unprocessable input. The request is not multipart/form-data (`NOT_MULTIPART`), the `file` field is missing "
        "(`FILE_MISSING`), the multipart structure is wrong (`MULTIPART_INVALID`), `preview` is not a valid slug "
        "(`PREVIEW_REF_INVALID`), or the bundle is refused: unknown format (`UNKNOWN_FORMAT`), unreadable or empty "
        "archive (`INVALID_ARCHIVE`, `EMPTY_ARCHIVE`), an unsafe path in it (`PATH_TRAVERSAL`, `ABSOLUTE_PATH`, "
        "`SYMLINK_REFUSED`, `HARDLINK_REFUSED`, `SPECIAL_FILE`, `RESERVED_SEGMENT`, `TOO_DEEP`, `DUPLICATE_PATH`, "
        "`NULL_BYTE`, `EMPTY_PATH`), a file that does not belong on a website (`SECRET_FILE`: a `.git` directory or an "
        "`.env` file, which come along automatically with a zipped project directory), no `index.html` in the root "
        "directory (`NO_INDEX`, with the paths found in `indexCandidates`), or a `basePath` that is not valid "
        "(`BASE_PATH_INVALID`), is not a directory in the bundle (`BASE_PATH_UNKNOWN`, also when the path points to a "
        "file) or contains no `index.html` (`BASE_PATH_WITHOUT_INDEX`, also when that directory contains no files at "
        "all)."
    ): (
        "Onverwerkbare invoer. Het verzoek is geen multipart/form-data (`NOT_MULTIPART`), het veld `file` ontbreekt "
        "(`FILE_MISSING`), de multipart-vorm klopt niet (`MULTIPART_INVALID`), `preview` is geen geldige slug "
        "(`PREVIEW_REF_INVALID`), of de bundel wordt geweigerd: onbekende vorm (`UNKNOWN_FORMAT`), onleesbaar of leeg "
        "archief (`INVALID_ARCHIVE`, `EMPTY_ARCHIVE`), een onveilig pad erin (`PATH_TRAVERSAL`, `ABSOLUTE_PATH`, "
        "`SYMLINK_REFUSED`, `HARDLINK_REFUSED`, `SPECIAL_FILE`, `RESERVED_SEGMENT`, `TOO_DEEP`, `DUPLICATE_PATH`, "
        "`NULL_BYTE`, `EMPTY_PATH`), een bestand dat niet op een website hoort (`SECRET_FILE`: een `.git`-map of een "
        "`.env`-bestand, die met een gezipte projectmap vanzelf meekomen), geen `index.html` in de hoofdmap "
        "(`NO_INDEX`, met de gevonden paden in `indexCandidates`), of een `basePath` dat niet deugt "
        "(`BASE_PATH_INVALID`), geen map in de bundel is (`BASE_PATH_UNKNOWN`, ook wanneer het pad naar een bestand "
        "wijst) of geen `index.html` bevat (`BASE_PATH_WITHOUT_INDEX`, ook wanneer die map helemaal geen bestanden "
        "bevat)."
    ),
    "Too many requests. The rate limit budget is used up; try again later.": (
        "Te veel verzoeken. Het ratelimit-budget is op; probeer het later opnieuw."
    ),
    (
        "Unavailable. The CI provider cannot be reached to fetch the keys or to check the repository "
        "(`CI_PROVIDER_UNREACHABLE`); try again later. Or the content volume has too little free space for this upload "
        "(`STORAGE_UNAVAILABLE`): checked in advance against the declared `Content-Length`, and again while receiving "
        "and unpacking, so the volume never drops below the configured margin of free space. Whatever was already "
        "written is then cleaned up; try again later."
    ): (
        "Niet beschikbaar. De CI-provider is niet bereikbaar om de sleutels op te halen of de repository te "
        "controleren (`CI_PROVIDER_UNREACHABLE`); probeer het later opnieuw. Of het contentvolume heeft te weinig "
        "vrije ruimte voor deze upload (`STORAGE_UNAVAILABLE`): vooraf gemeten op de opgegeven `Content-Length`, en "
        "tijdens het ontvangen en uitpakken opnieuw, zodat het volume nooit onder de ingestelde marge vrije ruimte "
        "zakt. Wat al geschreven was wordt dan opgeruimd; probeer het later opnieuw."
    ),
    (
        "The bundle to publish, as `multipart/form-data`. The body is processed as a stream, so a large bundle never "
        "has to fit in memory."
    ): (
        "De te publiceren bundel, als `multipart/form-data`. Het lichaam wordt streamend verwerkt, dus een grote "
        "bundel hoeft nergens in geheugen te passen."
    ),
    (
        "The `file` field, required and exactly once. Either a single `.html` file (which becomes the site's "
        "`index.html`) or a `.zip`, `.tar.gz` or `.tgz` archive. The format is recognised by the file name; anything "
        "else yields 422 `UNKNOWN_FORMAT`. Paths in the archive must be relative and safe: absolute paths, `..`, "
        "symlinks and the reserved top-level segments `_preview` and `_version` are refused."
    ): (
        "Het bestandsveld, verplicht en precies één keer. Geaccepteerd worden een los `.html`-bestand (dat wordt de "
        "`index.html` van de site) of een archief `.zip`, `.tar.gz` of `.tgz`. De vorm wordt aan de bestandsnaam "
        "herkend; iets anders levert 422 `UNKNOWN_FORMAT`. Paden in het archief moeten relatief en veilig zijn: "
        "absolute paden, `..`, symlinks en de gereserveerde topsegmenten `_preview` en `_version` worden geweigerd."
    ),
    (
        "Optional. With this field the bundle becomes a preview under this ref, instead of the live site. The ref is a "
        "slug (lowercase letters, digits, hyphens, at most 63 characters) and is usually the pull request number. If "
        "the preview already exists, it is replaced and its expiry moves forward. Omit it for a live deploy."
    ): (
        "Optioneel. Met dit veld wordt de bundel een preview onder deze ref, in plaats van de live site. De ref is een "
        "slug (kleine letters, cijfers, koppeltekens, hoogstens 63 tekens) en is meestal het pull-requestnummer. "
        "Bestaat de preview al, dan wordt hij vervangen en schuift zijn vervaltijd vooruit. Weglaten voor een "
        "live-deploy."
    ),
    (
        "Optional. The directory inside the archive that becomes the root of the site; everything next to it is not "
        "published. The path is relative to the root directory after unwrapping enclosing directories and must be an "
        "existing directory containing an `index.html`. Meant to confirm one of the suggestions a refused deploy "
        "returns in `indexCandidates`; without this field Plak determines the root directory itself."
    ): (
        "Optioneel. De map binnen het archief die de hoofdmap van de site wordt; alles wat ernaast staat wordt niet "
        "gepubliceerd. Het pad is relatief aan de hoofdmap na het afpellen van omhullende mappen en moet een bestaande "
        "map met een `index.html` erin zijn. Bedoeld als bevestiging van het voorstel dat een geweigerde deploy "
        "meegeeft in `indexCandidates`; zonder dit veld bepaalt Plak de hoofdmap zelf."
    ),
    "Clean up a preview": "Een preview opruimen",
    (
        "Removes the preview with this ref, including its files. Meant for the CI step that runs when a pull request "
        "closes. The live site and the version history are left alone.\n"
        "\n"
        "**Who can call this:** the same as the deploy: a CI ID token from the linked repository (from any branch), a "
        "CLI token or an admin session with CSRF header of an active member with effective site role `editor` or "
        "higher.\n"
        "\n"
        "**Idempotent:** a ref that does not (any more) exist also yields 204, so a repeated cleanup step in CI does "
        "not fail on a rerun.\n"
        "\n"
        "**Example:**\n"
        "\n"
        "```\n"
        "curl --fail --silent --show-error --request DELETE \\\n"
        "  --header \"Authorization: Bearer $PLAK_TOKEN\" \\\n"
        "  \"$PLAK_ADMIN_URL/-/api/v1/sites/aurora/docs/previews/pr-42\"\n"
        "```"
    ): (
        "Haalt de preview met deze ref weg, inclusief haar bestanden. Bedoeld voor de CI-stap die draait als een pull "
        "request sluit. De live site en de versiehistorie blijven ongemoeid.\n"
        "\n"
        "**Mag:** hetzelfde als de deploy: een CI-ID-token uit de gekoppelde repository (vanaf elke branch), een "
        "CLI-token of een beheersessie met CSRF-header van een actief lid met effectieve siterol `editor` of ruimer.\n"
        "\n"
        "**Idempotent:** een ref die niet (meer) bestaat levert ook 204, zodat een herhaalde opruimstap in CI niet "
        "alsnog rood wordt.\n"
        "\n"
        "**Voorbeeld:**\n"
        "\n"
        "```\n"
        "curl --fail --silent --show-error --request DELETE \\\n"
        "  --header \"Authorization: Bearer $PLAK_TOKEN\" \\\n"
        "  \"$PLAK_ADMIN_URL/-/api/v1/sites/aurora/docs/previews/pr-42\"\n"
        "```"
    ),
    "Name of the preview, usually the pull request number or the branch name as a slug.": (
        "Naam van de preview, meestal het pull-requestnummer of de branchnaam als slug."
    ),
    "The preview no longer exists. No content is returned.": "De preview bestaat niet meer. Er komt geen inhoud terug.",
    "Unprocessable input. The ref in the path is not a valid slug (`PREVIEW_REF_INVALID`).": (
        "Onverwerkbare invoer. De ref in het pad is geen geldige slug (`PREVIEW_REF_INVALID`)."
    ),
    (
        "Unavailable. The CI provider cannot be reached to fetch the keys or to check the repository "
        "(`CI_PROVIDER_UNREACHABLE`); try again later."
    ): (
        "Niet beschikbaar. De CI-provider is niet bereikbaar om de sleutels op te halen of de repository te "
        "controleren (`CI_PROVIDER_UNREACHABLE`); probeer het later opnieuw."
    ),
    "Start signing in with the CLI": "Inloggen met de CLI beginnen",
    (
        "Step 1 of `plak login` (RFC 8628). The CLI receives a secret `deviceCode` and shows the `userCode` plus "
        "`verificationUri`; the member opens that page in the admin interface, compares the code and approves. "
        "Meanwhile the CLI calls `POST /cli/tokens` every `interval` seconds. The codes expire after ten minutes.\n"
        "\n"
        "**Who can call this:** anyone, without authentication. Each IP address has its own limit on the number of "
        "requests, on top of the general rate limit."
    ): (
        "Stap 1 van `plak login` (RFC 8628). De CLI krijgt een geheime `deviceCode` en toont de `userCode` plus "
        "`verificationUri`; het lid opent die pagina in het beheer, vergelijkt de code en keurt goed. Intussen vraagt "
        "de CLI elke `interval` seconden `POST /cli/tokens`. De codes verlopen na tien minuten.\n"
        "\n"
        "**Mag:** iedereen, zonder authenticatie. Per IP-adres geldt een eigen limiet op het aantal aanvragen, naast "
        "de algemene ratelimit."
    ),
    "The device code for the CLI and the user code for the member.": (
        "De apparaatcode voor de CLI en de gebruikerscode voor het lid."
    ),
    "Unprocessable input. `clientName` is longer than 100 characters.": (
        "Onverwerkbare invoer. `clientName` is langer dan 100 tekens."
    ),
    "Too many requests. Too many requests from this address (`TOO_MANY_REQUESTS`); try again later.": (
        "Te veel verzoeken. Te veel aanvragen vanaf dit adres (`TOO_MANY_REQUESTS`); probeer het later opnieuw."
    ),
    "Fetch or refresh tokens": "Tokens ophalen of verversen",
    (
        "With `grantType` `device_code`: the CLI asks whether the member has approved yet. Until then, the response is "
        "400 `AUTHORIZATION_PENDING`; polling faster than `interval` returns `SLOW_DOWN`, and the CLI must then wait "
        "five seconds longer between polls. After approval the tokens are returned once; after that the device code is "
        "spent.\n"
        "\n"
        "With `grantType` `refresh_token`: a new access token and a new refresh token. The old refresh token is "
        "invalid afterwards. If the refresh token that was just replaced is presented again within ten seconds (two "
        "refreshes at the same time), the response is `INVALID_GRANT` and the session remains valid. If a used refresh "
        "token is presented after that window, or an older one is presented, two parties hold the same session: Plak "
        "revokes the entire CLI session and writes `cli_refresh_reuse` to the audit log. A CLI session expires 30 days "
        "after the last refresh and in any case 90 days after sign-in.\n"
        "\n"
        "**Who can call this:** anyone with a valid device code or refresh token; there is no other authentication."
    ): (
        "Met `grantType` `device_code`: de CLI vraagt of het lid al heeft goedgekeurd. Zolang dat niet zo is volgt 400 "
        "`AUTHORIZATION_PENDING`; wie sneller vraagt dan `interval` krijgt `SLOW_DOWN` en wacht voortaan vijf seconden "
        "langer. Na goedkeuring komen er eenmalig tokens terug; daarna is de apparaatcode op.\n"
        "\n"
        "Met `grantType` `refresh_token`: een nieuw toegangstoken en een nieuw verversingstoken. Het oude "
        "verversingstoken is daarna ongeldig. Wordt het zojuist vervangen verversingstoken binnen tien seconden nog "
        "eens aangeboden (twee verversingen tegelijk), dan volgt `INVALID_GRANT` en blijft de sessie staan. Komt een "
        "al gebruikt verversingstoken later of ouder terug, dan hebben twee partijen dezelfde sessie in handen: Plak "
        "trekt de hele CLI-sessie in en schrijft `cli_refresh_reuse` in het auditlog. Een CLI-sessie verloopt 30 dagen "
        "na het laatste verversen en hoe dan ook 90 dagen na het koppelen.\n"
        "\n"
        "**Mag:** iedereen met een geldige apparaatcode of verversingstoken; er is verder geen authenticatie."
    ),
    "An access token and a new refresh token.": "Een toegangstoken en een nieuw verversingstoken.",
    (
        "Bad request. Not approved yet (`AUTHORIZATION_PENDING`), asked too fast (`SLOW_DOWN`), the device code has "
        "expired (`EXPIRED_TOKEN`), the member refused (`ACCESS_DENIED`), or the code or the refresh token is unknown, "
        "already used, expired or belongs to an inactive member (`INVALID_GRANT`)."
    ): (
        "Ongeldig verzoek. Nog niet goedgekeurd (`AUTHORIZATION_PENDING`), te snel gevraagd (`SLOW_DOWN`), de "
        "apparaatcode is verlopen (`EXPIRED_TOKEN`), het lid weigerde (`ACCESS_DENIED`), of de code of het "
        "verversingstoken is onbekend, al gebruikt, verlopen of hoort bij een niet-actief lid (`INVALID_GRANT`)."
    ),
    (
        "Unprocessable input. A field in the path or in the body does not have the shape the schema prescribes; "
        "`detail` names the fields."
    ): (
        "Onverwerkbare invoer. Een veld in het pad of in het lichaam heeft niet de vorm die het schema voorschrijft; "
        "`detail` noemt de velden."
    ),
    "Sign out with the CLI": "Uitloggen met de CLI",
    (
        "Revokes the CLI session (`plak logout`), with all its tokens; it disappears from the list of linked CLI "
        "sessions. Identify the session in one of two ways:\n"
        "\n"
        "* `Authorization: Bearer plakcli_...`: the access token, even if it has already expired, as long as it is "
        "genuine and the session still exists;\n"
        "* a JSON body `{\"refreshToken\": \"plakclr_...\"}` (as in RFC 7009), the current refresh token or one that "
        "was already used.\n"
        "\n"
        "The response is always 204, even for an unknown or already revoked token: this way it does not reveal which "
        "tokens exist. The audit log does distinguish the two cases (`cli_logout` with `allowed` or `refused`).\n"
        "\n"
        "**Who can call this:** anyone who holds a token of the session; also a member who has been deactivated in the "
        "meantime."
    ): (
        "Trekt de CLI-sessie in (`plak logout`), met al haar tokens; ze verdwijnt uit de lijst gekoppelde sessies. "
        "Noem de sessie op een van twee manieren:\n"
        "\n"
        "* `Authorization: Bearer plakcli_...`: het toegangstoken, ook als het al verlopen is, zolang het echt is en "
        "de sessie nog bestaat;\n"
        "* een JSON-lichaam `{\"refreshToken\": \"plakclr_...\"}` (zoals RFC 7009), het huidige of een al gebruikt "
        "verversingstoken.\n"
        "\n"
        "Het antwoord is altijd 204, ook voor een onbekend of al ingetrokken token: zo verraadt het niet welke tokens "
        "bestaan. Het auditlog onderscheidt de twee gevallen wel (`cli_logout` met `allowed` of `refused`).\n"
        "\n"
        "**Mag:** iedereen die een token van de sessie heeft; ook een lid dat inmiddels gedeactiveerd is."
    ),
    "The CLI session does not exist (any more). No content is returned.": (
        "De CLI-sessie bestaat niet (meer). Er komt geen inhoud terug."
    ),
    "Unprocessable input. The body is not an object with a `refreshToken` of at most 200 characters.": (
        "Onverwerkbare invoer. Het lichaam is geen object met een `refreshToken` van hoogstens 200 tekens."
    ),
    "Show the signed-in CLI member": "Wie is de CLI",
    (
        "The member on whose behalf the CLI acts (`plak whoami`), and when the CLI session expires if it is not "
        "refreshed. The member must still be active, as with every deploy.\n"
        "\n"
        "**Who can call this:** the holder of a valid CLI access token (`Authorization: Bearer plakcli_...`)."
    ): (
        "Het lid namens wie de CLI handelt (`plak whoami`), en tot wanneer de CLI-sessie loopt als hij niet meer "
        "ververst wordt. Het lid moet nog actief zijn, net als bij elke deploy.\n"
        "\n"
        "**Mag:** de houder van een geldig CLI-toegangstoken (`Authorization: Bearer plakcli_...`)."
    ),
    "The member behind this access token and when the session expires.": (
        "Het lid achter dit toegangstoken en wanneer de sessie verloopt."
    ),
    (
        "Not authenticated. Missing, invalid, expired or revoked access token (`TOKEN_INVALID`). The response carries "
        "`WWW-Authenticate: Bearer`."
    ): (
        "Niet geauthenticeerd. Geen of een ongeldig, verlopen of ingetrokken toegangstoken (`TOKEN_INVALID`). Het "
        "antwoord draagt `WWW-Authenticate: Bearer`."
    ),
    "Forbidden. The member behind this CLI session is no longer active (`MEMBER_NOT_ACTIVE`).": (
        "Geen toegang. Het lid achter deze CLI-sessie is niet (meer) actief (`MEMBER_NOT_ACTIVE`)."
    ),
    "The signed-in member": "Het ingelogde lid",
    (
        "Returns the member behind the current admin session, plus the origin on which the SPA builds content links, "
        "preview links and secret links. This is also the cheapest way to check whether the session is still valid.\n"
        "\n"
        "`groupRoles` and `siteRoles` say where this member is allowed to do something, so the SPA knows what to "
        "offer. `siteRoles` only contains the sites with a direct site role; on every other site of a group the group "
        "role from `groupRoles` applies. A platform administrator can have both lists empty: they manage people and "
        "groups, and give themselves a group role to make content visible to them.\n"
        "\n"
        "**Who can call this:** any active member with a valid admin session. A member with status `deactivated` gets "
        "403, as on every other endpoint."
    ): (
        "Geeft het lid achter de huidige beheersessie, plus de origin waarop de SPA content-links, preview-links en "
        "geheime links bouwt. Dit is meteen de goedkoopste manier om te controleren of de sessie nog geldig is.\n"
        "\n"
        "`groupRoles` en `siteRoles` zeggen waar dit lid iets mag, zodat de SPA weet wat ze aanbiedt. `siteRoles` "
        "bevat alleen de sites met een eigen siterol; op elke andere site van een groep geldt de groepsrol uit "
        "`groupRoles`. Een platformbeheerder kan beide lijsten leeg hebben: hij beheert mensen en groepen, en geeft "
        "zichzelf voor content een zichtbare groepsrol.\n"
        "\n"
        "**Mag:** elk actief lid met een geldige beheersessie. Een lid met status `deactivated` krijgt 403, net als op "
        "elk ander endpoint."
    ),
    "The member behind the current session, with the content origin.": (
        "Het lid achter de huidige sessie, met de content-origin."
    ),
    "Not authenticated. There is no valid admin session: the cookie is missing, invalid or expired (`NO_SESSION`).": (
        "Niet geauthenticeerd. Er is geen geldige beheersessie: het cookie ontbreekt, is ongeldig of is verlopen "
        "(`NO_SESSION`)."
    ),
    (
        "Forbidden. The request comes from an origin other than the admin host, or the member is not (or no longer) "
        "active (`ORIGIN_REFUSED`, `MEMBER_NOT_ACTIVE`)."
    ): (
        "Geen toegang. Het verzoek komt van een andere origin dan de beheer-host, of het lid is niet (meer) actief "
        "(`ORIGIN_REFUSED`, `MEMBER_NOT_ACTIVE`)."
    ),
    "Too many requests. The rate limit budget for this session is used up; try again later.": (
        "Te veel verzoeken. Het ratelimit-budget voor deze sessie is op; probeer het later opnieuw."
    ),
    "Set my language": "Mijn taal instellen",
    (
        "Records the language in which this member wants to read the admin interface: `nl`, `en`, or `null` to leave "
        "the language to the browser again. The choice is stored on the account, not on the device, so it applies "
        "everywhere this member signs in.\n"
        "\n"
        "The SPA then sends this language in `Accept-Language`, so problem+json messages come back in it too.\n"
        "\n"
        "**Who can call this:** any active member, for themselves, with a valid CSRF header."
    ): (
        "Legt vast in welke taal dit lid het beheer wil lezen: `nl`, `en`, of `null` om de taal weer aan de browser "
        "over te laten. De keuze staat op het account, niet op het apparaat, dus hij geldt overal waar dit lid "
        "inlogt.\n"
        "\n"
        "Wat de SPA erna doet is de taal meesturen in `Accept-Language`, zodat ook een problem+json-melding in die "
        "taal terugkomt.\n"
        "\n"
        "**Mag:** elk actief lid, voor zichzelf, met een geldige CSRF-header."
    ),
    "The language choice has been recorded. No content is returned.": (
        "De taalkeuze is vastgelegd. Er komt geen inhoud terug."
    ),
    (
        "Forbidden. The request comes from an origin other than the admin host, or the member is not (or no longer) "
        "active (`ORIGIN_REFUSED`, `MEMBER_NOT_ACTIVE`). The `X-CSRF-Token` header is missing or does not match the "
        "CSRF cookie (`CSRF_INVALID`)."
    ): (
        "Geen toegang. Het verzoek komt van een andere origin dan de beheer-host, of het lid is niet (meer) actief "
        "(`ORIGIN_REFUSED`, `MEMBER_NOT_ACTIVE`). De header `X-CSRF-Token` ontbreekt of komt niet overeen met het "
        "CSRF-cookie (`CSRF_INVALID`)."
    ),
    "Unprocessable input. `language` is not a supported language and not `null`.": (
        "Onverwerkbare invoer. `language` is geen ondersteunde taal en niet `null`."
    ),
    "All visible groups with their sites": "Alle zichtbare groepen met hun sites",
    (
        "The home screen of the admin SPA: per group the sites with their access, whether anything is live, when "
        "something was last deployed and how many previews are open.\n"
        "\n"
        "**Who can call this:** any active member. The member sees the groups in which they have a group role, each "
        "with all its sites, plus the groups in which they only have a site role: of those only the sites in question "
        "appear, because a site role grants nothing at group level. A platform administrator likewise only sees their "
        "own groups. Anyone without a role anywhere gets an empty list, not a 403."
    ): (
        "Het startscherm van de beheer-SPA: per groep de sites met hun toegang, of er iets live staat, wanneer er voor "
        "het laatst is gedeployd en hoeveel previews er openstaan.\n"
        "\n"
        "**Mag:** elk actief lid. Het lid ziet de groepen waar het een groepsrol in heeft, elk met al hun sites, plus "
        "de groepen waar het alleen een siterol heeft: daarvan verschijnen uitsluitend die sites, want een siterol "
        "geeft niets op groepsniveau. Een platformbeheerder ziet net zo alleen zijn eigen groepen. Wie nergens een rol "
        "heeft krijgt een lege lijst, geen 403."
    ),
    "The groups this member is allowed to see, each with its sites.": (
        "De groepen die dit lid mag zien, elk met hun sites."
    ),
    "Create a group": "Groep aanmaken",
    (
        "Creates a group and makes the creator group admin (`admin`) right away, so they can put sites in it. The new "
        "group starts with base `site_team` and no exceptions, unless `defaultAccess` asks for something else; that "
        "can be changed afterwards. Because the creator becomes group admin, choosing the default access right away is "
        "no more than they may do afterwards anyway.\n"
        "\n"
        "**Who can call this:** any active member, with a valid CSRF header. Creating a group is not a reserved "
        "action: anyone who wants to publish something must be able to make a place for it themselves. Besides the "
        "admin session with a valid CSRF header, this is also allowed with a CLI token from `plak login` "
        "(`Authorization: Bearer plakcli_...`), with exactly the same role check; the CSRF header is then not needed, "
        "because a token is not sent along automatically the way a cookie is. A CI ID token is not allowed. Each "
        "member has a limit of 20 new groups and sites combined per 60 minutes, across the admin interface and the CLI "
        "together."
    ): (
        "Maakt een groep aan en maakt de aanmaker meteen groepsbeheerder (`admin`), zodat hij er sites in kan zetten. "
        "De nieuwe groep begint met basis `site_team` en geen uitzonderingen, tenzij `defaultAccess` iets anders "
        "vraagt; dat is daarna te wijzigen. Omdat de aanmaker groepsbeheerder wordt, is de standaardtoegang meteen "
        "kiezen niets meer dan hij daarna zelf ook mag.\n"
        "\n"
        "**Mag:** ieder actief lid, met een geldige CSRF-header. Een groep aanmaken is geen voorbehouden handeling: "
        "wie iets wil publiceren moet daar zelf een plek voor kunnen maken. Behalve met de beheersessie en een geldige "
        "CSRF-header mag dit ook met een CLI-token uit `plak login` (`Authorization: Bearer plakcli_...`), met precies "
        "dezelfde rolcontrole; de CSRF-header vervalt dan, want een token gaat niet vanzelf mee zoals een cookie. Een "
        "CI-ID-token mag het niet. Per lid geldt een limiet van 20 nieuwe groepen en sites samen per 60 minuten, via "
        "beheer en CLI samen."
    ),
    "The created group.": "De aangemaakte groep.",
    (
        "Not authenticated. There is no valid admin session: the cookie is missing, invalid or expired (`NO_SESSION`). "
        "With a Bearer header: it is not a CLI token from `plak login`, or it is invalid, revoked or expired "
        "(`TOKEN_INVALID`); the response then carries `WWW-Authenticate: Bearer`."
    ): (
        "Niet geauthenticeerd. Er is geen geldige beheersessie: het cookie ontbreekt, is ongeldig of is verlopen "
        "(`NO_SESSION`). Met een Bearer-header: het is geen CLI-token uit `plak login`, of het is ongeldig, "
        "ingetrokken of verlopen (`TOKEN_INVALID`); het antwoord draagt dan `WWW-Authenticate: Bearer`."
    ),
    (
        "Forbidden. The request comes from an origin other than the admin host, or the member is not (or no longer) "
        "active (`ORIGIN_REFUSED`, `MEMBER_NOT_ACTIVE`). The `X-CSRF-Token` header is missing or does not match the "
        "CSRF cookie (`CSRF_INVALID`). With a CLI token: the member is not (or no longer) active (`MEMBER_NOT_ACTIVE`)."
    ): (
        "Geen toegang. Het verzoek komt van een andere origin dan de beheer-host, of het lid is niet (meer) actief "
        "(`ORIGIN_REFUSED`, `MEMBER_NOT_ACTIVE`). De header `X-CSRF-Token` ontbreekt of komt niet overeen met het "
        "CSRF-cookie (`CSRF_INVALID`). Met een CLI-token: het lid is niet (meer) actief (`MEMBER_NOT_ACTIVE`)."
    ),
    "Conflict. A group with this slug already exists (`SLUG_EXISTS`).": (
        "Conflict. Er bestaat al een groep met deze slug (`SLUG_EXISTS`)."
    ),
    "Unprocessable input. The slug is invalid or reserved (`SLUG_INVALID`), or the name is empty (`FIELD_EMPTY`).": (
        "Onverwerkbare invoer. De slug is ongeldig of gereserveerd (`SLUG_INVALID`), of de naam is leeg "
        "(`FIELD_EMPTY`)."
    ),
    (
        "Too many requests. The rate limit budget for this session is used up; try again later. This member has "
        "already created 20 groups and sites combined in the past hour (`TOO_MANY_CREATIONS`); the `Retry-After` "
        "header says after how many seconds it is allowed again."
    ): (
        "Te veel verzoeken. Het ratelimit-budget voor deze sessie is op; probeer het later opnieuw. Dit lid heeft in "
        "het afgelopen uur al 20 groepen en sites samen aangemaakt (`TOO_MANY_CREATIONS`); de header `Retry-After` "
        "zegt na hoeveel seconden het weer kan."
    ),
    "Group with sites and members": "Groep met sites en leden",
    (
        "Everything the group page of the SPA needs in one go.\n"
        "\n"
        "**Who can call this:** group role `reader` or higher, or a platform administrator."
    ): (
        "Alles wat de groepspagina van de SPA in één keer nodig heeft.\n"
        "\n"
        "**Mag:** groepsrol `reader` of ruimer, of een platformbeheerder."
    ),
    "The group with its sites and members.": "De groep met haar sites en leden.",
    (
        "Forbidden. The request comes from an origin other than the admin host, or the member is not (or no longer) "
        "active (`ORIGIN_REFUSED`, `MEMBER_NOT_ACTIVE`). The member's role in this group is insufficient for this "
        "action (`INSUFFICIENT_ROLE`)."
    ): (
        "Geen toegang. Het verzoek komt van een andere origin dan de beheer-host, of het lid is niet (meer) actief "
        "(`ORIGIN_REFUSED`, `MEMBER_NOT_ACTIVE`). Je rol in deze groep is te smal voor deze handeling "
        "(`INSUFFICIENT_ROLE`)."
    ),
    "Not found. Unknown group (`UNKNOWN_GROUP`).": "Niet gevonden. Onbekende groep (`UNKNOWN_GROUP`).",
    "Delete a group": "Groep verwijderen",
    (
        "Deletes the group with all its sites, and per site everything `DELETE /sites/{group}/{site}` also removes: "
        "versions, previews, invitees, secret links, the linked repository and the unpacked files on disk. "
        "Irreversible; every URL of the group returns 404 afterwards.\n"
        "\n"
        "**Who can call this:** group role `admin`, with a valid CSRF header. A platform administrator without a group "
        "role is not allowed."
    ): (
        "Verwijdert de groep met al haar sites, en per site alles wat `DELETE /sites/{group}/{site}` ook weghaalt: "
        "versies, previews, genodigden, geheime links, de gekoppelde repository en de uitgepakte bestanden op schijf. "
        "Onomkeerbaar; elke URL van de groep geeft daarna 404.\n"
        "\n"
        "**Mag:** groepsrol `admin`, met een geldige CSRF-header. Een platformbeheerder die geen groepsrol heeft mag "
        "het niet."
    ),
    "The group and all its sites have been deleted. No content is returned.": (
        "De groep en al haar sites zijn verwijderd. Er komt geen inhoud terug."
    ),
    (
        "Forbidden. The request comes from an origin other than the admin host, or the member is not (or no longer) "
        "active (`ORIGIN_REFUSED`, `MEMBER_NOT_ACTIVE`). The `X-CSRF-Token` header is missing or does not match the "
        "CSRF cookie (`CSRF_INVALID`). The member's role in this group is insufficient for this action "
        "(`INSUFFICIENT_ROLE`)."
    ): (
        "Geen toegang. Het verzoek komt van een andere origin dan de beheer-host, of het lid is niet (meer) actief "
        "(`ORIGIN_REFUSED`, `MEMBER_NOT_ACTIVE`). De header `X-CSRF-Token` ontbreekt of komt niet overeen met het "
        "CSRF-cookie (`CSRF_INVALID`). Je rol in deze groep is te smal voor deze handeling (`INSUFFICIENT_ROLE`)."
    ),
    "Set the default access of a group": "Standaardtoegang van een groep zetten",
    (
        "Sets the access that new sites in this group start with: the base and the two exceptions in one go. Existing "
        "sites are not affected; you set those per site.\n"
        "\n"
        "**Who can call this:** group role `admin`, with a valid CSRF header. This is policy about content, so a "
        "platform administrator without a group role is not allowed."
    ): (
        "Zet de toegang die sites meekrijgen die hierna in deze groep worden aangemaakt: de basis en de twee "
        "uitzonderingen in één keer. Bestaande sites veranderen niet mee; die zet je per site.\n"
        "\n"
        "**Mag:** groepsrol `admin`, met een geldige CSRF-header. Dit is beleid over content, dus een "
        "platformbeheerder die geen groepsrol heeft mag het niet."
    ),
    "The group with its new default access.": "De groep met haar nieuwe standaardtoegang.",
    "Create a site": "Site aanmaken",
    (
        "Creates a site within a group. The site gets the default access of the group, or whatever `access` asks for "
        "instead, and is served at `/{groupSlug}/{siteSlug}/` on the content origin, as soon as something has been "
        "deployed to it.\n"
        "\n"
        "**Who can call this:** group role `editor` or higher, with a valid CSRF header; the creator becomes `admin` "
        "of the site they create, and may therefore choose the access right away: that is no more than they may do "
        "afterwards anyway. A platform administrator without a group role is not allowed. Besides the admin session "
        "with a valid CSRF header, this is also allowed with a CLI token from `plak login` (`Authorization: Bearer "
        "plakcli_...`), with exactly the same role check; the CSRF header is then not needed, because a token is not "
        "sent along automatically the way a cookie is. A CI ID token is not allowed. Each member has a limit of 20 new "
        "groups and sites combined per 60 minutes, across the admin interface and the CLI together."
    ): (
        "Maakt een site binnen een groep. De site krijgt de standaardtoegang van de groep, of wat `access` daarvan "
        "afwijkend vraagt, en staat op `/{groupSlug}/{siteSlug}/` op de content-origin, zodra er iets naartoe is "
        "gedeployd.\n"
        "\n"
        "**Mag:** groepsrol `editor` of ruimer, met een geldige CSRF-header; de maker wordt `admin` van de site die "
        "hij aanmaakt, en mag de toegang dus meteen kiezen: dat is niets meer dan hij daarna zelf ook mag. Een "
        "platformbeheerder die geen groepsrol heeft, mag dit niet. Behalve met de beheersessie en een geldige "
        "CSRF-header mag dit ook met een CLI-token uit `plak login` (`Authorization: Bearer plakcli_...`), met precies "
        "dezelfde rolcontrole; de CSRF-header vervalt dan, want een token gaat niet vanzelf mee zoals een cookie. Een "
        "CI-ID-token mag het niet. Per lid geldt een limiet van 20 nieuwe groepen en sites samen per 60 minuten, via "
        "beheer en CLI samen."
    ),
    "The created site.": "Het aangemaakte site.",
    (
        "Forbidden. The request comes from an origin other than the admin host, or the member is not (or no longer) "
        "active (`ORIGIN_REFUSED`, `MEMBER_NOT_ACTIVE`). The `X-CSRF-Token` header is missing or does not match the "
        "CSRF cookie (`CSRF_INVALID`). With a CLI token: the member is not (or no longer) active "
        "(`MEMBER_NOT_ACTIVE`). The member's role in this group is insufficient for this action (`INSUFFICIENT_ROLE`)."
    ): (
        "Geen toegang. Het verzoek komt van een andere origin dan de beheer-host, of het lid is niet (meer) actief "
        "(`ORIGIN_REFUSED`, `MEMBER_NOT_ACTIVE`). De header `X-CSRF-Token` ontbreekt of komt niet overeen met het "
        "CSRF-cookie (`CSRF_INVALID`). Met een CLI-token: het lid is niet (meer) actief (`MEMBER_NOT_ACTIVE`). Je rol "
        "in deze groep is te smal voor deze handeling (`INSUFFICIENT_ROLE`)."
    ),
    "Conflict. A site with this slug already exists in this group (`SLUG_EXISTS`).": (
        "Conflict. Er bestaat al een site met deze slug in deze groep (`SLUG_EXISTS`)."
    ),
    "Unprocessable input. The slug is invalid (`SLUG_INVALID`), or the title is empty (`FIELD_EMPTY`).": (
        "Onverwerkbare invoer. De slug is ongeldig (`SLUG_INVALID`), of de titel is leeg (`FIELD_EMPTY`)."
    ),
    "Delete a site": "Site verwijderen",
    (
        "Deletes the site with everything attached to it: versions, previews, invitees, secret links, the linked "
        "repository and the unpacked files on disk. Irreversible; the URL returns 404 afterwards.\n"
        "\n"
        "**Who can call this:** effective site role `admin`, with a valid CSRF header."
    ): (
        "Verwijdert de site met alles eraan: versies, previews, genodigden, geheime links, de gekoppelde repository en "
        "de uitgepakte bestanden op schijf. Onomkeerbaar; de URL geeft daarna 404.\n"
        "\n"
        "**Mag:** effectieve siterol `admin`, met een geldige CSRF-header."
    ),
    "The site and all its content have been deleted. No content is returned.": (
        "De site en alle content zijn verwijderd. Er komt geen inhoud terug."
    ),
    (
        "Forbidden. The request comes from an origin other than the admin host, or the member is not (or no longer) "
        "active (`ORIGIN_REFUSED`, `MEMBER_NOT_ACTIVE`). The `X-CSRF-Token` header is missing or does not match the "
        "CSRF cookie (`CSRF_INVALID`). The member's role on this site is insufficient for this action "
        "(`INSUFFICIENT_ROLE`)."
    ): (
        "Geen toegang. Het verzoek komt van een andere origin dan de beheer-host, of het lid is niet (meer) actief "
        "(`ORIGIN_REFUSED`, `MEMBER_NOT_ACTIVE`). De header `X-CSRF-Token` ontbreekt of komt niet overeen met het "
        "CSRF-cookie (`CSRF_INVALID`). Je rol op deze site is te smal voor deze handeling (`INSUFFICIENT_ROLE`)."
    ),
    "Not found. Unknown group (`UNKNOWN_GROUP`) or unknown site (`UNKNOWN_SITE`).": (
        "Niet gevonden. Onbekende groep (`UNKNOWN_GROUP`) of onbekende site (`UNKNOWN_SITE`)."
    ),
    "Set the access to a site": "Toegang tot een site zetten",
    (
        "Determines who may see the live content of this site: the base and the two exceptions in one go, because they "
        "belong together and a visitor gets in as soon as one of the three lets them in. The change applies "
        "immediately to every subsequent request for the content. Previews with their own `accessOverride` do not "
        "follow this value.\n"
        "\n"
        "**Who can call this:** effective site role `admin`, with a valid CSRF header."
    ): (
        "Bepaalt wie de live content van deze site mag zien: de basis en de twee uitzonderingen in één keer, want ze "
        "horen bij elkaar en een bezoeker komt binnen zodra een van de drie hem binnenlaat. De wijziging geldt "
        "onmiddellijk voor elke volgende aanvraag van de content. Previews met een eigen `accessOverride` volgen deze "
        "waarde niet.\n"
        "\n"
        "**Mag:** effectieve siterol `admin`, met een geldige CSRF-header."
    ),
    "The site with its new access.": "De site met zijn nieuwe toegang.",
    "Allow or block external sources": "Externe bronnen toestaan of blokkeren",
    (
        "Determines whether the content of this site may load scripts and styles from cdnjs, jsDelivr and unpkg, and "
        "fonts from Google Fonts. On by default; turning it off is an extra restriction and the safer choice for a "
        "confidential page. The change applies immediately to every subsequent request for the content, for the live "
        "site, previews and version views. Blocked in both modes: fetching data from or sending data to other hosts, "
        "images from elsewhere, an iframe, and a form that posts elsewhere.\n"
        "\n"
        "**Who can call this:** effective site role `admin`, with a valid CSRF header."
    ): (
        "Bepaalt of de content van deze site scripts en stijlen mag laden van cdnjs, jsDelivr en unpkg, en lettertypen "
        "van Google Fonts. Staat standaard aan; uitzetten is een extra beperking en de veiligere keuze voor een "
        "vertrouwelijke pagina. De wijziging geldt onmiddellijk voor elke volgende aanvraag van de content, voor de "
        "live site, previews en versieweergaven. In beide standen geblokkeerd: gegevens ophalen bij of sturen naar "
        "andere hosts, afbeeldingen van elders, een iframe, en een formulier dat elders post.\n"
        "\n"
        "**Mag:** effectieve siterol `admin`, met een geldige CSRF-header."
    ),
    "The site with its new setting.": "De site met zijn nieuwe instelling.",
    "Turn isolation from other sites on or off": "Afscherming van andere sites aan- of uitzetten",
    (
        "All sites share one hostname. When this isolation is on, the default, the content is served with a CSP "
        "sandbox without `allow-same-origin`: the page gets an opaque origin and cannot read any other site on that "
        "hostname, receives no cookies and cannot store anything in the browser. Its own styles, scripts, images and "
        "fonts load as usual. Turning it off is needed for a site that uses `localStorage`, `sessionStorage` or a "
        "cookie, and puts that site back on the origin it shares with all other sites. The change applies immediately "
        "to every subsequent request for the content, for the live site, previews and version views.\n"
        "\n"
        "**Who can call this:** effective site role `admin`, with a valid CSRF header."
    ): (
        "Alle sites delen een hostnaam. Staat deze afscherming aan, de standaard, dan wordt de content geserveerd met "
        "een CSP-sandbox zonder `allow-same-origin`: de pagina krijgt een opaque origin en kan geen enkele andere site "
        "op die hostnaam lezen, krijgt geen cookies mee en kan niets in de browser bewaren. Eigen stijlen, scripts, "
        "afbeeldingen en lettertypen laden gewoon. Uitzetten is nodig voor een site die `localStorage`, "
        "`sessionStorage` of een cookie gebruikt, en zet die site terug op de herkomst die hij met alle andere sites "
        "deelt. De wijziging geldt onmiddellijk voor elke volgende aanvraag van de content, voor de live site, "
        "previews en versieweergaven.\n"
        "\n"
        "**Mag:** effectieve siterol `admin`, met een geldige CSRF-header."
    ),
    "Set the number of previous versions kept": "Aantal bewaarde vorige versies zetten",
    (
        "Determines how many previous live versions the nightly cleanup of this site leaves in place, in addition to "
        "the current live version. Older live versions are removed, row and files, and cannot be rolled back to "
        "afterwards. `0` keeps all live versions, `null` puts the site back on the platform default. The change takes "
        "effect at the next nightly cleanup.\n"
        "\n"
        "**Who can call this:** effective site role `admin`, with a valid CSRF header."
    ): (
        "Bepaalt hoeveel vorige live-versies de nachtelijke opschoning van deze site laat staan, naast de huidige "
        "live-versie. Oudere live-versies gaan weg, rij en bestanden, en daar kan daarna niet meer naar teruggerold "
        "worden. `0` bewaart alle live-versies, `null` zet de site terug op de standaard van het platform. De "
        "wijziging geldt vanaf de volgende nachtelijke opschoning.\n"
        "\n"
        "**Mag:** effectieve siterol `admin`, met een geldige CSRF-header."
    ),
    (
        "Unprocessable input. Not an integer of 0 or more (`LIVE_VERSIONS_KEPT_INVALID`), or a number that is too "
        "large to store (`LIVE_VERSIONS_KEPT_TOO_LARGE`)."
    ): (
        "Onverwerkbare invoer. Geen geheel getal van 0 of meer (`LIVE_VERSIONS_KEPT_INVALID`), of een getal dat te "
        "groot is om op te slaan (`LIVE_VERSIONS_KEPT_TOO_LARGE`)."
    ),
    "Invitees of a site": "Genodigden van een site",
    (
        "The addresses that may see this site while the `invitees` exception is on. While it is off, the list is kept "
        "but has no effect.\n"
        "\n"
        "**Who can call this:** effective site role `editor` or higher. This list holds e-mail addresses of external "
        "people and therefore sits above `reader`."
    ): (
        "De adressen die deze site mogen zien zolang de uitzondering `invitees` aan staat. Staat die uit, dan blijft "
        "de lijst bestaan maar heeft hij geen effect.\n"
        "\n"
        "**Mag:** effectieve siterol `editor` of ruimer. Deze lijst draagt e-mailadressen van externen en ligt daarom "
        "hoger dan `reader`."
    ),
    "The invitees, sorted by identifier.": "De genodigden, op identifier gesorteerd.",
    (
        "Forbidden. The request comes from an origin other than the admin host, or the member is not (or no longer) "
        "active (`ORIGIN_REFUSED`, `MEMBER_NOT_ACTIVE`). The member's role on this site is insufficient for this "
        "action (`INSUFFICIENT_ROLE`)."
    ): (
        "Geen toegang. Het verzoek komt van een andere origin dan de beheer-host, of het lid is niet (meer) actief "
        "(`ORIGIN_REFUSED`, `MEMBER_NOT_ACTIVE`). Je rol op deze site is te smal voor deze handeling "
        "(`INSUFFICIENT_ROLE`)."
    ),
    "Add an invitee": "Genodigde toevoegen",
    (
        "Puts an address on the invitee list. The identifier is normalised to lowercase; the invitee does not need to "
        "have an account yet, but must be able to sign in through SSO.\n"
        "\n"
        "**Who can call this:** effective site role `admin`, with a valid CSRF header."
    ): (
        "Zet een adres op de genodigdenlijst. De identifier wordt naar kleine letters genormaliseerd; de genodigde "
        "hoeft nog geen account te hebben, maar moet wel via SSO kunnen inloggen.\n"
        "\n"
        "**Mag:** effectieve siterol `admin`, met een geldige CSRF-header."
    ),
    "The added invitee.": "De toegevoegde genodigde.",
    "Conflict. This address is already on the invitee list (`INVITEE_EXISTS`).": (
        "Conflict. Dit adres staat al op de genodigdenlijst (`INVITEE_EXISTS`)."
    ),
    "Unprocessable input. The identifier is empty (`FIELD_EMPTY`).": (
        "Onverwerkbare invoer. De identifier is leeg (`FIELD_EMPTY`)."
    ),
    "Remove an invitee": "Genodigde verwijderen",
    (
        "Removes an address from the invitee list. An ID that does not belong to this site returns 404. The path takes "
        "the invitee's `id` from the list, not the address itself: an address in a URL ends up in the log lines of "
        "every proxy in between.\n"
        "\n"
        "**Who can call this:** effective site role `editor` or higher, with a valid CSRF header."
    ): (
        "Haalt een adres van de genodigdenlijst. Een id dat niet bij deze site hoort, levert 404. In het pad staat het "
        "`id` uit de genodigdenlijst, niet het adres zelf: een adres in een URL belandt in de logregels van elke proxy "
        "ertussen.\n"
        "\n"
        "**Mag:** effectieve siterol `editor` of ruimer, met een geldige CSRF-header."
    ),
    "ID of the invitee, from the invitee list of the site.": "Id van de genodigde, uit de genodigdenlijst van de site.",
    "The invitee has been removed from the list. No content is returned.": (
        "De genodigde is van de lijst. Er komt geen inhoud terug."
    ),
    (
        "Not found. Unknown group (`UNKNOWN_GROUP`) or unknown site (`UNKNOWN_SITE`). This invitee is not on the list "
        "of this site (`UNKNOWN_INVITEE`)."
    ): (
        "Niet gevonden. Onbekende groep (`UNKNOWN_GROUP`) of onbekende site (`UNKNOWN_SITE`). Deze genodigde staat "
        "niet op de lijst van deze site (`UNKNOWN_INVITEE`)."
    ),
    "Secret links of a site": "Geheime links van een site",
    (
        "The secret links of this site, newest first, including the revoked links. The secret value is not included: "
        "it can only be seen when the link is created.\n"
        "\n"
        "**Who can call this:** effective site role `editor` or higher."
    ): (
        "De geheime links van deze site, nieuwste eerst, inclusief de ingetrokken links. De geheime waarde staat er "
        "niet bij: die is alleen bij het aanmaken te zien.\n"
        "\n"
        "**Mag:** effectieve siterol `editor` of ruimer."
    ),
    "The secret links, newest first.": "De geheime links, nieuwste eerst.",
    "Create a secret link": "Geheime link aanmaken",
    (
        "Creates a secret link with which the site can be seen without signing in, while the `keys` exception is on.\n"
        "\n"
        "The response contains `value` once: the full key `<selector>.<secret>`. Plak only stores a hash, so if you "
        "lose the value, create a new key.\n"
        "\n"
        "**Who can call this:** effective site role `admin`, with a valid CSRF header."
    ): (
        "Maakt een geheime link waarmee de site zonder inloggen te zien is, zolang de uitzondering `keys` aan staat.\n"
        "\n"
        "Het antwoord bevat eenmalig `value`: de volledige sleutel `<selector>.<geheim>`. Plak bewaart alleen een "
        "hash, dus wie de waarde kwijt is, maakt een nieuwe sleutel aan.\n"
        "\n"
        "**Mag:** effectieve siterol `admin`, met een geldige CSRF-header."
    ),
    "The created key, with its full value shown once.": "De aangemaakte sleutel, met eenmalig haar volledige waarde.",
    (
        "Unprocessable input. `expiresAt` is not a valid time, lies in the past (`EXPIRY_IN_PAST`) or further ahead "
        "than allowed (`EXPIRY_TOO_FAR`)."
    ): (
        "Onverwerkbare invoer. `expiresAt` is geen geldig tijdstip, ligt in het verleden (`EXPIRY_IN_PAST`) of verder "
        "vooruit dan toegestaan (`EXPIRY_TOO_FAR`)."
    ),
    "Revoke a secret link": "Geheime link intrekken",
    (
        "Sets the key to `revoked`; the link no longer works afterwards. The key stays in the list, so there is a "
        "record that it existed. The path uses the `selector`, not the full key value.\n"
        "\n"
        "**Who can call this:** effective site role `editor` or higher, with a valid CSRF header."
    ): (
        "Zet de sleutel op `revoked`; de link werkt daarna niet meer. De sleutel blijft in de lijst staan, zodat "
        "zichtbaar blijft dat hij bestond. Het pad gebruikt de `selector`, niet de volledige sleutelwaarde.\n"
        "\n"
        "**Mag:** effectieve siterol `editor` of ruimer, met een geldige CSRF-header."
    ),
    "The non-secret first part of a key value, before the dot.": (
        "Het niet-geheime eerste deel van een sleutelwaarde, voor de punt."
    ),
    "The key has been revoked. No content is returned.": "De sleutel is ingetrokken. Er komt geen inhoud terug.",
    (
        "Not found. Unknown group (`UNKNOWN_GROUP`) or unknown site (`UNKNOWN_SITE`). This site has no key with this "
        "selector (`UNKNOWN_KEY`)."
    ): (
        "Niet gevonden. Onbekende groep (`UNKNOWN_GROUP`) of onbekende site (`UNKNOWN_SITE`). Deze site heeft geen "
        "sleutel met deze selector (`UNKNOWN_KEY`)."
    ),
    "Linked repository of a site": "Gekoppelde repository van een site",
    (
        "The repository from which GitHub or Forgejo workflows with an OIDC ID token may publish to this site, without "
        "a secret. A 404 `REPOSITORY_NOT_SET` means nothing has been linked yet.\n"
        "\n"
        "**Who can call this:** effective site role `editor` or higher: whoever publishes must be able to set up the "
        "workflow."
    ): (
        "De repository waarvan GitHub- of Forgejo-workflows met een OIDC-ID-token naar deze site mogen publiceren, "
        "zonder geheim. Een 404 `REPOSITORY_NOT_SET` betekent dat er nog niets gekoppeld is.\n"
        "\n"
        "**Mag:** effectieve siterol `editor` of ruimer: wie publiceert, moet de workflow kunnen inrichten."
    ),
    "The repository from which CI may publish to this site.": "De repository waaruit CI naar deze site mag publiceren.",
    (
        "Not found. Unknown group (`UNKNOWN_GROUP`) or unknown site (`UNKNOWN_SITE`). No repository is linked to this "
        "site (`REPOSITORY_NOT_SET`)."
    ): (
        "Niet gevonden. Onbekende groep (`UNKNOWN_GROUP`) of onbekende site (`UNKNOWN_SITE`). Aan deze site is geen "
        "repository gekoppeld (`REPOSITORY_NOT_SET`)."
    ),
    "Link a repository to a site": "Repository aan een site koppelen",
    (
        "Links a GitHub or Forgejo repository to this site, or replaces the link. Plak looks the repository up at the "
        "provider (`GET /repos/{owner}/{repo}`) and stores its numeric IDs: these stay the same when the repository is "
        "renamed, and a new repository under the same name does not get them. A CI ID token from this repository may "
        "publish afterwards: a preview (and its cleanup) from any branch, live only from `push`, `workflow_dispatch` "
        "or `schedule` and, if one is set, only from `liveBranch`.\n"
        "\n"
        "Plak does the lookup without credentials, so it cannot see a private repository. In that case pass "
        "`repositoryId` and `ownerId` yourself (`gh api repos/{owner}/{repo} --jq '.id, .owner.id'`): If the lookup "
        "fails, Plak stores them as given. A wrong ID does not link anything else, it only causes every deploy to be "
        "refused. If Plak does find the repository, the IDs must match.\n"
        "\n"
        "Also allowed with the CLI token from `plak login` (`plak site link`), then without a CSRF header. A CI ID "
        "token links nothing.\n"
        "\n"
        "**Who can call this:** effective site role `admin`, with a valid CSRF header or the CLI token."
    ): (
        "Koppelt een GitHub- of Forgejo-repository aan deze site, of vervangt de koppeling. Plak zoekt de repository "
        "op bij de provider (`GET /repos/{owner}/{repo}`) en bewaart haar numerieke ids: die blijven gelijk bij een "
        "hernoeming, en een nieuwe repository onder dezelfde naam krijgt ze niet. Een CI-ID-token uit deze repository "
        "mag daarna publiceren: een preview (en het opruimen ervan) vanaf elke branch, live alleen vanuit `push`, "
        "`workflow_dispatch` of `schedule` en, als die is ingesteld, alleen vanaf `liveBranch`.\n"
        "\n"
        "Plak zoekt zonder inloggegevens, dus een privé repository vindt het niet. Geef dan zelf `repositoryId` en "
        "`ownerId` mee (`gh api repos/{owner}/{repo} --jq '.id, .owner.id'`): Plak bewaart ze zonder opzoeking als die "
        "faalt. Een verkeerd id koppelt niets anders, het weigert alleen elke deploy. Vindt Plak de repository wel, "
        "dan moeten de ids kloppen.\n"
        "\n"
        "Ook met het CLI-token uit `plak login` (`plak site link`), dan zonder CSRF-header. Een CI-ID-token koppelt "
        "niets.\n"
        "\n"
        "**Mag:** effectieve siterol `admin`, met een geldige CSRF-header of het CLI-token."
    ),
    "The linked repository, with the IDs the provider returned.": (
        "De gekoppelde repository, met de ids die de provider teruggaf."
    ),
    (
        "Forbidden. The request comes from an origin other than the admin host, or the member is not (or no longer) "
        "active (`ORIGIN_REFUSED`, `MEMBER_NOT_ACTIVE`). The `X-CSRF-Token` header is missing or does not match the "
        "CSRF cookie (`CSRF_INVALID`). With a CLI token: the member is not (or no longer) active "
        "(`MEMBER_NOT_ACTIVE`). The member's role on this site is insufficient for this action (`INSUFFICIENT_ROLE`)."
    ): (
        "Geen toegang. Het verzoek komt van een andere origin dan de beheer-host, of het lid is niet (meer) actief "
        "(`ORIGIN_REFUSED`, `MEMBER_NOT_ACTIVE`). De header `X-CSRF-Token` ontbreekt of komt niet overeen met het "
        "CSRF-cookie (`CSRF_INVALID`). Met een CLI-token: het lid is niet (meer) actief (`MEMBER_NOT_ACTIVE`). Je rol "
        "op deze site is te smal voor deze handeling (`INSUFFICIENT_ROLE`)."
    ),
    (
        "Unprocessable input. Owner or repository is not a valid name (`REPOSITORY_INVALID`), the host does not belong "
        "to the provider or is not among the allowed Forgejo instances (`HOST_NOT_ALLOWED`), the live branch is not a "
        "valid branch name (`LIVE_BRANCH_INVALID`), the provider does not know the repository, or it is not public, "
        "and no IDs were passed (`REPOSITORY_NOT_FOUND`), the IDs are not both a positive integer "
        "(`REPOSITORY_IDS_INVALID`), or the provider gives the repository different IDs (`REPOSITORY_IDS_MISMATCH`)."
    ): (
        "Onverwerkbare invoer. Eigenaar of repository is geen geldige naam (`REPOSITORY_INVALID`), de host hoort niet "
        "bij de provider of staat niet in de toegestane Forgejo-instanties (`HOST_NOT_ALLOWED`), de live-branch is "
        "geen geldige branchnaam (`LIVE_BRANCH_INVALID`), de provider kent de repository niet, of niet openbaar, en er "
        "zijn geen ids meegegeven (`REPOSITORY_NOT_FOUND`), de ids zijn niet allebei een positief geheel getal "
        "(`REPOSITORY_IDS_INVALID`), of de provider geeft de repository andere ids (`REPOSITORY_IDS_MISMATCH`)."
    ),
    (
        "Unavailable. The provider is unreachable (`CI_PROVIDER_UNREACHABLE`) or its limit for anonymous requests is "
        "used up (`CI_PROVIDER_RATE_LIMITED`), and no IDs were passed; try again later."
    ): (
        "Niet beschikbaar. De provider is niet bereikbaar (`CI_PROVIDER_UNREACHABLE`) of zijn limiet voor anonieme "
        "verzoeken is op (`CI_PROVIDER_RATE_LIMITED`), en er zijn geen ids meegegeven; probeer het later opnieuw."
    ),
    "Unlink a repository": "Repository ontkoppelen",
    (
        "Removes the link: CI ID tokens from that repository are refused afterwards. Versions that were published from "
        "CI earlier remain.\n"
        "\n"
        "**Who can call this:** effective site role `admin`, with a valid CSRF header."
    ): (
        "Haalt de koppeling weg: CI-ID-tokens uit die repository worden daarna geweigerd. Versies die eerder vanuit CI "
        "zijn gepubliceerd blijven staan.\n"
        "\n"
        "**Mag:** effectieve siterol `admin`, met een geldige CSRF-header."
    ),
    "The link has been removed. No content is returned.": "De koppeling is weg. Er komt geen inhoud terug.",
    "Require the site id of a workflow": "Het site-ID van een workflow verplicht maken",
    (
        "For a link made before the site id existed: from now on the linked repository may publish to this site only "
        "with a CI ID token whose audience names the site id, `{PLAK_BASE_URL}/-/sites/{siteId}`. A workflow does that "
        "with `site-id` (action) or `--site-id` (CLI); a workflow without it is refused with 403 "
        "`CI_SITE_ID_REQUIRED`. A link made since then, or that got another repository since, requires it already.\n"
        "\n"
        "This goes one way: `true` sets it, `false` on a link that requires the site id is refused, so no admin can "
        "quietly let a workflow without it in again. Asking for what already holds changes nothing and writes no audit "
        "row.\n"
        "\n"
        "**Who can call this:** effective site role `admin`, with a valid CSRF header."
    ): (
        "Voor een koppeling van vóór het site-ID: vanaf nu mag de gekoppelde repository alleen nog naar deze site "
        "publiceren met een CI-ID-token waarvan de audience het site-ID noemt, `{PLAK_BASE_URL}/-/sites/{siteId}`. "
        "Een workflow doet dat met `site-id` (action) of `--site-id` (CLI); een workflow zonder wordt geweigerd met "
        "403 `CI_SITE_ID_REQUIRED`. Een koppeling die sindsdien is gemaakt, of die sindsdien een andere repository "
        "kreeg, vraagt het al.\n"
        "\n"
        "Dit gaat één kant op: `true` zet het aan, `false` op een koppeling die het site-ID al vraagt wordt "
        "geweigerd, zodat geen beheerder een workflow zonder site-ID stilletjes weer toelaat. Vragen om wat al geldt "
        "verandert niets en schrijft geen auditregel.\n"
        "\n"
        "**Mag:** effectieve siterol `admin`, met een geldige CSRF-header."
    ),
    "The linked repository, with its new setting.": "De gekoppelde repository, met de nieuwe instelling.",
    (
        "Unprocessable input. `siteIdRequired` is `false` while the link already requires the site id "
        "(`SITE_ID_REQUIRED_PERMANENT`)."
    ): (
        "Onverwerkbare invoer. `siteIdRequired` is `false` terwijl de koppeling het site-ID al vraagt "
        "(`SITE_ID_REQUIRED_PERMANENT`)."
    ),
    "Look up a pending CLI login": "Openstaande CLI-login opzoeken",
    (
        "Looks up the pending CLI login behind a user code, so the admin interface can show which program is "
        "requesting access, when and from which network. A POST and not a query parameter, so the code does not end up "
        "in log lines.\n"
        "\n"
        "**Who can call this:** any active member, with an admin session no older than fifteen minutes and a valid "
        "CSRF header. Per member, looking up, approving and denying together count towards a limit of 20 per 10 "
        "minutes."
    ): (
        "Zoekt de openstaande CLI-login achter een gebruikerscode op, zodat het beheer kan laten zien welk programma "
        "wanneer en vanaf welk netwerk wil koppelen. Een POST en geen queryparameter, zodat de code niet in logregels "
        "belandt.\n"
        "\n"
        "**Mag:** elk actief lid, met een beheersessie van hoogstens een kwartier oud en een geldige CSRF-header. Per "
        "lid tellen opzoeken, goedkeuren en weigeren samen voor een limiet van 20 per 10 minuten."
    ),
    "What the approval screen shows.": "Wat het goedkeuringsscherm toont.",
    (
        "Not authenticated. There is no valid admin session: the cookie is missing, invalid or expired (`NO_SESSION`). "
        "The admin session is more than fifteen minutes old (`SESSION_NOT_FRESH`): sign in again and try again."
    ): (
        "Niet geauthenticeerd. Er is geen geldige beheersessie: het cookie ontbreekt, is ongeldig of is verlopen "
        "(`NO_SESSION`). De beheersessie is ouder dan een kwartier (`SESSION_NOT_FRESH`): log opnieuw in en probeer "
        "het nog eens."
    ),
    "Not found. The code is unknown, expired or already handled (`USER_CODE_UNKNOWN`).": (
        "Niet gevonden. De code is onbekend, verlopen of al afgehandeld (`USER_CODE_UNKNOWN`)."
    ),
    (
        "Too many requests. The rate limit budget for this session is used up; try again later. Too many attempts by "
        "this member in a short time (`TOO_MANY_ATTEMPTS`)."
    ): (
        "Te veel verzoeken. Het ratelimit-budget voor deze sessie is op; probeer het later opnieuw. Te veel pogingen "
        "door dit lid in korte tijd (`TOO_MANY_ATTEMPTS`)."
    ),
    "Approve a CLI login": "CLI-login goedkeuren",
    (
        "Approves the CLI login behind this user code for the signed-in member. The CLI then fetches its tokens itself "
        "and from now on acts as this member, with exactly their roles. Only do this if you just started `plak login` "
        "yourself.\n"
        "\n"
        "**Who can call this:** any active member, with an admin session no older than fifteen minutes and a valid "
        "CSRF header. Per member, looking up, approving and denying together count towards a limit of 20 per 10 "
        "minutes."
    ): (
        "Keurt de CLI-login achter deze gebruikerscode goed voor het ingelogde lid. De CLI haalt daarna zelf zijn "
        "tokens op en handelt voortaan als dit lid, met precies diens rollen. Alleen doen als je zelf zojuist `plak "
        "login` startte.\n"
        "\n"
        "**Mag:** elk actief lid, met een beheersessie van hoogstens een kwartier oud en een geldige CSRF-header. Per "
        "lid tellen opzoeken, goedkeuren en weigeren samen voor een limiet van 20 per 10 minuten."
    ),
    "Approved. No content is returned.": "Goedgekeurd. Er komt geen inhoud terug.",
    "Deny a CLI login": "CLI-login weigeren",
    (
        "Denies the CLI login behind this user code; the CLI gets `ACCESS_DENIED`.\n"
        "\n"
        "**Who can call this:** any active member, with an admin session no older than fifteen minutes and a valid "
        "CSRF header. Per member, looking up, approving and denying together count towards a limit of 20 per 10 "
        "minutes."
    ): (
        "Weigert de CLI-login achter deze gebruikerscode; de CLI krijgt `ACCESS_DENIED`.\n"
        "\n"
        "**Mag:** elk actief lid, met een beheersessie van hoogstens een kwartier oud en een geldige CSRF-header. Per "
        "lid tellen opzoeken, goedkeuren en weigeren samen voor een limiet van 20 per 10 minuten."
    ),
    "Denied. No content is returned.": "Geweigerd. Er komt geen inhoud terug.",
    "My linked CLI sessions": "Mijn gekoppelde sessies",
    (
        "Every `plak login` that is still active, newest first. Expired sessions are no longer listed.\n"
        "\n"
        "**Who can call this:** any active member, for their own sessions."
    ): (
        "Elke `plak login` die nog loopt, nieuwste eerst. Verlopen sessies staan er niet meer bij.\n"
        "\n"
        "**Mag:** elk actief lid, voor zijn eigen sessies."
    ),
    "The CLI sessions of the signed-in member, newest first.": "De CLI-sessies van het ingelogde lid, nieuwste eerst.",
    "Revoke a linked CLI session": "Gekoppelde sessie intrekken",
    (
        "Revokes a CLI session; whoever used it has to run `plak login` again afterwards.\n"
        "\n"
        "**Who can call this:** any active member, for their own sessions, with a valid CSRF header."
    ): (
        "Trekt een CLI-sessie in; wie hem gebruikte moet daarna opnieuw `plak login` doen.\n"
        "\n"
        "**Mag:** elk actief lid, voor zijn eigen sessies, met een geldige CSRF-header."
    ),
    "ID of the CLI session, from `GET /me/cli-sessions`.": "Id van de CLI-sessie, uit `GET /me/cli-sessions`.",
    "The CLI session has been revoked. No content is returned.": (
        "De CLI-sessie is ingetrokken. Er komt geen inhoud terug."
    ),
    "Not found. You have no CLI session with this ID (`CLI_SESSION_UNKNOWN`).": (
        "Niet gevonden. Je hebt geen CLI-sessie met dit id (`CLI_SESSION_UNKNOWN`)."
    ),
    "Members of a group": "Leden van een groep",
    (
        "Who may manage the sites of this group, sorted by e-mail address, each with their role.\n"
        "\n"
        "**Who can call this:** group role `reader` or higher, or a platform administrator."
    ): (
        "Wie de sites van deze groep mag beheren, op e-mailadres gesorteerd, elk met zijn rol.\n"
        "\n"
        "**Mag:** groepsrol `reader` of ruimer, of een platformbeheerder."
    ),
    "The members of the group, sorted by e-mail address.": "De leden van de groep, op e-mailadres gesorteerd.",
    "Add a member to a group": "Lid aan een groep toevoegen",
    (
        "Adds an existing platform member to this group, looked up by e-mail address or SSO subject. The member must "
        "already have signed in to the admin interface once themselves; Plak does not create an account here.\n"
        "\n"
        "Without `role` the member becomes `reader`: new members start with read-only access, and you give the role "
        "they need deliberately.\n"
        "\n"
        "**Who can call this:** group role `admin`, or a platform administrator, with a valid CSRF header."
    ): (
        "Voegt een bestaand platformlid aan deze groep toe, gezocht op e-mailadres of SSO-subject. Het lid moet al "
        "eens zelf op het beheer ingelogd hebben; Plak maakt hier geen account aan.\n"
        "\n"
        "Zonder `role` wordt het lid `reader`: wie erbij komt kijkt eerst mee, en de rol die hij nodig heeft geef je "
        "bewust.\n"
        "\n"
        "**Mag:** groepsrol `admin`, of een platformbeheerder, met een geldige CSRF-header."
    ),
    "The added group member.": "Het toegevoegde groepslid.",
    (
        "Not found. Unknown group (`UNKNOWN_GROUP`). There is no member with this identifier; they must sign in "
        "themselves first (`UNKNOWN_MEMBER`)."
    ): (
        "Niet gevonden. Onbekende groep (`UNKNOWN_GROUP`). Er is geen lid met deze identifier; diegene moet eerst zelf "
        "inloggen (`UNKNOWN_MEMBER`)."
    ),
    (
        "Conflict. The e-mail address or name belongs to more than one member (`IDENTIFIER_AMBIGUOUS`); if the name is "
        "ambiguous, use the e-mail address; if the e-mail address is ambiguous, use the SSO subject. This member is "
        "already in the group (`ALREADY_GROUP_MEMBER`)."
    ): (
        "Conflict. Het e-mailadres of de naam hoort bij meer dan één lid (`IDENTIFIER_AMBIGUOUS`); zoek bij een "
        "meerduidige naam op het e-mailadres, bij een meerduidig e-mailadres op het SSO-subject. Dit lid zit al in de "
        "groep (`ALREADY_GROUP_MEMBER`)."
    ),
    "Unprocessable input. The identifier is empty (`FIELD_EMPTY`), or `role` is not an existing role.": (
        "Onverwerkbare invoer. De identifier is leeg (`FIELD_EMPTY`), of `role` is geen bestaande rol."
    ),
    "Find someone to add to the group": "Iemand zoeken om aan de groep toe te voegen",
    (
        "Searches the platform members by name or e-mail address, so you can add someone without knowing their address "
        "by heart. The `identifier` of a hit is exactly what `POST /groups/{group_slug}/members` expects.\n"
        "\n"
        "Only active members are returned: you do not add someone who has been deactivated. The answer is a shortlist "
        "of at most ten names, not a dump of the whole organisation; someone who is already in the group is included, "
        "with `alreadyMember` set to `true`.\n"
        "\n"
        "Only those who may add members may search, and Plak does not create an account here either: someone only "
        "appears once they have signed in to the admin interface themselves.\n"
        "\n"
        "**Who can call this:** group role `admin`, or a platform administrator."
    ): (
        "Zoekt in de platformleden op naam of e-mailadres, zodat je iemand kunt toevoegen zonder zijn adres uit het "
        "hoofd te kennen. De `identifier` uit een treffer is precies wat `POST /groups/{group_slug}/members` "
        "verwacht.\n"
        "\n"
        "Alleen actieve leden komen terug: wie buitengesloten is, voeg je niet toe. Het antwoord is een shortlist van "
        "hoogstens tien namen, geen uitdraai van de hele organisatie; wie er al in de groep zit staat er wel bij, met "
        "`alreadyMember` op `true`.\n"
        "\n"
        "Zoeken mag precies wie ook mag toevoegen, en Plak maakt hier net zomin een account aan: iemand verschijnt pas "
        "zodra hij zelf op het beheer heeft ingelogd.\n"
        "\n"
        "**Mag:** groepsrol `admin`, of een platformbeheerder."
    ),
    (
        "Search term of at least two characters. Matches name and e-mail address, case-insensitively, anywhere in the "
        "text."
    ): (
        "Zoekterm van minstens twee tekens. Zoekt hoofdletterongevoelig op naam en op e-mailadres, ergens in de tekst."
    ),
    "At most ten active platform members, sorted by name and then by e-mail address.": (
        "Hoogstens tien actieve platformleden, op naam en daarbinnen op e-mailadres gesorteerd."
    ),
    "Unprocessable input. The search term is shorter than two characters (`SEARCH_TOO_SHORT`).": (
        "Onverwerkbare invoer. De zoekterm is korter dan twee tekens (`SEARCH_TOO_SHORT`)."
    ),
    "Remove a member from a group": "Lid uit een groep halen",
    (
        "Removes someone from the group. The member's platform account remains, as do any other group memberships. The "
        "path holds `memberId` from the member list, not the e-mail address: an address in a URL ends up in the log "
        "lines of every proxy in between.\n"
        "\n"
        "A direct site role on an individual site is independent of the group and by default stays in place. With "
        "`siteRoles=remove` you remove those roles in the same action, but only on sites in this group; whatever this "
        "member has elsewhere is left untouched. Everything happens in one transaction, so it is all gone or nothing "
        "changes. Each removed site role produces the same audit row as removing it from the site screen "
        "(`site_member_remove`).\n"
        "\n"
        "The last member of a group cannot be removed: a group without members can no longer be managed, because only "
        "someone who is in it may add someone. For the same reason the last `admin` cannot be removed.\n"
        "\n"
        "**Who can call this:** group role `admin`, or a platform administrator, with a valid CSRF header. Whoever may "
        "manage the group may already remove any site role in it at the site itself."
    ): (
        "Haalt iemand uit de groep. Het platformlid zelf blijft bestaan, net als zijn eventuele andere "
        "groepslidmaatschappen. In het pad staat `memberId` uit de ledenlijst, niet het e-mailadres: een adres in een "
        "URL belandt in de logregels van elke proxy ertussen.\n"
        "\n"
        "Een eigen rol op een losse site staat los van de groep en blijft standaard gelden. Met `siteRoles=remove` "
        "haal je die rollen in dezelfde handeling weg, maar alleen op sites in deze groep; wat dit lid elders heeft "
        "blijft onaangeroerd. Alles gebeurt in één transactie, dus het is allemaal weg of er verandert niets. Elke "
        "weggehaalde siterol levert dezelfde auditregel op als weghalen vanaf het sitescherm (`site_member_remove`).\n"
        "\n"
        "Het laatste lid van een groep kan er niet uit: een groep zonder leden is niet meer te beheren, want iemand "
        "toevoegen mag alleen wie er zelf in zit. Om dezelfde reden kan de laatste `admin` er niet uit.\n"
        "\n"
        "**Mag:** groepsrol `admin`, of een platformbeheerder, met een geldige CSRF-header. Wie de groep mag beheren, "
        "mag elke siterol erin al weghalen bij de site zelf."
    ),
    "ID of the platform member.": "Id van het platformlid.",
    (
        "What happens to the direct site roles that this member has on sites in this group. `keep` leaves them in "
        "place, so they keep access to those sites; `remove` removes them in the same action. Omitted means `keep`: "
        "removing more than was asked is a deliberate choice.\n"
        "\n"
        "Only sites in this group. A site role in another group stays out of view and untouched."
    ): (
        "Wat er gebeurt met de eigen siterollen die dit lid heeft op sites in deze groep. `keep` laat ze staan, zodat "
        "diegene bij die sites blijft kunnen; `remove` haalt ze in dezelfde handeling weg. Weggelaten betekent `keep`: "
        "meer weghalen dan gevraagd is een bewuste keuze.\n"
        "\n"
        "Alleen sites in deze groep. Een siterol in een andere groep blijft buiten beeld en buiten schot."
    ),
    "The member is no longer in the group. No content is returned.": (
        "Het lid zit niet meer in de groep. Er komt geen inhoud terug."
    ),
    (
        "Not found. Unknown group (`UNKNOWN_GROUP`). There is no member with this ID (`UNKNOWN_MEMBER`), or that "
        "member is not in this group (`NOT_GROUP_MEMBER`)."
    ): (
        "Niet gevonden. Onbekende groep (`UNKNOWN_GROUP`). Er is geen lid met dit id (`UNKNOWN_MEMBER`), of dat lid "
        "zit niet in deze groep (`NOT_GROUP_MEMBER`)."
    ),
    "Conflict. This is the last member of the group (`LAST_GROUP_MEMBER`), or its last admin (`LAST_GROUP_ADMIN`).": (
        "Conflict. Dit is het laatste lid van de groep (`LAST_GROUP_MEMBER`), of de laatste beheerder ervan "
        "(`LAST_GROUP_ADMIN`)."
    ),
    "Change the group role of a member": "Groepsrol van een lid wijzigen",
    (
        "Gives a member of this group a different role. The role determines what they may do in the whole group: "
        "`reader` (views), `editor` (publishes) or `admin` (determines access, invitees, secret links and who is in "
        "the group). A higher role can do everything a lower role can.\n"
        "\n"
        "This never changes a site role: you set that per site, and it only widens what someone may do on that one "
        "site.\n"
        "\n"
        "The last `admin` of a group cannot be demoted; a group without an admin can no longer be managed. Demoting "
        "yourself is allowed: as long as there is another admin, they can restore you.\n"
        "\n"
        "The path holds `memberId` from the member list, not the e-mail address: an address in a URL ends up in the "
        "log lines of every proxy in between.\n"
        "\n"
        "**Who can call this:** group role `admin`, or a platform administrator, with a valid CSRF header."
    ): (
        "Geeft een lid van deze groep een andere rol. De rol bepaalt wat diegene in de hele groep mag: `reader` (leest "
        "mee), `editor` (publiceert) of `admin` (bepaalt toegang, genodigden, geheime links en wie er in de groep "
        "zit). Een ruimere rol kan alles wat een smallere rol kan.\n"
        "\n"
        "Een siterol komt hier nooit uit: die zet je per site, en hij verbreedt alleen wat iemand op die ene site "
        "mag.\n"
        "\n"
        "De laatste `admin` van een groep kan niet gedegradeerd worden; een groep zonder beheerder is niet meer te "
        "beheren. Jezelf degraderen kan wel: zolang er een andere beheerder is, kan die je terugzetten.\n"
        "\n"
        "In het pad staat `memberId` uit de ledenlijst, niet het e-mailadres: een adres in een URL belandt in de "
        "logregels van elke proxy ertussen.\n"
        "\n"
        "**Mag:** groepsrol `admin`, of een platformbeheerder, met een geldige CSRF-header."
    ),
    "The group member with their new role.": "Het groepslid met zijn nieuwe rol.",
    "Conflict. This is the last admin of the group (`LAST_GROUP_ADMIN`).": (
        "Conflict. Dit is de laatste beheerder van de groep (`LAST_GROUP_ADMIN`)."
    ),
    "All platform members": "Alle platformleden",
    (
        "Everyone who has ever signed in to the admin interface, sorted by e-mail address, with their role and status. "
        "This is also where you see who has been denied access.\n"
        "\n"
        "**Who can call this:** only a platform administrator."
    ): (
        "Iedereen die ooit op het beheer heeft ingelogd, op e-mailadres gesorteerd, met hun rol en status. Hier zie je "
        "ook wie de toegang is ontzegd.\n"
        "\n"
        "**Mag:** alleen een platformbeheerder."
    ),
    "All platform members, sorted by e-mail address.": "Alle platformleden, op e-mailadres gesorteerd.",
    (
        "Forbidden. The request comes from an origin other than the admin host, or the member is not (or no longer) "
        "active (`ORIGIN_REFUSED`, `MEMBER_NOT_ACTIVE`). Only a platform administrator is allowed to do this "
        "(`NOT_ADMIN`)."
    ): (
        "Geen toegang. Het verzoek komt van een andere origin dan de beheer-host, of het lid is niet (meer) actief "
        "(`ORIGIN_REFUSED`, `MEMBER_NOT_ACTIVE`). Alleen een platformbeheerder mag dit (`NOT_ADMIN`)."
    ),
    "Disk usage of the content volume": "Vulling van het contentvolume",
    (
        "The whole content volume at a glance: size, used, free and the reserve below which a deploy is refused. It "
        "lists no site or group: the platform administrator manages people and groups and does not look into the "
        "sites.\n"
        "\n"
        "**Who can call this:** only a platform administrator."
    ): (
        "Het hele contentvolume in één blik: grootte, gebruikt, vrij en de reserve waaronder een deploy wordt "
        "geweigerd. Er staat geen site of groep in: de platformbeheerder beheert mensen en groepen en kijkt niet in de "
        "sites.\n"
        "\n"
        "**Mag:** alleen een platformbeheerder."
    ),
    "Total, used and free space on the content volume, with the reserve.": (
        "Totaal, gebruikt en vrij op het contentvolume, met de reserve."
    ),
    (
        "Unavailable. The volume cannot be measured, for example because the content root is missing "
        "(`VOLUME_UNMEASURABLE`)."
    ): (
        "Niet beschikbaar. Het volume is niet te meten, bijvoorbeeld omdat de contentroot ontbreekt "
        "(`VOLUME_UNMEASURABLE`)."
    ),
    "Reactivate a platform member": "Platformlid heractiveren",
    (
        "Sets the status of a member to `active`, so they may use the admin API again. For a member whose access has "
        "been revoked (status `deactivated`).\n"
        "\n"
        "**Who can call this:** only a platform administrator, with a valid CSRF header."
    ): (
        "Zet de status van een lid op `active`, waarmee het de beheer-API weer mag gebruiken. Voor een lid van wie de "
        "toegang is ingetrokken (status `deactivated`).\n"
        "\n"
        "**Mag:** alleen een platformbeheerder, met een geldige CSRF-header."
    ),
    "The member with their new status.": "Het lid met zijn nieuwe status.",
    (
        "Forbidden. The request comes from an origin other than the admin host, or the member is not (or no longer) "
        "active (`ORIGIN_REFUSED`, `MEMBER_NOT_ACTIVE`). The `X-CSRF-Token` header is missing or does not match the "
        "CSRF cookie (`CSRF_INVALID`). Only a platform administrator is allowed to do this (`NOT_ADMIN`)."
    ): (
        "Geen toegang. Het verzoek komt van een andere origin dan de beheer-host, of het lid is niet (meer) actief "
        "(`ORIGIN_REFUSED`, `MEMBER_NOT_ACTIVE`). De header `X-CSRF-Token` ontbreekt of komt niet overeen met het "
        "CSRF-cookie (`CSRF_INVALID`). Alleen een platformbeheerder mag dit (`NOT_ADMIN`)."
    ),
    "Not found. Unknown member (`UNKNOWN_MEMBER`).": "Niet gevonden. Onbekend lid (`UNKNOWN_MEMBER`).",
    "Deactivate a platform member": "Platformlid deactiveren",
    (
        "Sets the status of a member to `deactivated`. Every subsequent API request from that member gets 403, even "
        "with a session that is still valid. Group memberships remain, so activating brings the member back as they "
        "were. All CLI sessions (`plak login`) of the member are revoked, though: after reactivation they have to run "
        "`plak login` again.\n"
        "\n"
        "**Who can call this:** only a platform administrator, with a valid CSRF header."
    ): (
        "Zet de status van een lid op `deactivated`. Elk volgend API-verzoek van dat lid krijgt 403, ook met een "
        "sessie die nog geldig is. Groepslidmaatschappen blijven staan, zodat activeren het lid terugbrengt zoals het "
        "was. Alle CLI-sessies (`plak login`) van het lid worden wel ingetrokken: na heractiveren moet het opnieuw "
        "koppelen.\n"
        "\n"
        "**Mag:** alleen een platformbeheerder, met een geldige CSRF-header."
    ),
    "Change the platform role of a member": "Platformrol van een lid wijzigen",
    (
        "Makes a member platform administrator or removes that role. A platform administrator manages people and "
        "groups: activating and deactivating members, appointing administrators, and reading the member list. They do "
        "not manage sites; for that they give themselves a group role.\n"
        "\n"
        "Three things are not possible, all because they would make the platform unmanageable: taking away your own "
        "role, demoting the last active administrator, and changing the bootstrap account.\n"
        "\n"
        "**Who can call this:** only a platform administrator, with a valid CSRF header."
    ): (
        "Maakt een lid platformbeheerder of haalt die rol er weer af. Een platformbeheerder beheert mensen en groepen: "
        "leden activeren en deactiveren, beheerders aanwijzen, en de ledenlijst lezen. Hij beheert geen sites; "
        "daarvoor kent hij zichzelf een groepsrol toe.\n"
        "\n"
        "Drie dingen kunnen niet, allemaal omdat ze het platform onbeheerbaar zouden maken: je eigen rol afnemen, de "
        "laatste actieve beheerder degraderen, en het bootstrap-account wijzigen.\n"
        "\n"
        "**Mag:** alleen een platformbeheerder, met een geldige CSRF-header."
    ),
    "The member with their new platform role.": "Het lid met zijn nieuwe platformrol.",
    (
        "Conflict. Taking away your own role (`SELF_NOT_ALLOWED`), the last active administrator "
        "(`LAST_PLATFORM_ADMIN`), or the bootstrap account (`BOOTSTRAP_MEMBER`)."
    ): (
        "Conflict. Je eigen rol afnemen (`SELF_NOT_ALLOWED`), de laatste actieve beheerder (`LAST_PLATFORM_ADMIN`), of "
        "het bootstrap-account (`BOOTSTRAP_MEMBER`)."
    ),
    "Read the audit log": "Auditlog teruglezen",
    (
        "Reads the audit log, newest first: refusals, signing in and out, viewing non-public content, deploys and "
        "admin actions. Vocabulary and retention periods are in `docs/audit-log.md`.\n"
        "\n"
        "**Paging works with `cursor`, not with a page number.** The log grows while you read, and a shifting offset "
        "would skip rows or show them twice. Take `nextCursor` from the response unchanged; if it is `null`, this was "
        "the last page.\n"
        "\n"
        "**Actors appear pseudonymised** and are not translated back here. If you are looking for someone in "
        "particular, first fetch their pseudonym with `POST /platform/audit/actor-pseudonym` and filter on "
        "`actorPseudonym` with it.\n"
        "\n"
        "**Who can call this:** only a platform administrator. A group admin does not see even their own group: the "
        "group of a row is only a free-form key in `refs`, and no authorization boundary can be built on that. Reading "
        "the log is itself audited; if writing that audit row fails, no page is returned: see the `503`."
    ): (
        "Leest het auditlog terug, nieuwste eerst: weigeringen, inloggen en uitloggen, kijken op niet-publieke "
        "content, deploys en beheerhandelingen. Woordenschat en bewaartermijnen staan in `docs/audit-log.md`.\n"
        "\n"
        "**Bladeren gaat met `cursor`, niet met een paginanummer.** Het log groeit terwijl je leest, en een "
        "verschuivende offset zou regels overslaan of dubbel tonen. Neem `nextCursor` uit het antwoord ongewijzigd "
        "over; is die `null`, dan was dit de laatste pagina.\n"
        "\n"
        "**Actoren staan er gepseudonimiseerd in** en worden hier niet teruggevertaald. Zoek je iemand in het "
        "bijzonder, haal dan eerst zijn pseudoniem op met `POST /platform/audit/actor-pseudonym` en filter daarmee op "
        "`actorPseudonym`.\n"
        "\n"
        "**Mag:** alleen een platformbeheerder. Een groepsbeheerder ziet ook zijn eigen groep niet: de groep van een "
        "regel staat alleen als vrije sleutel in `refs`, en daar is geen autorisatiegrens op te bouwen. Het teruglezen "
        "zelf wordt geaudit; lukt die auditrij niet, dan komt er geen pagina: zie de `503`."
    ),
    "Number of rows per page, 1 to 200.": "Aantal regels per pagina, 1 tot 200.",
    "The `nextCursor` from the previous response, copied unchanged. Omit it for the first page.": (
        "De `nextCursor` uit het vorige antwoord, ongewijzigd overgenomen. Laat hem weg voor de eerste pagina."
    ),
    "Only rows from this time onwards (RFC 3339, inclusive). Without a time zone, UTC applies.": (
        "Alleen regels vanaf dit tijdstip (RFC 3339, inclusief). Zonder tijdzone geldt UTC."
    ),
    "Only rows from before this time (RFC 3339, exclusive). Without a time zone, UTC applies.": (
        "Alleen regels van vóór dit tijdstip (RFC 3339, exclusief). Zonder tijdzone geldt UTC."
    ),
    "Exact action, for example `content_access`, `deploy` or `site_create`.": (
        "Exacte handeling, bijvoorbeeld `content_access`, `deploy` of `site_create`."
    ),
    "Exact outcome: `allowed`, `refused` or `login_redirect`.": (
        "Exacte uitkomst: `allowed`, `refused` of `login_redirect`."
    ),
    "Exact reason code behind the outcome, for example `UNKNOWN_SITE`.": (
        "Exacte redencode achter de uitkomst, bijvoorbeeld `UNKNOWN_SITE`."
    ),
    "Slug of the group the row is about.": "Slug van de groep waar de regel over gaat.",
    "Slug of the site the row is about.": "Slug van de site waar de regel over gaat.",
    (
        "Pseudonym of one actor, 64 hexadecimal characters. Fetch it with `POST /platform/audit/actor-pseudonym`. An "
        "e-mail address does not belong here: a query parameter ends up in proxy logs."
    ): (
        "Pseudoniem van één actor, 64 hexadecimale tekens. Haal het op met `POST /platform/audit/actor-pseudonym`. Een "
        "e-mailadres hoort hier niet: een queryparameter belandt in proxylogs."
    ),
    "One page of audit rows, newest first, with the cursor to the next.": (
        "Eén pagina auditregels, nieuwste eerst, met de cursor naar de volgende."
    ),
    (
        "Unprocessable input. A filter is invalid: the cursor is unreadable (`CURSOR_INVALID`), `actorPseudonym` is "
        "not a hexadecimal HMAC value (`ACTOR_PSEUDONYM_INVALID`), or `limit` is outside 1 to 200."
    ): (
        "Onverwerkbare invoer. Een filter deugt niet: de cursor is onleesbaar (`CURSOR_INVALID`), `actorPseudonym` is "
        "geen hexadecimale HMAC-waarde (`ACTOR_PSEUDONYM_INVALID`), of `limit` valt buiten 1 tot 200."
    ),
    (
        "Unavailable. The action itself writes an audit row, and that write failed (`AUDIT_UNAVAILABLE`); nothing was "
        "returned. Try again later."
    ): (
        "Niet beschikbaar. De handeling zelf schrijft een auditrij, en die schrijfactie faalde (`AUDIT_UNAVAILABLE`); "
        "er is niets teruggegeven. Probeer het later opnieuw."
    ),
    "Look up the pseudonym of an actor": "Pseudoniem van een actor opzoeken",
    (
        "Translates an identifier into the pseudonym under which it appears in the audit log, so you can filter on "
        "`actorPseudonym`. So you must already know who you are looking for: the log does not give away a list of "
        "names. If you only know the pseudonym from a log row, use `POST /platform/audit/actor-identity` for the "
        "opposite direction.\n"
        "\n"
        "That this is a POST and not a query parameter is deliberate: an e-mail address in a URL ends up in the log "
        "lines of every proxy in between.\n"
        "\n"
        "The pseudonym depends on `PLAK_AUDIT_PEPPER`. After a rotation of that pepper you only find rows from after "
        "the rotation.\n"
        "\n"
        "An e-mail address that matches neither a member nor the verified address of a content viewer is not "
        "pseudonymised: that is a 404. An identifier that is not an e-mail address and belongs to nothing (for example "
        "a bare SSO subject) is pseudonymised as given, as `unknown`. If an e-mail address belongs to more than one "
        "subject (only possible via e-mail, never via an SSO subject), the response is a 409: in that case search by "
        "the SSO subject.\n"
        "\n"
        "**Who can call this:** only a platform administrator, with a valid CSRF header and a `reason` of 10 to 500 "
        "characters. The lookup itself is audited, with the pseudonym, `resolved_as` and the reason, never the "
        "identifier. If that audit row fails, no response is returned: see the `503`. Counts towards the daily limit "
        "on such lookups, even on a 404."
    ): (
        "Vertaalt een identifier naar het pseudoniem waaronder hij in het auditlog staat, zodat je op `actorPseudonym` "
        "kunt filteren. Wie je zoekt moet je dus al bij naam kennen: het log geeft geen namenlijst prijs. Ken je juist "
        "alleen het pseudoniem uit een logregel, gebruik dan `POST /platform/audit/actor-identity` voor de omgekeerde "
        "richting.\n"
        "\n"
        "Dat dit een POST is en geen queryparameter, is met opzet: een e-mailadres in een URL belandt in de logregels "
        "van elke proxy ertussen.\n"
        "\n"
        "Het pseudoniem hangt aan `PLAK_AUDIT_PEPPER`. Na een rotatie van die pepper vind je alleen nog regels van ná "
        "de rotatie.\n"
        "\n"
        "Een e-mailadres dat bij geen lid en geen geverifieerd e-mailadres van een content-viewer hoort, wordt niet "
        "gepseudonimiseerd: dat is een 404. Een niet-e-mailadres dat nergens bij hoort (bijvoorbeeld een los "
        "SSO-subject) wordt wel letterlijk gepseudonimiseerd, als `unknown`. Hoort een e-mailadres bij meer dan één "
        "subject (kan alleen via e-mail, nooit via een SSO-subject), dan is het antwoord een 409: zoek in dat geval op "
        "het SSO-subject.\n"
        "\n"
        "**Mag:** alleen een platformbeheerder, met een geldige CSRF-header en een `reason` van 10 tot 500 tekens. Het "
        "opzoeken zelf wordt geaudit, met het pseudoniem, `resolved_as` en de reden, nooit de identifier. Lukt die "
        "auditrij niet, dan komt er geen antwoord: zie de `503`. Telt mee voor de dagelijkse limiet op zulke "
        "opzoekingen, ook bij een 404."
    ),
    "The audit pseudonym that belongs to this identifier.": "Het auditpseudoniem dat bij deze identifier hoort.",
    (
        "Conflict. The e-mail address or name belongs to more than one member (`IDENTIFIER_AMBIGUOUS`); if the name is "
        "ambiguous, use the e-mail address; if the e-mail address is ambiguous, use the SSO subject."
    ): (
        "Conflict. Het e-mailadres of de naam hoort bij meer dan één lid (`IDENTIFIER_AMBIGUOUS`); zoek bij een "
        "meerduidige naam op het e-mailadres, bij een meerduidig e-mailadres op het SSO-subject."
    ),
    (
        "Unprocessable input. `reason` is missing, is shorter than 10 or longer than 500 characters (after stripping "
        "spaces), contains a control or formatting character, or contains an `@` (not an e-mail address; give a case "
        "or ticket number)."
    ): (
        "Onverwerkbare invoer. `reason` ontbreekt, is korter dan 10 of langer dan 500 tekens (na spaties strippen), "
        "bevat een stuur- of opmaakteken, of bevat een `@` (geen e-mailadres; noem een zaak- of ticketnummer)."
    ),
    (
        "Too many requests. The rate limit budget for this session is used up; try again later. The daily limit on "
        "audit lookups by this platform administrator has been reached (`LOOKUP_LIMIT_REACHED`); try again tomorrow."
    ): (
        "Te veel verzoeken. Het ratelimit-budget voor deze sessie is op; probeer het later opnieuw. De dagelijkse "
        "limiet voor herleidingen door deze platformbeheerder is bereikt (`LOOKUP_LIMIT_REACHED`); probeer het morgen "
        "opnieuw."
    ),
    "Look up the actor behind a pseudonym": "Actor achter een pseudoniem opzoeken",
    (
        "The opposite direction of `POST /platform/audit/actor-pseudonym`: give a pseudonym from a log row, get back "
        "who is behind it. Every member, every content viewer and every linked repository is pseudonymised again with "
        "the current `PLAK_AUDIT_PEPPER` and compared with the given pseudonym; nothing is kept to speed up this "
        "lookup, so expect a full pass over all three.\n"
        "\n"
        "If the lookup finds nothing, there is no member, content viewer or repository that pseudonymises to this "
        "pseudonym. Several causes are possible: the pepper has been rotated since that row was written; the actor "
        "only ever visited content through the content-host SSO and that visit was more than 90 days ago "
        "(`content_viewers` is then cleaned up, like the related `content_access` rows); or the repository has since "
        "been unlinked, or deleted along with its site or group (`ON DELETE CASCADE`).\n"
        "\n"
        "That this is a POST and not a query parameter is deliberate, as with the other side of this lookup.\n"
        "\n"
        "**Who can call this:** only a platform administrator, with a valid CSRF header and a `reason` of 10 to 500 "
        "characters. The lookup itself is audited, with the given pseudonym, `resolved_as` and the reason, never the "
        "identifier found. If writing that audit row fails, no response is returned: see the `503`. Counts towards the "
        "daily limit on such lookups, even on a 404."
    ): (
        "De omgekeerde richting van `POST /platform/audit/actor-pseudonym`: geef een pseudoniem uit een logregel, "
        "krijg terug wie erachter zit. Elk lid, elke content-viewer en elke gekoppelde repository wordt met de actuele "
        "`PLAK_AUDIT_PEPPER` opnieuw gepseudonimiseerd en vergeleken met het opgegeven pseudoniem; er wordt niets "
        "bijgehouden om deze zoekslag te versnellen, dus reken op een volledige doorloop van alle drie.\n"
        "\n"
        "Levert de zoekslag niets op, dan is er geen lid, content-viewer of repository dat tot dit pseudoniem "
        "pseudonimiseert. Meerdere oorzaken komen daarvoor in aanmerking: de pepper is geroteerd sinds die regel "
        "geschreven werd; de actor bezocht alleen ooit content via de content-host-SSO en dat bezoek ligt al langer "
        "dan 90 dagen terug (`content_viewers` wordt dan net als de bijbehorende `content_access`-regels opgeruimd); "
        "of de repository is inmiddels ontkoppeld, of met haar site of groep verwijderd (`ON DELETE CASCADE`).\n"
        "\n"
        "Dat dit een POST is en geen queryparameter, is met opzet, net als bij de andere kant van deze zoekslag.\n"
        "\n"
        "**Mag:** alleen een platformbeheerder, met een geldige CSRF-header en een `reason` van 10 tot 500 tekens. Het "
        "opzoeken zelf wordt geaudit, met het opgegeven pseudoniem, `resolved_as` en de reden, nooit de gevonden "
        "identifier. Lukt die auditrij niet, dan komt er geen antwoord: zie de `503`. Telt mee voor de dagelijkse "
        "limiet op zulke opzoekingen, ook bij een 404."
    ),
    "The member, content viewer or CI repository behind this pseudonym.": (
        "Het lid, de content-viewer of de CI-repository achter dit pseudoniem."
    ),
    (
        "Not found. No member, content viewer or linked repository pseudonymises to this value (`PSEUDONYM_UNKNOWN`): "
        "the pepper has been rotated, the content viewer was cleaned up after 90 days without a visit, or the "
        "repository was unlinked or deleted along with its site."
    ): (
        "Niet gevonden. Geen lid, content-viewer of gekoppelde repository pseudonimiseert tot deze waarde "
        "(`PSEUDONYM_UNKNOWN`): de pepper is geroteerd, de content-viewer is opgeruimd na 90 dagen zonder bezoek, of "
        "de repository is ontkoppeld of met haar site verwijderd."
    ),
    (
        "Unprocessable input. `reason` is missing, is shorter than 10 or longer than 500 characters (after stripping "
        "spaces), contains a control or formatting character, or contains an `@` (not an e-mail address; give a case "
        "or ticket number). Not 64 hexadecimal characters (`ACTOR_PSEUDONYM_INVALID`)."
    ): (
        "Onverwerkbare invoer. `reason` ontbreekt, is korter dan 10 of langer dan 500 tekens (na spaties strippen), "
        "bevat een stuur- of opmaakteken, of bevat een `@` (geen e-mailadres; noem een zaak- of ticketnummer). Geen 64 "
        "hexadecimale tekens (`ACTOR_PSEUDONYM_INVALID`)."
    ),
    "Reveal the full IP address of an audit row": "Volledig IP-adres van een auditregel achterhalen",
    (
        "`GET /platform/audit` only shows the truncated network per row (`ipTruncated`); this endpoint decrypts the "
        "full address stored encrypted next to it (`PLAK_AUDIT_IP_KEY`, a different key from the audit pepper). "
        "Intended as a last resort, when the truncated network is not enough, for example in an incident "
        "investigation.\n"
        "\n"
        "**Who can call this:** only a platform administrator, with a valid CSRF header and a `reason` of 10 to 500 "
        "characters. The decryption itself is audited, with the row ID and the reason, never the IP address. If that "
        "audit row fails, no response is returned: see the `503`. Counts towards the daily limit on such lookups, even "
        "on a 404."
    ): (
        "`GET /platform/audit` toont per regel alleen het afgeknotte netwerk (`ipTruncated`); dit endpoint ontsleutelt "
        "het volledige adres dat er versleuteld naast staat (`PLAK_AUDIT_IP_KEY`, een andere sleutel dan de "
        "auditpepper). Bedoeld voor het uiterste geval waarin het netwerk niet volstaat, bijvoorbeeld bij een "
        "incidentonderzoek.\n"
        "\n"
        "**Mag:** alleen een platformbeheerder, met een geldige CSRF-header en een `reason` van 10 tot 500 tekens. Het "
        "ontsleutelen zelf wordt geaudit, met het regel-id en de reden, nooit het IP-adres. Lukt die auditrij niet, "
        "dan komt er geen antwoord: zie de `503`. Telt mee voor de dagelijkse limiet op zulke opzoekingen, ook bij een "
        "404."
    ),
    "ID of the audit row (`AuditEntryOut.id`).": "Id van de auditregel (`AuditEntryOut.id`).",
    "The full IP address for this audit row.": "Het volledige IP-adres bij deze auditregel.",
    "Not found. No audit row with this ID, or it has no encrypted IP address (`AUDIT_IP_UNKNOWN`).": (
        "Niet gevonden. Geen auditregel met dit id, of er staat geen versleuteld IP-adres bij (`AUDIT_IP_UNKNOWN`)."
    ),
    "Members of a site": "Leden van een site",
    (
        "Everyone who can reach this site, not only those with a direct role here. Each row shows the group role, the "
        "site role and the resulting role: the higher of the two wins, because a site role only widens and never takes "
        "anything away.\n"
        "\n"
        "A list with only the site roles would leave out the group members and read as if far fewer people can reach "
        "the site than is actually the case.\n"
        "\n"
        "**Who can call this:** effective site role `reader`."
    ): (
        "Iedereen die bij deze site kan, niet alleen wie hier een eigen rol heeft. Per regel staat de groepsrol, de "
        "siterol en de rol die daaruit volgt: de ruimste van de twee wint, want een siterol verbreedt alleen en neemt "
        "nooit iets af.\n"
        "\n"
        "Een lijst met alleen de siterollen zou de groepsleden weglaten en lezen alsof veel minder mensen erbij kunnen "
        "dan werkelijk het geval is.\n"
        "\n"
        "**Mag:** effectieve siterol `reader`."
    ),
    "Everyone who can reach this site, each with the role that grants access.": (
        "Iedereen die bij deze site kan, met de rol waarmee."
    ),
    "Give a member a role on this site": "Lid een rol op deze site geven",
    (
        "Gives an existing platform member a role on this one site. That can be a higher role than their group role; "
        "lower has no effect, because the higher of the two applies. Someone does not need to be a group member: this "
        "is how you give an outsider access to exactly this site.\n"
        "\n"
        "A group role is changed on the group, not here.\n"
        "\n"
        "**Who can call this:** effective site role `admin`, with a valid CSRF header."
    ): (
        "Geeft een bestaand platformlid een rol op deze ene site. Dat kan een ruimere rol zijn dan zijn groepsrol; "
        "smaller heeft geen effect, want de ruimste van de twee blijft gelden. Iemand hoeft geen groepslid te zijn: zo "
        "geef je een buitenstaander toegang tot precies deze site.\n"
        "\n"
        "Een groepsrol wijzig je niet hier maar bij de groep.\n"
        "\n"
        "**Mag:** effectieve siterol `admin`, met een geldige CSRF-header."
    ),
    "The member with their new site role.": "Het lid met zijn nieuwe siterol.",
    (
        "Not found. Unknown group (`UNKNOWN_GROUP`) or unknown site (`UNKNOWN_SITE`). There is no member with this "
        "identifier; they must sign in themselves first (`UNKNOWN_MEMBER`)."
    ): (
        "Niet gevonden. Onbekende groep (`UNKNOWN_GROUP`) of onbekende site (`UNKNOWN_SITE`). Er is geen lid met deze "
        "identifier; diegene moet eerst zelf inloggen (`UNKNOWN_MEMBER`)."
    ),
    (
        "Conflict. The e-mail address or name belongs to more than one member (`IDENTIFIER_AMBIGUOUS`); if the name is "
        "ambiguous, use the e-mail address; if the e-mail address is ambiguous, use the SSO subject. This member "
        "already has a role on this site (`ALREADY_SITE_MEMBER`)."
    ): (
        "Conflict. Het e-mailadres of de naam hoort bij meer dan één lid (`IDENTIFIER_AMBIGUOUS`); zoek bij een "
        "meerduidige naam op het e-mailadres, bij een meerduidig e-mailadres op het SSO-subject. Dit lid heeft al een "
        "rol op deze site (`ALREADY_SITE_MEMBER`)."
    ),
    "Find someone to give a role on this site": "Iemand zoeken om een rol op deze site te geven",
    (
        "Searches the platform members by name or e-mail address, so you can give someone a role on this site without "
        "knowing their address by heart. The `identifier` of a hit is exactly what `POST "
        "/sites/{group_slug}/{site_slug}/members` expects.\n"
        "\n"
        "Only active members are returned: you do not add someone who has been deactivated. The answer is a shortlist "
        "of at most ten names, not a dump of the whole organisation; someone who already has a direct site role here "
        "is included, with `alreadyMember` set to `true`.\n"
        "\n"
        "You can simply pick group members here: a site role widens what they may already do through the group. What "
        "that is appears in `groupRole`, so you can see whether a site role adds anything.\n"
        "\n"
        "Only those who may add members may search, and Plak does not create an account here either: someone only "
        "appears once they have signed in to the admin interface themselves.\n"
        "\n"
        "**Who can call this:** effective site role `admin`."
    ): (
        "Zoekt in de platformleden op naam of e-mailadres, zodat je iemand een rol op deze site kunt geven zonder zijn "
        "adres uit het hoofd te kennen. De `identifier` uit een treffer is precies wat `POST "
        "/sites/{group_slug}/{site_slug}/members` verwacht.\n"
        "\n"
        "Alleen actieve leden komen terug: wie buitengesloten is, voeg je niet toe. Het antwoord is een shortlist van "
        "hoogstens tien namen, geen uitdraai van de hele organisatie; wie hier al een eigen siterol heeft staat er wel "
        "bij, met `alreadyMember` op `true`.\n"
        "\n"
        "Groepsleden kun je hier gewoon kiezen: een siterol verruimt wat zij via de groep al mogen. Wat dat is staat "
        "in `groupRole`, zodat je ziet of een siterol iets toevoegt.\n"
        "\n"
        "Zoeken mag precies wie ook mag toevoegen, en Plak maakt hier net zomin een account aan: iemand verschijnt pas "
        "zodra hij zelf op het beheer heeft ingelogd.\n"
        "\n"
        "**Mag:** effectieve siterol `admin`."
    ),
    "Change the site role of a member": "Siterol van een lid wijzigen",
    (
        "Gives a member a different role on this one site. Only applies to a direct site role; for someone listed "
        "through their group membership, change the role on the group.\n"
        "\n"
        "The path holds `memberId` from the member list, not the e-mail address: an address in a URL ends up in the "
        "log lines of every proxy in between.\n"
        "\n"
        "**Who can call this:** effective site role `admin`, with a valid CSRF header."
    ): (
        "Geeft een lid een andere rol op deze ene site. Werkt alleen op een siterol; wie hier staat omdat hij "
        "groepslid is, wijzig je bij de groep.\n"
        "\n"
        "In het pad staat `memberId` uit de ledenlijst, niet het e-mailadres: een adres in een URL belandt in de "
        "logregels van elke proxy ertussen.\n"
        "\n"
        "**Mag:** effectieve siterol `admin`, met een geldige CSRF-header."
    ),
    (
        "Not found. Unknown group (`UNKNOWN_GROUP`) or unknown site (`UNKNOWN_SITE`). There is no member with this ID "
        "(`UNKNOWN_MEMBER`), or that member has no direct site role on this site (`NOT_SITE_MEMBER`)."
    ): (
        "Niet gevonden. Onbekende groep (`UNKNOWN_GROUP`) of onbekende site (`UNKNOWN_SITE`). Er is geen lid met dit "
        "id (`UNKNOWN_MEMBER`), of dat lid heeft geen eigen rol op deze site (`NOT_SITE_MEMBER`)."
    ),
    "Remove the site role of a member": "Siterol van een lid weghalen",
    (
        "Removes the role that applied only to this site. A group role remains, so whoever can reach this site through "
        "the group still can afterwards.\n"
        "\n"
        "The path holds `memberId` from the member list, not the e-mail address: an address in a URL ends up in the "
        "log lines of every proxy in between.\n"
        "\n"
        "There is no last-admin protection here as there is for a group: the admins of the group can always reach this "
        "site, so a site without an admin of its own is not unmanageable.\n"
        "\n"
        "**Who can call this:** effective site role `admin`, with a valid CSRF header."
    ): (
        "Haalt de rol weg die alleen op deze site gold. Een groepsrol blijft staan, dus wie via de groep bij deze site "
        "kan, kan dat daarna nog steeds.\n"
        "\n"
        "In het pad staat `memberId` uit de ledenlijst, niet het e-mailadres: een adres in een URL belandt in de "
        "logregels van elke proxy ertussen.\n"
        "\n"
        "Er is hier geen laatste-beheerder-bescherming zoals bij een groep: de beheerders van de groep kunnen altijd "
        "bij deze site, dus een site zonder eigen beheerder is niet onbeheerbaar.\n"
        "\n"
        "**Mag:** effectieve siterol `admin`, met een geldige CSRF-header."
    ),
    "The member no longer has a direct site role on this site. No content is returned.": (
        "Het lid heeft geen eigen rol meer op deze site. Er komt geen inhoud terug."
    ),
    "Deploy history of a site": "Deployhistorie van een site",
    (
        "All versions of this site, newest first, with their target (`live` or `preview`) and origin (`upload` by a "
        "member or `action` by CI). Exactly one version has `isLive`, unless nothing is live yet.\n"
        "\n"
        "**Who can call this:** effective site role `reader` or higher."
    ): (
        "Alle versies van deze site, nieuwste eerst, met hun doel (`live` of `preview`) en herkomst (`upload` door een "
        "lid of `action` door CI). Precies een versie heeft `isLive`, tenzij er nog niets live staat.\n"
        "\n"
        "**Mag:** effectieve siterol `reader` of ruimer."
    ),
    "The versions of the site, newest first.": "De versies van de site, nieuwste eerst.",
    "Storage and retention rule of a site": "Opslag en bewaarregel van een site",
    (
        "How much space the versions of this site currently take up, how much they may take up together, and how many "
        "previous live versions the nightly cleanup leaves in place. The current live version always stays, even after "
        "rolling back to an older version. The limit applies to the whole platform; the number of versions kept is the "
        "platform default, unless a site admin set a custom number for this site.\n"
        "\n"
        "**Who can call this:** effective site role `reader` or higher."
    ): (
        "Hoeveel ruimte de versies van deze site nu innemen, hoeveel ze samen mogen innemen, en hoeveel vorige "
        "live-versies de nachtelijke opschoning laat staan. De huidige live-versie blijft altijd, ook na terugrollen "
        "naar een oudere versie. De limiet geldt voor het hele platform; het aantal bewaarde versies is de standaard "
        "van het platform, tenzij een sitebeheerder voor deze site een eigen aantal instelde.\n"
        "\n"
        "**Mag:** effectieve siterol `reader` of ruimer."
    ),
    "The usage, the limit and the number of live versions kept.": (
        "Het gebruik, de limiet en het aantal bewaarde live-versies."
    ),
    "Roll back to an earlier version": "Terugrollen naar een eerdere versie",
    (
        "Puts an existing version (back) on the public URL. Nothing is uploaded again: the unpacked files of that "
        "version are already there. The switch applies immediately.\n"
        "\n"
        "Only versions with target `live` can be live; a preview version returns 422.\n"
        "\n"
        "**Who can call this:** effective site role `editor` or higher, with a valid CSRF header."
    ): (
        "Zet een bestaande versie (terug) op de publieke URL. Er wordt niets opnieuw geüpload: de uitgepakte bestanden "
        "van die versie staan er al. De wissel geldt onmiddellijk.\n"
        "\n"
        "Alleen versies met doel `live` kunnen live staan; een preview-versie levert 422.\n"
        "\n"
        "**Mag:** effectieve siterol `editor` of ruimer, met een geldige CSRF-header."
    ),
    "ID of the version.": "Id van de versie.",
    "The site with the new live version.": "De site met de nieuwe live versie.",
    (
        "Not found. Unknown group (`UNKNOWN_GROUP`), unknown site (`UNKNOWN_SITE`), or the version does not exist or "
        "belongs to another site (`UNKNOWN_VERSION`, `VERSION_OTHER_SITE`)."
    ): (
        "Niet gevonden. Onbekende groep (`UNKNOWN_GROUP`), onbekende site (`UNKNOWN_SITE`), of de versie bestaat niet "
        "of hoort bij een ander site (`UNKNOWN_VERSION`, `VERSION_OTHER_SITE`)."
    ),
    "Unprocessable input. This version cannot go live, for example because it is a preview version.": (
        "Onverwerkbare invoer. Deze versie kan niet live: het is bijvoorbeeld een preview-versie."
    ),
    "Previews of a site": "Previews van een site",
    (
        "The previews that currently exist for this site, sorted by ref, with their URL on the content origin and the "
        "time at which the cleanup job discards them.\n"
        "\n"
        "**Who can call this:** effective site role `reader` or higher."
    ): (
        "De previews die nu voor deze site bestaan, op ref gesorteerd, met hun URL op de content-origin en het "
        "tijdstip waarop de opruimjob ze weggooit.\n"
        "\n"
        "**Mag:** effectieve siterol `reader` of ruimer."
    ),
    "The previews of the site, sorted by ref.": "De previews van de site, op ref gesorteerd.",
    "Set the access to a preview": "Toegang tot een preview zetten",
    (
        "Gives this preview its own access settings, independent of the site: so a preview can be broader or stricter "
        "than the live site. It is base plus exceptions as a whole, never a base of the preview with exceptions of the "
        "site. `access: null` removes the override, after which the preview follows the site again.\n"
        "\n"
        "**Who can call this:** effective site role `admin`, with a valid CSRF header."
    ): (
        "Geeft deze ene preview een eigen toegang, los van de site: zo kan een preview ruimer of juist strenger staan "
        "dan de live site. Het is basis plus uitzonderingen in hun geheel, nooit een basis van de preview met "
        "uitzonderingen van de site. `access: null` haalt de uitzondering weg, waarna de preview de site weer volgt.\n"
        "\n"
        "**Mag:** effectieve siterol `admin`, met een geldige CSRF-header."
    ),
    "The preview with its new access.": "De preview met haar nieuwe toegang.",
    (
        "Not found. Unknown group (`UNKNOWN_GROUP`), unknown site (`UNKNOWN_SITE`), or this site has no preview with "
        "this ref (`UNKNOWN_PREVIEW`)."
    ): (
        "Niet gevonden. Onbekende groep (`UNKNOWN_GROUP`), onbekende site (`UNKNOWN_SITE`), of dit site heeft geen "
        "preview met deze ref (`UNKNOWN_PREVIEW`)."
    ),
    (
        "The base answer to \"who can look at this site\": exactly one per site,\n"
        "and never the whole story.\n"
        "\n"
        "`nobody` grants no access on its own. A site on that base is unreachable unless\n"
        "one of the two exceptions beside it (secret links, invitees) is on, which is\n"
        "what makes \"alleen via de uitzonderingen hieronder\" (\"only through the exceptions below\")\n"
        "expressible at all."
    ): (
        "Het basisantwoord op \"wie mag deze site zien\": precies één per site,\n"
        "en nooit het hele verhaal.\n"
        "\n"
        "NOBODY geeft op zichzelf niemand toegang. Een site met die basis is onbereikbaar, tenzij\n"
        "een van de twee uitzonderingen ernaast (geheime links, genodigden) aan staat; daarmee is\n"
        "\"alleen via de uitzonderingen hieronder\" überhaupt uit te drukken."
    ),
    (
        "The base, exactly one of: `public` (everyone), `sso` (every user who signs in with SSO Rijk), `site_team` "
        "(anyone with a role on the site or on its group) or `nobody` (nobody by default: only through the exceptions "
        "below)."
    ): (
        "De basis, precies één van: `public` (iedereen), `sso` (elke gebruiker die inlogt met SSO Rijk), `site_team` "
        "(wie een rol heeft op de site of op haar groep) of `nobody` (niemand standaard: alleen via de uitzonderingen "
        "hieronder)."
    ),
    (
        "Whether secret links grant access. Besides the base there are two exceptions that can be added independently "
        "of each other: `keys` lets in anyone with a valid secret link, even without signing in, and `invitees` lets "
        "in signed-in addresses on the invitee list. They widen the base and never narrow it, so with base `public` "
        "they change nothing."
    ): (
        "Of geheime links toegang geven. Naast de basis staan twee uitzonderingen die er los van elkaar bij kunnen: "
        "`keys` laat iedereen met een geldige geheime link binnen, ook zonder inloggen, en `invitees` laat ingelogde "
        "adressen van de genodigdenlijst binnen. Ze verbreden de basis en versmallen hem nooit, dus bij basis `public` "
        "veranderen ze niets."
    ),
    "Whether to give invitees access after signing in with SSO Rijk.": (
        "Of genodigden toegang geven na inloggen met SSO Rijk."
    ),
    "Who may see the content: a base plus two exceptions.": (
        "Wie de content mag zien: een basis plus twee uitzonderingen."
    ),
    (
        "Access when creating: base plus exceptions. Every field may be omitted; what then applies\n"
        "is described on the field that uses this object."
    ): (
        "Toegang bij het aanmaken: basis plus uitzonderingen. Elk veld mag weg; wat er dan geldt,\n"
        "staat bij het veld dat dit object draagt."
    ),
    "Whether secret links grant access, even without signing in.": (
        "Of geheime links toegang geven, ook zonder inloggen."
    ),
    "Whether invitees get access after signing in with SSO Rijk.": (
        "Of genodigden toegang geven na inloggen met SSO Rijk."
    ),
    (
        "Why this re-identification is needed, 10 to 500 characters. Give a case or ticket number, not an e-mail "
        "address or other personal data: this field ends up readable in the audit log, for every platform "
        "administrator."
    ): (
        "Waarom deze herleiding nodig is, 10 tot 500 tekens. Noem een zaak- of ticketnummer, geen e-mailadres of "
        "andere persoonsgegevens: dit veld komt leesbaar in het auditlog te staan, voor elke platformbeheerder."
    ),
    "The pseudonym from the audit log: 64 hexadecimal characters.": (
        "Het pseudoniem uit het auditlog: 64 hexadecimale tekens."
    ),
    "An audit pseudonym, to look up the actor behind it.": (
        "Een auditpseudoniem, om de actor erachter mee op te zoeken."
    ),
    "Whether the pseudonym belongs to a member, a content viewer or a CI repository.": (
        "Of het pseudoniem bij een lid, een content-viewer of een CI-repository hoort."
    ),
    "Internal ID of the member, for kind `member`.": "Interne id van het lid, bij kind `member`.",
    "E-mail address, for kind `member` or `content_viewer`; `null` if none is known.": (
        "E-mailadres, bij kind `member` of `content_viewer`; `null` als er geen bekend is."
    ),
    (
        "Whether `email` was a verified claim of the identity provider, for kind `content_viewer`. The forward lookup "
        "(`actor-pseudonym`) only matches on a verified e-mail address."
    ): (
        "Of `email` een geverifieerde claim van de identity provider was, bij kind `content_viewer`. De voorwaartse "
        "zoekslag (`actor-pseudonym`) matcht alleen op een geverifieerd e-mailadres."
    ),
    "Display name of the member, for kind `member`; empty if the identity provider does not send it.": (
        "Weergavenaam van het lid, bij kind `member`; leeg als de identity provider die niet stuurt."
    ),
    "Status of the member, for kind `member`.": "Status van het lid, bij kind `member`.",
    "Last sign-in on the content host, for kind `content_viewer`.": (
        "Laatste keer inloggen op de content-host, bij kind `content_viewer`."
    ),
    "CI provider of the repository, for kind `ci`.": "CI-provider van de repository, bij kind `ci`.",
    "Base URL of the provider, for kind `ci`.": "Basis-URL van de provider, bij kind `ci`.",
    "The repository as `owner/repo`, for kind `ci`.": "De repository als `eigenaar/repo`, bij kind `ci`.",
    (
        "The sites to which this repository is currently linked, as `group/site`, for kind `ci`. An unlinked "
        "repository can no longer be traced."
    ): (
        "De sites waaraan deze repository nu gekoppeld is, als `groep/site`, bij kind `ci`. Een ontkoppelde repository "
        "is niet meer te herleiden."
    ),
    "The actor behind an audit pseudonym.": "De actor achter een auditpseudoniem.",
    (
        "E-mail address or SSO subject of a member or content viewer, or a linked repository as `owner/repo`, "
        "`github.com/owner/repo` or `https://code.overheid.nl/owner/repo`. An e-mail address is first translated to "
        "the SSO subject of that member or viewer, because that is what is audited."
    ): (
        "E-mailadres of SSO-subject van een lid of content-viewer, of een gekoppelde repository als `eigenaar/repo`, "
        "`github.com/eigenaar/repo` of `https://code.overheid.nl/eigenaar/repo`. Een e-mailadres wordt eerst naar het "
        "SSO-subject van dat lid of die viewer vertaald, want daarop is geaudit."
    ),
    "The identifier of an actor, to look up their audit pseudonym with.": (
        "De identifier van een actor, om zijn auditpseudoniem mee op te zoeken."
    ),
    "Value to use as `actorPseudonym` filter on `GET /platform/audit`.": (
        "Waarde om als `actorPseudonym` mee te filteren op `GET /platform/audit`."
    ),
    (
        "What the identifier belonged to: `member` (translated to their SSO subject), `content_viewer` (an SSO viewer "
        "of protected content, seen in the last 90 days), `ci` (a repository linked to a site) or `unknown` "
        "(pseudonymised as given, only for an identifier that is not an e-mail address: an e-mail address that belongs "
        "to nothing gives a 404)."
    ): (
        "Waar de identifier bij hoorde: `member` (vertaald naar zijn SSO-subject), `content_viewer` (een SSO-viewer "
        "van afgeschermde content, gezien in de laatste 90 dagen), `ci` (een repository die aan een site gekoppeld is) "
        "of `unknown` (letterlijk gepseudonimiseerd, alleen bij een niet-e-mailadres: een e-mailadres dat nergens bij "
        "hoort geeft een 404)."
    ),
    "The audit pseudonym that belongs to an identifier.": "Het auditpseudoniem dat bij een identifier hoort.",
    "ID of the audit row; also the second sort key after `occurredAt`.": (
        "Id van de auditregel; ook de tweede sorteersleutel achter `occurredAt`."
    ),
    "Time of the action.": "Tijdstip van de handeling.",
    (
        "Kind of actor: `member` (a signed-in member, including via the CLI), `ci` (a CI workflow with an ID token), "
        "`system` (the application itself) or `anonymous` (a visitor without a session)."
    ): (
        "Soort actor: `member` (een ingelogd lid, ook via de CLI), `ci` (een CI-workflow met een ID-token), `system` "
        "(de applicatie zelf) of `anonymous` (een bezoeker zonder sessie)."
    ),
    (
        "Pseudonym of the actor: HMAC-SHA256 of their identifier under the audit pepper. Equal pseudonyms mean the "
        "same actor, as long as the pepper has not been changed. `null` for `system` and `anonymous`."
    ): (
        "Pseudoniem van de actor: HMAC-SHA256 van zijn identifier onder de audit-pepper. Gelijke pseudoniemen "
        "betekenen dezelfde actor, zolang de pepper niet gewisseld is. `null` bij `system` en `anonymous`."
    ),
    "What happened, for example `content_access` or `site_create`.": (
        "Wat er gebeurde, bijvoorbeeld `content_access` of `site_create`."
    ),
    "How it went: `allowed`, `refused` or `login_redirect`.": (
        "Hoe het afliep: `allowed`, `refused` of `login_redirect`."
    ),
    "Reason behind the outcome, for example `UNKNOWN_SITE`.": "Reden achter de uitkomst, bijvoorbeeld `UNKNOWN_SITE`.",
    "What the action was about: keys such as `group`, `site`, `preview` and `path`.": (
        "Waar de handeling over ging: sleutels als `group`, `site`, `preview` en `path`."
    ),
    "Network of the visitor, truncated to /24 (IPv4) or /48 (IPv6); never the whole address.": (
        "Netwerk van de bezoeker, afgeknot op /24 (IPv4) of /48 (IPv6); nooit het hele adres."
    ),
    "One row of the audit log: an action, who did it and how it went.": (
        "Eén regel uit het auditlog: een handeling, wie hem deed en hoe hij afliep."
    ),
    "The rows of this page, newest first.": "De regels van deze pagina, nieuwste eerst.",
    "Opaque reference to the next page; pass it back unchanged as `cursor`. `null` means this was the last page.": (
        "Ondoorzichtige verwijzing naar de volgende pagina; geef hem ongewijzigd terug als `cursor`. `null` betekent "
        "dat dit de laatste pagina was."
    ),
    "One page of the audit log, newest row first.": "Eén pagina uit het auditlog, nieuwste regel eerst.",
    "E-mail address of the member.": "E-mailadres van het lid.",
    "Display name; empty if the identity provider does not send one.": (
        "Weergavenaam; leeg als de identity provider die niet stuurt."
    ),
    "The member behind a CLI session.": "Het lid achter een CLI-sessie.",
    "ID of the CLI session; you use it to revoke it.": "Id van de CLI-sessie; hiermee trek je hem in.",
    "How the CLI named itself when signing in.": "Hoe de CLI zichzelf noemde bij het koppelen.",
    "Time of sign-in.": "Tijdstip van koppelen.",
    "Last time the CLI did something or refreshed; `null` if that has not happened yet.": (
        "Laatste keer dat de CLI iets deed of verversde; `null` als dat nog niet gebeurde."
    ),
    "When the session expires without further use.": "Wanneer de sessie verloopt zonder verder gebruik.",
    "A linked CLI session: an approved `plak login` of the signed-in member.": (
        "Een gekoppelde sessie: een goedgekeurde `plak login` van het ingelogde lid."
    ),
    (
        "ID of the version that was just created. It identifies the deploy in the site's version list, and it is what "
        "you roll back to later."
    ): (
        "Id van de zojuist aangemaakte versie. Hiermee is de deploy terug te vinden in de versielijst van de site, en "
        "hiernaar is later terug te rollen."
    ),
    (
        "Where the deploy can be viewed: the live site, or for a preview the preview URL. CI can post a link to it, in "
        "a pull request for example."
    ): (
        "Waar de deploy te bekijken is: de live site, of bij een preview de preview-URL. CI kan hier een link naar "
        "plaatsen, in een pull request bijvoorbeeld."
    ),
    (
        "Who may see the deploy: for a preview the preview's own access if one is set, otherwise the site's. CI can "
        "use this to tell, alongside the link, whether signing in is needed."
    ): (
        "Wie de deploy mag zien: bij een preview de eigen toegang van die preview als die is ingesteld, anders die van "
        "de site. CI kan hiermee bij de link zeggen of je moet inloggen."
    ),
    "What CI gets back after a successful deploy.": "Wat CI terugkrijgt na een geslaagde deploy.",
    "Secret for the CLI itself: it uses this to request the tokens. Never show or log it.": (
        "Geheim voor de CLI zelf: hiermee vraagt hij de tokens op. Nooit tonen of loggen."
    ),
    (
        "Code to show in the terminal and to compare or type in the admin interface: eight characters without 0, O, 1 "
        "and I, with a hyphen in the middle. Case-insensitive."
    ): (
        "Code om in de terminal te tonen en in het beheer te vergelijken of in te tikken: acht tekens zonder 0, O, 1 "
        "en I, met een koppelteken in het midden. Hoofdletterongevoelig."
    ),
    "Page in the admin interface where the member enters the code.": (
        "Pagina in het beheer waar het lid de code invoert."
    ),
    "The same page with the code already filled in; for opening in a browser.": (
        "Dezelfde pagina met de code al ingevuld; om in de browser te openen."
    ),
    "Seconds until the codes expire.": "Seconden tot de codes verlopen.",
    "Minimum number of seconds the CLI waits between attempts.": (
        "Seconden die de CLI minstens tussen twee pogingen wacht."
    ),
    "What the CLI needs to finish signing in (RFC 8628 3.2).": (
        "Wat de CLI nodig heeft om het inloggen af te maken (RFC 8628 3.2)."
    ),
    "The user code, normalised.": "De gebruikerscode, genormaliseerd.",
    "How the CLI named itself; `null` if it gave nothing.": "Hoe de CLI zichzelf noemde; `null` als hij niets opgaf.",
    "The network from which the CLI started the login (IPv4 /24, IPv6 /48).": (
        "Het netwerk waarvandaan de CLI de login begon (IPv4 /24, IPv6 /48)."
    ),
    "When the CLI started the login.": "Wanneer de CLI de login begon.",
    "When the code expires.": "Wanneer de code verloopt.",
    (
        "Whether the CLI started the login from the same truncated network (IPv4 /24, IPv6 /48) as the one from which "
        "the member is approving now. `false` is a reason to be extra careful: someone else may have sent the code. "
        "`null` if one of the two addresses is unknown. No full IP address is in the response."
    ): (
        "Of de CLI de login begon vanaf hetzelfde afgekapte netwerk (IPv4 /24, IPv6 /48) als waarvandaan het lid nu "
        "goedkeurt. `false` is een reden om extra op te letten: iemand anders kan de code gestuurd hebben. `null` als "
        "een van beide adressen onbekend is. Er komt geen volledig IP-adres in het antwoord."
    ),
    "A pending CLI login, as the approval screen shows it.": (
        "Een openstaande CLI-login, zoals het goedkeuringsscherm hem toont."
    ),
    (
        "How the CLI names itself; shown on the approval screen and in the list of linked CLI sessions. At most 100 "
        "characters; control and formatting characters are stripped."
    ): (
        "Hoe de CLI zichzelf noemt; staat op het goedkeuringsscherm en in de lijst gekoppelde sessies. Hoogstens 100 "
        "tekens; stuur- en opmaaktekens worden eruit gehaald."
    ),
    "The start of `plak login`.": "Het begin van `plak login`.",
    (
        "`true` (the default) lets the page load scripts and styles from cdnjs, jsDelivr and unpkg, and fonts from "
        "Google Fonts. `false` only allows sources from the site itself, and is the safer choice for a confidential "
        "page. What stays blocked in both modes: fetching data from or sending data to other hosts, images from "
        "elsewhere, an iframe, and a form that posts elsewhere."
    ): (
        "`true` (de standaard) laat de pagina scripts en stijlen laden van cdnjs, jsDelivr en unpkg, en lettertypen "
        "van Google Fonts. `false` laat alleen bronnen uit de site zelf toe, en is de veiligere keuze voor een "
        "vertrouwelijke pagina. Wat in beide standen geblokkeerd blijft: gegevens ophalen bij of sturen naar andere "
        "hosts, afbeeldingen van elders, een iframe, en een formulier dat elders post."
    ),
    "Whether the content of this site may load external sources.": (
        "Of de content van deze site externe bronnen mag laden."
    ),
    "Display name of the group.": "Weergavenaam van de groep.",
    (
        "Slug of the group: lowercase letters, digits and hyphens, at most 63 characters, and not reserved "
        "(`robots.txt`, `favicon.ico`, `.well-known`). This becomes the first path segment of every site URL of the "
        "group."
    ): (
        "Slug van de groep: kleine letters, cijfers en koppeltekens, hoogstens 63 tekens, en niet gereserveerd "
        "(`robots.txt`, `favicon.ico`, `.well-known`). Dit wordt het eerste padsegment van elke site-URL van de groep."
    ),
    (
        "Optional: the default access the group starts with. Every omitted field, or the whole object omitted, gets "
        "the default: base `site_team`, no secret links, no invitees."
    ): (
        "Optioneel: de standaardtoegang waarmee de groep begint. Elk weggelaten veld, of het hele object weggelaten, "
        "krijgt de standaard: basis `site_team`, geen geheime links, geen genodigden."
    ),
    "A new group.": "Een nieuwe groep.",
    "The group itself.": "De groep zelf.",
    "Sites of the group, sorted by slug.": "Sites van de groep, op slug gesorteerd.",
    "Members of the group, sorted by e-mail address.": "Leden van de groep, op e-mailadres gesorteerd.",
    "Everything the group page of the SPA needs in one go.": (
        "Alles wat de groepspagina van de SPA in één keer nodig heeft."
    ),
    (
        "E-mail address, SSO subject, or the full name as known in the admin interface (case and leading or trailing "
        "spaces ignored). A name only works if it matches exactly one active platform member; if it matches more than "
        "one, the response is 409; use the e-mail address instead. Case in an e-mail address is ignored."
    ): (
        "E-mailadres, SSO-subject, of de volledige naam zoals die op het beheer bekend is (hoofdletters en spaties aan "
        "het begin of eind genegeerd). Een naam werkt alleen als hij precies overeenkomt met één actief platformlid; "
        "komt hij bij meer dan één lid voor, dan volgt een 409 en zoek je op het e-mailadres. Hoofdletters in een "
        "e-mailadres worden genegeerd."
    ),
    (
        "Role this member gets in the group: `reader` (views), `editor` (publishes) or `admin` (determines access, "
        "invitees, secret links and who is in the group). A higher role can do everything a lower role can. Omitted "
        "means `reader`."
    ): (
        "Rol die dit lid in de groep krijgt: `reader` (leest mee), `editor` (publiceert) of `admin` (bepaalt toegang, "
        "genodigden, geheime links en wie er in de groep zit). Een ruimere rol kan alles wat een smallere rol kan. "
        "Weggelaten betekent `reader`."
    ),
    "A person who is added to the group, with the role they get there.": (
        "Een persoon die aan de groep wordt toegevoegd, met de rol die hij daar krijgt."
    ),
    "Slug of the group.": "Slug van de groep.",
    "ID of the platform member; you use it to remove them from the group.": (
        "Id van het platformlid; hiermee haal je het uit de groep."
    ),
    "What you use to add this member to the group or change their role: the e-mail address.": (
        "Waarmee je dit lid aan de groep toevoegt of zijn rol wijzigt: het e-mailadres."
    ),
    "Display name from the SSO profile; empty if missing.": "Weergavenaam uit het SSO-profiel; leeg als die ontbreekt.",
    "E-mail address from the SSO profile.": "E-mailadres uit het SSO-profiel.",
    (
        "Role of this member in this group: `reader` (views), `editor` (publishes) or `admin` (determines access, "
        "invitees, secret links and who is in the group). A higher role can do everything a lower role can."
    ): (
        "Rol van dit lid in deze groep: `reader` (leest mee), `editor` (publiceert) of `admin` (bepaalt toegang, "
        "genodigden, geheime links en wie er in de groep zit). Een ruimere rol kan alles wat een smallere rol kan."
    ),
    (
        "The sites in this group on which this member has a direct site role, sorted by slug. Such a role is "
        "independent of the group role and stays in force if the member leaves the group, unless you remove it at the "
        "same time (`siteRoles=remove` when removing).\n"
        "\n"
        "What this member has in another group is not listed: that belongs to that group."
    ): (
        "De sites in déze groep waarop dit lid een eigen rol heeft, op slug gesorteerd. Zo'n rol staat los van de "
        "groepsrol en blijft gelden als het lid uit de groep gaat, tenzij je hem meeneemt (`siteRoles=remove` bij het "
        "verwijderen).\n"
        "\n"
        "Wat dit lid in een andere groep heeft staat er niet bij: dat hoort bij die groep."
    ),
    "A member of a group, as the member list of that group shows it.": (
        "Een lid van een groep, zoals de ledenlijst van die groep het toont."
    ),
    "Access that a new site in this group starts with. Existing sites are not affected.": (
        "Toegang die een nieuwe site in deze groep meekrijgt. Bestaande sites veranderen niet mee."
    ),
    "A group: owner of sites and the unit of membership.": (
        "Een groep: eigenaar van sites en de eenheid waarop lidmaatschap telt."
    ),
    (
        "Role this member gets in the group: `reader` (views), `editor` (publishes) or `admin` (determines access, "
        "invitees, secret links and who is in the group). A higher role can do everything a lower role can."
    ): (
        "Rol die dit lid in de groep krijgt: `reader` (leest mee), `editor` (publiceert) of `admin` (bepaalt toegang, "
        "genodigden, geheime links en wie er in de groep zit). Een ruimere rol kan alles wat een smallere rol kan."
    ),
    "The new role of a member within a group.": "De nieuwe rol van een lid binnen een groep.",
    (
        "The sites of the group that this member may see, sorted by slug. That is all of them, unless the member only "
        "reaches the group through a site role: then only those sites are listed."
    ): (
        "De sites van de groep die dit lid mag zien, op slug gesorteerd. Dat zijn ze alle, tenzij het lid de groep "
        "alleen via een siterol bereikt: dan staan alleen die sites erin."
    ),
    "A group with its sites, as the overview shows it.": "Een groep met haar sites, zoals het overzicht die toont.",
    "Slug of the site within this group.": "Slug van de site binnen deze groep.",
    "Title of the site, as shown in the admin interface.": "Titel van de site, zoals die in het beheer staat.",
    (
        "Role that applies only on this site: `reader` (views), `editor` (publishes) or `admin` (determines access, "
        "invitees, secret links and who has a role on the site). A higher role can do everything a lower role can."
    ): (
        "Rol die alleen op deze site geldt: `reader` (leest mee), `editor` (publiceert) of `admin` (bepaalt toegang, "
        "genodigden, geheime links en wie een rol op de site heeft). Een ruimere rol kan alles wat een smallere rol "
        "kan."
    ),
    "A role of its own on one site, always a site in the group you are looking at.": (
        "Een eigen rol op één site, altijd een site in de groep waar je naar kijkt."
    ),
    "E-mail address or SSO subject. Case in an e-mail address is ignored.": (
        "E-mailadres of SSO-subject. Hoofdletters in een e-mailadres worden genegeerd."
    ),
    "A person, identified by e-mail address or SSO subject.": "Een persoon, aangeduid met e-mailadres of SSO-subject.",
    "ID of this invitee; you use it to remove them from the list.": (
        "Id van deze genodigde; hiermee haal je hem van de lijst."
    ),
    "Slug of the site.": "Slug van de site.",
    "E-mail address or SSO subject of the invitee, normalised to lowercase.": (
        "E-mailadres of SSO-subject van de genodigde, genormaliseerd naar kleine letters."
    ),
    "ID of the member who added the invitee; empty if that member has since been deleted.": (
        "Id van het lid dat de genodigde toevoegde; leeg als dat lid inmiddels verwijderd is."
    ),
    "When the invitee was added.": "Tijdstip van toevoegen.",
    "An address on the invitee list of a site.": "Een adres op de genodigdenlijst van een site.",
    "Justification for decrypting the full IP address of one audit row.": (
        "Motivatie om het volledige IP-adres bij één auditregel te ontsleutelen."
    ),
    "The full IP address as stored with this audit row.": (
        "Het volledige IP-adres zoals opgeslagen bij deze auditregel."
    ),
    "The full IP address behind one audit row.": "Het volledige IP-adres achter één auditregel.",
    (
        "What this link is for; only for the admin's own use, it does not appear in the URL. Omit or leave empty for a "
        "name with today's date."
    ): (
        "Waar deze link voor is; alleen voor de beheerder zelf, hij komt niet in de URL. Laat weg of leeg voor een "
        "naam met de datum van vandaag."
    ),
    (
        "Time after which the link stops working (RFC 3339). Omit or `null` for the default validity of 90 days; at "
        "most 365 days ahead."
    ): (
        "Tijdstip waarna de link niet meer werkt (RFC 3339). Laat weg of `null` voor de standaardtermijn van 90 dagen; "
        "hoogstens 365 dagen vooruit."
    ),
    "A new secret link.": "Een nieuwe geheime link.",
    "The created key.": "De aangemaakte sleutel.",
    (
        "The full key value `<selector>.<secret>` for use in the link. Plak only stores a hash, so this value cannot "
        "be retrieved anywhere afterwards."
    ): (
        "De volledige sleutelwaarde `<selector>.<geheim>` voor in de link. Plak bewaart alleen een hash, dus deze "
        "waarde is hierna nergens meer op te vragen."
    ),
    "The fresh key plus its value. This is the only moment the value can be seen.": (
        "De verse sleutel plus haar waarde. Dit is het enige moment waarop de waarde te zien is."
    ),
    "What this link is for; only for the admin.": "Waar deze link voor is; alleen voor de beheerder.",
    "First half of the key value: the non-secret half, with which you look up the key.": (
        "Eerste helft van de sleutelwaarde: de niet-geheime helft, waarmee je de sleutel opzoekt."
    ),
    "`active` (works) or `revoked` (no longer works).": "`active` (werkt) of `revoked` (werkt niet meer).",
    "Time of creation.": "Tijdstip van aanmaken.",
    "Time after which the link stops working.": "Tijdstip waarna de link niet meer werkt.",
    "A secret link, without the secret itself.": "Een geheime link, zonder het geheim zelf.",
    (
        "`nl` or `en`, or `null` to let the browser decide again (`Accept-Language`, with English if that does not "
        "settle it). The choice is tied to the account, so it applies on every device."
    ): (
        "`nl` of `en`, of `null` om de taal weer door de browser te laten bepalen (`Accept-Language`, met Engels als "
        "het daar niet uitkomt). De keuze hangt aan het account, dus hij reist mee naar elk apparaat."
    ),
    "The language choice of the signed-in member.": "De taalkeuze van het ingelogde lid.",
    (
        "The number of previous live versions that the nightly cleanup leaves in place in addition to the current one: "
        "an integer of 0 or more. `0` keeps all live versions of this site; `null` makes the site follow the platform "
        "default."
    ): (
        "Het aantal vorige live-versies dat de nachtelijke opschoning naast de huidige laat staan: een geheel getal "
        "van 0 of meer. `0` bewaart alle live-versies van deze site; `null` laat de site de standaard van het platform "
        "volgen."
    ),
    "How many previous live versions this site keeps.": "Hoeveel vorige live-versies deze site bewaart.",
    "A refresh token of the session, the current one or one that was already used.": (
        "Een verversingstoken van de sessie, het huidige of een al gebruikt."
    ),
    "Sign out with the refresh token, for when there is no (valid) access token any more.": (
        "Uitloggen met het verversingstoken, voor als er geen (geldig) toegangstoken meer is."
    ),
    (
        "The interface language a member picked for themselves.\n"
        "\n"
        "The values are the ones plak.i18n supports; test_identity.py keeps the two\n"
        "in sync. \"Follow my browser\" is not a value here but the absence of one,\n"
        "see Member.language."
    ): (
        "De interfacetaal die een lid voor zichzelf koos.\n"
        "\n"
        "De waarden zijn die welke plak.i18n ondersteunt; test_identity.py houdt de twee\n"
        "gelijk. \"Volg mijn browser\" is hier geen waarde maar het ontbreken ervan,\n"
        "zie Member.language."
    ),
    "Internal ID of the member.": "Interne id van het lid.",
    "The `sub` from the SSO token; a session is tied to a member through it.": (
        "De `sub` uit het SSO-token; daarop hangt een sessie aan een lid."
    ),
    "E-mail address from the SSO profile; also the identifier for group membership.": (
        "E-mailadres uit het SSO-profiel; tevens de identifier bij groepslidmaatschap."
    ),
    "Display name from the SSO profile; empty if the identity provider does not send it.": (
        "Weergavenaam uit het SSO-profiel; leeg als de identity provider die niet stuurt."
    ),
    "`admin` may configure platform-wide, `member` only within their own groups.": (
        "`admin` mag platformbreed inrichten, `member` alleen binnen de eigen groepen."
    ),
    "`active` (may use the API) or `deactivated` (is refused). Only `active` gets through to the API.": (
        "`active` (mag de API gebruiken) of `deactivated` (wordt geweigerd). Alleen `active` komt langs de API."
    ),
    (
        "Whether this is the account from `PLAK_BOOTSTRAP_ADMIN_SUB`. That account is restored to administrator and "
        "active at every sign-in, so its status and platform role cannot be changed. The SPA uses this to not offer "
        "those actions."
    ): (
        "Of dit het account uit `PLAK_BOOTSTRAP_ADMIN_SUB` is. Dat account wordt bij elke login hersteld naar "
        "beheerder-en-actief, dus status en platformrol zijn er niet te wijzigen. De SPA gebruikt dit om die "
        "handelingen niet aan te bieden."
    ),
    "When the member was created: the first visit to the admin interface.": (
        "Moment waarop het lid ontstond: het eerste bezoek aan de beheeromgeving."
    ),
    "Last successful sign-in, or `null` if there was none yet.": (
        "Laatste succesvolle login, of `null` als die er nog niet was."
    ),
    "A platform member.": "Een platformlid.",
    "What you use to add this member: the e-mail address.": "Waarmee je dit lid toevoegt: het e-mailadres.",
    (
        "Whether this member already has a direct role here: a group role in the search field of a group, a site role "
        "in that of a site. You do not add them then; you change the role in the member list."
    ): (
        "Of dit lid hier al een eigen rol heeft: een groepsrol bij het zoekveld van een groep, een siterol bij dat van "
        "een site. Toevoegen doe je dan niet meer; de rol wijzig je in de ledenlijst."
    ),
    (
        "Only in the search field of a site: the role with which this member already reaches this site through the "
        "group, or `null` if they are not a group member: `reader` (views), `editor` (publishes) or `admin` "
        "(determines access, invitees, secret links and who is in the group). A higher role can do everything a lower "
        "role can. A site role only widens, so an equally low or lower site role changes nothing here. In the search "
        "field of a group this field is always `null`."
    ): (
        "Alleen bij het zoekveld van een site: de rol waarmee dit lid deze site nu al via de groep bereikt, of `null` "
        "als het geen groepslid is: `reader` (leest mee), `editor` (publiceert) of `admin` (bepaalt toegang, "
        "genodigden, geheime links en wie er in de groep zit). Een ruimere rol kan alles wat een smallere rol kan. Een "
        "siterol verruimt alleen, dus een even smalle of smallere siterol verandert hier niets aan. Bij het zoekveld "
        "van een groep is dit veld altijd `null`."
    ),
    "A platform member that was found, as the search field of 'add member' shows it.": (
        "Een gevonden platformlid, zoals het zoekveld bij 'lid toevoegen' het toont."
    ),
    (
        "Role of the member in this group: `reader` (views), `editor` (publishes) or `admin` (determines access, "
        "invitees, secret links and who is in the group). A higher role can do everything a lower role can."
    ): (
        "Rol van het lid in deze groep: `reader` (leest mee), `editor` (publiceert) of `admin` (bepaalt toegang, "
        "genodigden, geheime links en wie er in de groep zit). Een ruimere rol kan alles wat een smallere rol kan."
    ),
    "A group in which the signed-in member has a role.": "Een groep waarin het ingelogde lid een rol heeft.",
    (
        "Origin on which the published content lives. The SPA builds public URLs, preview links and secret links on "
        "it. Always a different host than the admin interface: content and admin never share an origin."
    ): (
        "Origin waarop de gepubliceerde content staat. De SPA bouwt hier publieke URL's, preview-links en geheime "
        "links op. Altijd een andere host dan het beheer: content en beheer delen nooit een origin."
    ),
    "Groups in which this member has a role, sorted by slug. Empty if the member is not a group member anywhere.": (
        "Groepen waarin dit lid een rol heeft, op slug gesorteerd. Leeg als het lid nergens groepslid is."
    ),
    (
        "Sites on which this member has a direct site role, sorted by group and site. Only the sites with a direct "
        "site role are listed: on every other site of a group the group role from `groupRoles` simply applies."
    ): (
        "Sites waarop dit lid een eigen siterol heeft, op groep en site gesorteerd. Alleen de sites met zo'n eigen rol "
        "staan erin: op elke andere site van een groep geldt gewoon de groepsrol uit `groupRoles`."
    ),
    (
        "The Forgejo instances whose CI ID tokens Plak accepts (`PLAK_CI_FORGEJO_HOSTS`), as base URL. GitHub is "
        "always accepted and is not listed here."
    ): (
        "De Forgejo-instanties waarvan Plak CI-ID-tokens accepteert (`PLAK_CI_FORGEJO_HOSTS`), als basis-URL. GitHub "
        "wordt altijd geaccepteerd en staat hier niet in."
    ),
    (
        "The audience a CI workflow must request for its ID token: exactly `PLAK_BASE_URL`. That is also the value for "
        "the `host` input of the plak action."
    ): (
        "De audience die een CI-workflow voor zijn ID-token moet aanvragen: precies `PLAK_BASE_URL`. Dat is ook de "
        "waarde voor de invoer `host` van de plak-action."
    ),
    (
        "The language this member chose for the admin interface: `nl` or `en`. `null` means the member made no choice "
        "and the SPA takes the language from the browser. The choice is stored on the account and so applies on every "
        "device."
    ): (
        "De taal die dit lid zelf koos voor het beheer: `nl` of `en`. `null` betekent dat het lid geen keuze maakte en "
        "de SPA de taal uit de browser haalt. De keuze staat op het account en geldt dus op elk apparaat."
    ),
    "The signed-in member, extended with what the SPA needs to build links.": (
        "Het ingelogde lid, aangevuld met wat de SPA nodig heeft om links te bouwen."
    ),
    "Slug of the group this site is in.": "Slug van de groep waar deze site in zit.",
    (
        "The site role itself, independent of the group role: `reader` (views), `editor` (publishes) or `admin` "
        "(determines access, invitees, secret links and who has a role on the site). A higher role can do everything a "
        "lower role can."
    ): (
        "De siterol zelf, los van de groepsrol: `reader` (leest mee), `editor` (publiceert) of `admin` (bepaalt "
        "toegang, genodigden, geheime links en wie een rol op de site heeft). Een ruimere rol kan alles wat een "
        "smallere rol kan."
    ),
    "What the member is actually allowed to do on this site: the higher of their group role and this site role.": (
        "Wat het lid op deze site werkelijk mag: de ruimste van zijn groepsrol en deze siterol."
    ),
    "A site on which the signed-in member has a direct site role.": (
        "Een site waarop het ingelogde lid een eigen siterol heeft."
    ),
    (
        "Groups this member may see, sorted by slug: where they have a group role, and where they have a role on a "
        "site. Empty if the member has no role anywhere."
    ): (
        "Groepen die dit lid mag zien, op slug gesorteerd: waar het een groepsrol heeft, en waar het een rol op een "
        "site heeft. Leeg als het lid nergens een rol heeft."
    ),
    "The home screen of the admin SPA.": "Het startscherm van de beheer-SPA.",
    "`admin` makes the member platform administrator, `member` removes that role again.": (
        "`admin` maakt het lid platformbeheerder, `member` haalt die rol er weer af."
    ),
    "The new platform role of a member.": "De nieuwe platformrol van een lid.",
    (
        "Access that applies only to this preview: base plus exceptions as a whole. `null` removes the override, after "
        "which the preview follows the site again."
    ): (
        "Toegang die alleen voor deze preview geldt: basis plus uitzonderingen in hun geheel. `null` haalt de "
        "uitzondering weg, waarna de preview de site weer volgt."
    ),
    "Separate access settings for a preview, or `null` to turn it off.": (
        "Een afwijkende toegang voor een preview, of `null` om die af te zetten."
    ),
    "Version that is on this preview.": "Versie die op deze preview staat.",
    "Access that applies only to this preview; `null` means: follows the site.": (
        "Toegang die alleen voor deze preview geldt; `null` betekent: volgt de site."
    ),
    "Time of the last deploy to this preview.": "Tijdstip van de laatste deploy naar deze preview.",
    (
        "Time at which the cleanup job discards this preview. Every new deploy to the same ref pushes it forward; "
        "`null` means it does not expire automatically."
    ): (
        "Tijdstip waarop de opruimjob deze preview weggooit. Elke nieuwe deploy naar dezelfde ref schuift het vooruit; "
        "`null` betekent dat hij niet automatisch verloopt."
    ),
    "Path of the preview on the content origin from `contentBaseUrl`.": (
        "Pad van de preview op de content-origin uit `contentBaseUrl`."
    ),
    "A preview: a temporary copy of a site alongside the live version.": (
        "Een preview: een tweede, tijdelijke uitgave van een site naast de live versie."
    ),
    (
        "Role within a group or within a site.\n"
        "\n"
        "A reader looks on, an editor changes content, an admin changes policy: who\n"
        "may look, who may join in, and what disappears for good. The interface\n"
        "labels these lezer, redacteur and beheerder."
    ): (
        "Rol binnen een groep of binnen een site.\n"
        "\n"
        "Een lezer kijkt mee, een redacteur wijzigt content, een beheerder wijzigt het beleid: wie\n"
        "mag kijken, wie mag meedoen, en wat voorgoed verdwijnt. De interface noemt deze\n"
        "lezer, redacteur en beheerder."
    ),
    (
        "`true` (the default) serves the content with a CSP sandbox without `allow-same-origin`, so the page gets an "
        "opaque origin: it cannot read any other site on this hostname, receives no cookies and cannot store anything "
        "in the browser. Its own styles, scripts, images and fonts load as usual. `false` puts the page back on the "
        "shared origin, needed for a site that uses `localStorage`, `sessionStorage` or a cookie."
    ): (
        "`true` (de standaard) serveert de content met een CSP-sandbox zonder `allow-same-origin`, waardoor de pagina "
        "een opaque origin krijgt: hij kan geen enkele andere site op deze hostnaam lezen, krijgt geen cookies mee en "
        "kan niets in de browser bewaren. Eigen stijlen, scripts, afbeeldingen en lettertypen laden gewoon. `false` "
        "zet de pagina terug op de gedeelde herkomst, nodig voor een site die `localStorage`, `sessionStorage` of een "
        "cookie gebruikt."
    ),
    "Whether the content of this site is isolated from the other sites.": (
        "Of de content van deze site afgeschermd wordt van de andere sites."
    ),
    "Display name of the site.": "Weergavenaam van de site.",
    "Slug of the site: lowercase letters, digits and hyphens, unique within the group.": (
        "Slug van de site: kleine letters, cijfers en koppeltekens, uniek binnen de groep."
    ),
    (
        "Optional: the access the site starts with. Every omitted field, or the whole object omitted, inherits the "
        "default access of the group, so `{\"keys\": true}` only turns on secret links on top of what the group "
        "already prescribes."
    ): (
        "Optioneel: de toegang waarmee de site begint. Elk weggelaten veld, of het hele object weggelaten, neemt de "
        "standaardtoegang van de groep over, dus `{\"keys\": true}` zet alleen geheime links aan bovenop wat de groep "
        "al voorschrijft."
    ),
    "A new site within a group.": "Een nieuwe site binnen een groep.",
    (
        "`true`: from now on only a CI ID token whose audience names the id of this site may publish. `false` is "
        "refused once that holds: a link that requires the site id keeps requiring it."
    ): (
        "`true`: vanaf nu mag alleen een CI-ID-token publiceren waarvan de audience het id van deze site noemt. "
        "`false` wordt geweigerd zodra dat geldt: een koppeling die het site-ID vraagt, blijft het vragen."
    ),
    "Whether the linked repository may publish only with a CI ID token bound to this site.": (
        "Of de gekoppelde repository alleen mag publiceren met een CI-ID-token dat aan deze site gebonden is."
    ),
    (
        "Role this member gets on this site: `reader` (views), `editor` (publishes) or `admin` (determines access, "
        "invitees, secret links and who has a role on the site). A higher role can do everything a lower role can. "
        "Omitted means `reader`. The role only widens; someone with a higher group role keeps that."
    ): (
        "Rol die dit lid op deze site krijgt: `reader` (leest mee), `editor` (publiceert) of `admin` (bepaalt toegang, "
        "genodigden, geheime links en wie een rol op de site heeft). Een ruimere rol kan alles wat een smallere rol "
        "kan. Weggelaten betekent `reader`. De rol verbreedt alleen; iemand met een ruimere groepsrol houdt die."
    ),
    "A person who gets a role on this one site, in addition to what a group role already gives.": (
        "Een persoon die een rol op deze ene site krijgt, naast wat een groepsrol al geeft."
    ),
    "ID of the platform member; you use it to remove their site role.": (
        "Id van het platformlid; hiermee haal je zijn siterol weg."
    ),
    "What you use to set the site role of this member: the e-mail address.": (
        "Waarmee je de siterol van dit lid zet: het e-mailadres."
    ),
    (
        "Role in the group of this site, or `null` if this member is not a group member: `reader` (views), `editor` "
        "(publishes) or `admin` (determines access, invitees, secret links and who is in the group). A higher role can "
        "do everything a lower role can. You change this role on the group, not here."
    ): (
        "Rol in de groep van deze site, of `null` als dit lid geen groepslid is: `reader` (leest mee), `editor` "
        "(publiceert) of `admin` (bepaalt toegang, genodigden, geheime links en wie er in de groep zit). Een ruimere "
        "rol kan alles wat een smallere rol kan. Deze rol wijzig je bij de groep, niet hier."
    ),
    (
        "Role that applies only on this site, or `null` if this member has none. This is the only field that the site "
        "routes change."
    ): (
        "Rol die alleen op deze site geldt, of `null` als dit lid er geen heeft. Dit is het enige veld dat de "
        "siteroutes wijzigen."
    ),
    "What this member is actually allowed to do here: the higher of `groupRole` and `siteRole`.": (
        "Wat dit lid hier werkelijk mag: de ruimste van `groupRole` en `siteRole`."
    ),
    "A row of the member list of a site: everyone who can reach this site.": (
        "Een rij van de ledenlijst van een site: iedereen die bij deze site kan."
    ),
    (
        "Fixed id of the site. A workflow names it as `site-id`, so a deploy can only land on this site, whatever its "
        "address."
    ): (
        "Vast id van de site. Een workflow noemt het als `site-id`, zodat een deploy alleen op deze site terecht kan "
        "komen, wat haar adres ook is."
    ),
    "Slug of the site; the second path segment of the site URL.": (
        "Slug van de site; het tweede padsegment van de site-URL."
    ),
    "Who may see the live content: base plus exceptions.": "Wie de live content mag zien: basis plus uitzonderingen.",
    (
        "Whether the content of this site may load scripts, styles and fonts from a fixed list of external hosts. "
        "`true` by default; turning it off is an extra restriction."
    ): (
        "Of de content van deze site scripts, stijlen en lettertypen van een vaste lijst externe hosts mag laden. "
        "Standaard `true`; uitzetten is een extra beperking."
    ),
    (
        "Whether the content of this site is isolated from the other sites on the same hostname. `true` by default; "
        "turning it off is needed for a site that stores something in the browser."
    ): (
        "Of de content van deze site afgeschermd wordt van de andere sites op dezelfde hostnaam. Standaard `true`; "
        "uitzetten is nodig voor een site die iets in de browser bewaart."
    ),
    "Version that is currently on the public URL, or `null` if nothing is live yet.": (
        "Versie die nu op de publieke URL staat, of `null` als er nog niets live is."
    ),
    (
        "Site-specific number of previous live versions that this site keeps, or `null` if the site follows the "
        "platform default. `0` keeps all live versions. The number that currently applies is at `GET "
        "/sites/{groupSlug}/{siteSlug}/storage`."
    ): (
        "Eigen aantal vorige live-versies dat deze site bewaart, of `null` als de site de standaard van het platform "
        "volgt. `0` bewaart alle live-versies. Het aantal dat nu geldt staat op `GET "
        "/sites/{groupSlug}/{siteSlug}/storage`."
    ),
    "ID of the member who created the site; empty if that member has since been deleted.": (
        "Id van het lid dat de site aanmaakte; leeg als dat lid inmiddels verwijderd is."
    ),
    "Shorter form of `liveVersionId is not null`, handy in lists.": (
        "Kortere vorm van `liveVersionId is not null`, handig in lijsten."
    ),
    "Time of the most recent deploy, live or preview; `null` if nothing has been deployed yet.": (
        "Tijdstip van de meest recente deploy, live of preview; `null` als er nog niets is gedeployd."
    ),
    "Number of previews that currently exist for this site.": "Aantal previews dat nu voor deze site bestaat.",
    "A site with the summary the SPA shows in lists.": "Een site met de samenvatting die de SPA in lijsten toont.",
    "`github` or `forgejo`.": "`github` of `forgejo`.",
    (
        "Base URL of the Forgejo instance, one of `ciForgejoHosts` from `GET /me`. Required for `forgejo`; omit for "
        "`github` (or `https://github.com`)."
    ): (
        "Basis-URL van de Forgejo-instantie, een van `ciForgejoHosts` uit `GET /me`. Verplicht bij `forgejo`; bij "
        "`github` weglaten (of `https://github.com`)."
    ),
    "Owner of the repository: user or organisation.": "Eigenaar van de repository: gebruiker of organisatie.",
    "Name of the repository.": "Naam van de repository.",
    (
        "The only branch allowed to publish live, without `refs/heads/`. `null` or empty: any branch may go live. Live "
        "is in any case only possible from `push`, `workflow_dispatch` or `schedule`. Previews and their cleanup are "
        "always allowed from any branch."
    ): (
        "De enige branch die live mag publiceren, zonder `refs/heads/`. `null` of leeg: elke branch mag live. Live "
        "gaat hoe dan ook alleen vanuit `push`, `workflow_dispatch` of `schedule`. Previews en het opruimen ervan "
        "mogen altijd vanaf elke branch."
    ),
    (
        "Numeric ID of the repository, only needed if Plak cannot look it up because it is private. Together with "
        "`ownerId`, or omit both. Can be retrieved with `gh api repos/{owner}/{repo} --jq '.id, .owner.id'`."
    ): (
        "Numeriek id van de repository, alleen nodig als Plak haar niet kan opzoeken omdat ze privé is. Samen met "
        "`ownerId`, of allebei weglaten. Op te vragen met `gh api repos/{owner}/{repo} --jq '.id, .owner.id'`."
    ),
    "Numeric ID of the owner, together with `repositoryId`.": "Numeriek id van de eigenaar, samen met `repositoryId`.",
    (
        "Fixed id of the site. A workflow names it as `site-id` (action) or `--site-id` (CLI); the CI ID token then "
        "names it in its audience."
    ): (
        "Vast id van de site. Een workflow noemt het als `site-id` (action) of `--site-id` (CLI); het CI-ID-token "
        "noemt het dan in zijn audience."
    ),
    "Base URL of the provider.": "Basis-URL van de provider.",
    "Owner as the provider spells it.": "Eigenaar zoals de provider hem spelt.",
    "Repository as the provider spells it.": "Repository zoals de provider haar spelt.",
    "Numeric ID of the repository at the provider; survives a rename.": (
        "Numeriek id van de repository bij de provider; overleeft een hernoeming."
    ),
    "Numeric ID of the owner at the provider.": "Numeriek id van de eigenaar bij de provider.",
    "The only branch allowed to publish live, or `null`: any branch.": (
        "De enige branch die live mag publiceren, of `null`: elke branch."
    ),
    (
        "Whether the IDs are confirmed: by the provider when linking, or by a CI ID token that carried both of them. "
        "`false` while they have only been entered by hand; the name may then also still differ."
    ): (
        "Of de ids bevestigd zijn: door de provider bij het koppelen, of door een CI-ID-token dat ze allebei droeg. "
        "`false` zolang ze alleen zijn zoals ze zijn ingevuld; de naam kan dan ook nog afwijken."
    ),
    (
        "Whether only a CI ID token bound to this site may publish: its audience is the admin URL followed by "
        "`/-/sites/` and `siteId`. `true` for every link made since the site id exists, for one that got another "
        "repository since, and for an older link of a repository that was linked to several sites; `false` for any "
        "other older link, which also accepts a token whose audience is the admin URL itself until a site admin "
        "requires the site id "
        "(`PUT /sites/{groupSlug}/{siteSlug}/repository/site-id-required`). Once `true`, it stays `true`."
    ): (
        "Of alleen een CI-ID-token dat aan deze site gebonden is mag publiceren: de audience is dan de beheer-URL "
        "gevolgd door `/-/sites/` en `siteId`. `true` voor elke koppeling die is gemaakt sinds het site-ID bestaat, "
        "voor een koppeling die sindsdien een andere repository kreeg, en voor een oudere koppeling van een repository "
        "die aan meerdere sites gekoppeld was; `false` voor elke andere oudere koppeling, die ook een token met de "
        "beheer-URL zelf als audience accepteert tot een sitebeheerder het site-ID verplicht maakt "
        "(`PUT /sites/{groupSlug}/{siteSlug}/repository/site-id-required`). Eenmaal `true`, blijft het `true`."
    ),
    "Name or e-mail address of the member who linked the repository; empty if that member has been deleted.": (
        "Naam of e-mailadres van wie de koppeling maakte; leeg als dat lid verwijderd is."
    ),
    "Time of linking.": "Tijdstip van koppelen.",
    "The linked repository of a site.": "De gekoppelde repository van een site.",
    (
        "Role this member gets on this site: `reader` (views), `editor` (publishes) or `admin` (determines access, "
        "invitees, secret links and who has a role on the site). A higher role can do everything a lower role can. A "
        "role lower than the group role changes nothing: the higher of the two applies."
    ): (
        "Rol die dit lid op deze site krijgt: `reader` (leest mee), `editor` (publiceert) of `admin` (bepaalt toegang, "
        "genodigden, geheime links en wie een rol op de site heeft). Een ruimere rol kan alles wat een smallere rol "
        "kan. Een smallere rol dan de groepsrol verandert niets: de ruimste van de twee blijft gelden."
    ),
    "The new site role of a member.": "De nieuwe siterol van een lid.",
    "What all versions of this site together take up on the content volume, live and preview, in bytes.": (
        "Wat alle versies van deze site samen innemen op het contentvolume, live en preview, in bytes."
    ),
    (
        "How much all versions of a site together may take up, in bytes. A deploy that would go over that gets 413 "
        "(`SITE_QUOTA_EXCEEDED`). `0` means no limit."
    ): (
        "Hoeveel alle versies van een site samen mogen innemen, in bytes. Een deploy die daar overheen zou gaan krijgt "
        "413 (`SITE_QUOTA_EXCEEDED`). `0` betekent geen limiet."
    ),
    (
        "How many previous live versions of this site are kept in addition to the current one: the site's own setting, "
        "or else the platform default. Older live versions are removed by the nightly cleanup, row and files. `0` "
        "means all versions are kept."
    ): (
        "Hoeveel vorige live-versies van deze site naast de huidige bewaard blijven: het eigen aantal van de site, of "
        "anders de standaard van het platform. Oudere live-versies ruimt de nachtelijke opschoning op, rij en "
        "bestanden. `0` betekent dat alle versies blijven."
    ),
    "`true` if the site follows the platform default, `false` if a site admin set a custom number.": (
        "`true` als de site de standaard van het platform volgt, `false` als een sitebeheerder een eigen aantal "
        "instelde."
    ),
    (
        "The platform default: how many previous live versions a site without a custom number keeps. `0` means such "
        "sites keep all versions."
    ): (
        "De standaard van het platform: hoeveel vorige live-versies een site zonder eigen aantal bewaart. `0` betekent "
        "dat zulke sites alle versies bewaren."
    ),
    "What a site takes up on the content volume, and how many live versions are kept.": (
        "Wat een site op het contentvolume inneemt, en hoeveel live-versies er bewaard blijven."
    ),
    "`device_code` after approval, `refresh_token` to refresh.": (
        "`device_code` na het goedkeuren, `refresh_token` om te verversen."
    ),
    "The `deviceCode`, for `grantType` `device_code`.": "De `deviceCode`, bij `grantType` `device_code`.",
    "The latest refresh token, for `grantType` `refresh_token`.": (
        "Het laatste verversingstoken, bij `grantType` `refresh_token`."
    ),
    "Exchange a device code, or a refresh token.": "Een apparaatcode inwisselen, of een verversingstoken.",
    "Access token `plakcli_...` for `Authorization: Bearer`.": (
        "Toegangstoken `plakcli_...` voor `Authorization: Bearer`."
    ),
    (
        "Refresh token `plakclr_...`. Single use: presenting a refresh token that has already been used revokes the "
        "entire CLI session."
    ): (
        "Verversingstoken `plakclr_...`. Eenmalig: wie een al gebruikt verversingstoken nog eens aanbiedt, trekt "
        "daarmee de hele CLI-sessie in."
    ),
    "Always `Bearer`.": "Altijd `Bearer`.",
    "Seconds until the access token expires.": "Seconden tot het toegangstoken verloopt.",
    "The member on whose behalf the CLI now acts.": "Het lid namens wie de CLI nu handelt.",
    "A fresh set of tokens. The refresh token is invalid after use: always keep the new one.": (
        "Een verse set tokens. Het verversingstoken is na gebruik ongeldig: bewaar steeds het nieuwe."
    ),
    "The user code, with or without hyphen, case-insensitive.": (
        "De gebruikerscode, met of zonder koppelteken, hoofdletterongevoelig."
    ),
    "The code from the terminal of `plak login`.": "De code uit de terminal van `plak login`.",
    "Internal ID of the version; you use it to roll back.": "Interne id van de versie; hiermee rol je terug.",
    "`live` for the public URL, `preview` for a preview ref.": (
        "`live` voor de publieke URL, `preview` voor een preview-ref."
    ),
    "Internal reference to the unpacked file tree on disk.": (
        "Interne verwijzing naar de uitgepakte bestandsboom op schijf."
    ),
    (
        "`upload` if a member published this version themselves (in the admin interface or with the CLI), `action` if "
        "CI published it from the linked repository."
    ): (
        "`upload` als een lid deze versie zelf publiceerde (in het beheer of met de CLI), `action` als CI hem "
        "publiceerde vanuit de gekoppelde repository."
    ),
    "Member who deployed, or `null` for a deploy from CI.": "Lid dat deployde, of `null` bij een deploy vanuit CI.",
    (
        "Name of the member who deployed, or their e-mail address if the identity provider sent no name. `null` for a "
        "deploy from CI. Without this field a list only has the internal ID to show, and that tells a reader nothing."
    ): (
        "Naam van het lid dat deployde, of zijn e-mailadres als de identity provider geen naam stuurde. `null` bij een "
        "deploy vanuit CI. Zonder dit veld heeft een lijst alleen het interne id om mee te tonen, en dat zegt een "
        "lezer niets."
    ),
    "Repository from which CI published this version, as host plus `owner/repo`; `null` for a deploy by a member.": (
        "Repository waaruit CI deze versie publiceerde, als host plus `eigenaar/repo`; `null` bij een deploy door een "
        "lid."
    ),
    "Time of the deploy.": "Tijdstip van de deploy.",
    "Whether this version is currently the live version of the site.": (
        "Of deze versie op dit moment de live versie van de site is."
    ),
    "A deployed version: an unpacked bundle that can be live or attached to a preview.": (
        "Een gedeployde versie: een uitgepakte bundel die live kan staan of aan een preview kan hangen."
    ),
    "Size of the content volume in bytes.": "Grootte van het contentvolume in bytes.",
    "Bytes in use on the volume.": "Bytes in gebruik op het volume.",
    "Bytes still free on the volume.": "Bytes die nog vrij zijn op het volume.",
    (
        "Free space the volume must keep (`PLAK_STORAGE_MIN_FREE_BYTES`); below that a deploy is refused. `0` means "
        "that check is off."
    ): (
        "Vrije ruimte die het volume moet houden (`PLAK_STORAGE_MIN_FREE_BYTES`); daaronder wordt een deploy "
        "geweigerd. `0` betekent dat die controle uit staat."
    ),
    (
        "Largest unpacked size of one deploy (`PLAK_INGEST_MAX_TOTAL`). If free space on the volume is less than "
        "`reserveBytes` plus this number, a deploy of maximum size is no longer possible."
    ): (
        "Grootste uitgepakte omvang van één deploy (`PLAK_INGEST_MAX_TOTAL`). Is het vrije volume kleiner dan "
        "`reserveBytes` plus dit getal, dan kan een deploy van maximale omvang niet meer."
    ),
    "How full the content volume is.": "Hoe vol het contentvolume is.",
    "The member on whose behalf the CLI acts.": "Het lid namens wie de CLI handelt.",
    (
        "Time at which the CLI session expires if it is no longer refreshed: 30 days after the last refresh, and never "
        "later than 90 days after sign-in."
    ): (
        "Tijdstip waarop de CLI-sessie verloopt als hij niet meer ververst wordt: 30 dagen na het laatste verversen, "
        "en nooit later dan 90 dagen na het koppelen."
    ),
    "The CLI's member and session expiry.": "Wie de CLI is, en tot wanneer.",
    "Error message following RFC 9457 (`application/problem+json`), with `code` as an extension.": (
        "Foutbericht volgens RFC 9457 (`application/problem+json`), met `code` als extensie."
    ),
    "Error type URI. Plak always uses `about:blank`; `code` carries the machine-readable meaning.": (
        "Fouttype-URI. Plak gebruikt altijd `about:blank`; `code` draagt de machineleesbare betekenis."
    ),
    "Short, fixed description of the status code, for example 'Forbidden'.": (
        "Korte, vaste omschrijving van de statuscode, bijvoorbeeld 'Geen toegang'."
    ),
    "The HTTP status code, repeated in the message.": "De HTTP-statuscode, herhaald in het bericht.",
    "Explanation of this one occurrence, meant for a human. Never contains internal details.": (
        "Toelichting op dit ene voorval, bedoeld voor een mens. Bevat nooit interne details."
    ),
    (
        "Extension member (RFC 9457): stable, machine-readable error code such as `NOT_GROUP_MEMBER` or `SLUG_EXISTS`. "
        "Absent for errors that FastAPI handles itself, such as a schema validation."
    ): (
        "Extensielid (RFC 9457): stabiele, machineleesbare foutcode zoals `NOT_GROUP_MEMBER` of `SLUG_EXISTS`. "
        "Ontbreekt bij fouten die FastAPI zelf afhandelt, zoals een schemavalidatie."
    ),
    (
        "Extension member (RFC 9457): with `NO_INDEX`, `BASE_PATH_WITHOUT_INDEX`, `BASE_PATH_UNKNOWN` and "
        "`TOO_MANY_FILES`, the `index.html` paths found in the bundle, shortest first and at most five. The paths are "
        "relative to the root directory after unwrapping, so the directory of such a path is exactly the value that "
        "can be sent back as the `basePath` form field. If a path contains no `/`, that `index.html` is already in the "
        "root directory: there is no directory to send back, and the fix is to omit `basePath`."
    ): (
        "Extensielid (RFC 9457): bij `NO_INDEX`, `BASE_PATH_WITHOUT_INDEX`, `BASE_PATH_UNKNOWN` en `TOO_MANY_FILES` de "
        "`index.html`-paden die in de bundel gevonden zijn, kortste eerst en hoogstens vijf. De paden staan relatief "
        "aan de hoofdmap na het afpellen, dus de map van zo'n pad is precies de waarde die als formveld `basePath` "
        "teruggestuurd kan worden. Bevat een pad geen `/`, dan staat die `index.html` al in de hoofdmap: dan is er "
        "geen map om terug te sturen en is de herstelactie juist om `basePath` weg te laten."
    ),
    (
        "The admin session you get after SSO sign-in on the admin host. The browser sends the cookie by itself, so "
        "'Try it out' works here once you are signed in; the Authorize button is not needed. Mutations also require "
        "the `X-CSRF-Token` header with the value of the CSRF cookie, which this page sends along automatically."
    ): (
        "De beheersessie die je na SSO-login op de beheer-host krijgt. De browser stuurt het cookie zelf mee, dus 'Try "
        "it out' werkt hier zodra je bent ingelogd; de knop Authorize hoeft niet. Mutaties eisen daarnaast de header "
        "`X-CSRF-Token` met de waarde van het CSRF-cookie, die deze pagina automatisch meestuurt."
    ),
    (
        "A CI ID token (JWT from GitHub or Forgejo Actions, with the admin URL as audience) or a CLI token "
        "`plakcli_...` from `plak login`. Paste the whole token under Authorize. Only the deploy endpoints and the CLI "
        "session endpoints accept it, and, with a CLI token only, creating a group, creating a site and linking a "
        "repository; anywhere else a Bearer header yields 401, even when the token is valid."
    ): (
        "Een CI-ID-token (JWT van GitHub of Forgejo Actions, audience de beheer-URL) of een CLI-token `plakcli_...` "
        "uit `plak login`. Plak het hele token achter Authorize. Alleen de deploy-endpoints en de CLI-sessie-endpoints "
        "accepteren het, en, alleen met een CLI-token, het aanmaken van een groep, het aanmaken van een site en het "
        "koppelen van een repository; elders levert een Bearer-header 401, ook als het token geldig is."
    ),
}
