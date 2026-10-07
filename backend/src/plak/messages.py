"""The texts of problem+json answers, in Dutch and in English.

The API answers with `{type, title, status, detail, code}`. The `code` is the
contract: a client branches on it, and it never changes with the language.
`title` and `detail` are presentation, and they live here rather than at the
raise site, so that the same refusal can be told in either language.

A raise site names a key and the values that go into it; the error handler
renders once, in the language the request asked for (api/errors.py). A key is
usually the code itself (`UNKNOWN_SITE`). Where one code covers refusals that
deserve different wording, the key gains a lowercase suffix
(`MULTIPART_INVALID.incomplete`); the code is what stands before the dot, so
the contract does not grow a variant.

A key that starts lowercase is a fragment (`suggestion.nearest`): a sentence
that only ever appears inside another message, never a code of its own.
Fragments travel as a `Msg` in the parameters of the message that holds them,
so that a composed message is composed in one language.

NL is the reference. The two catalogues are held to the same keys and the
same parameters per key at import time, and test_messages.py holds them to
the raise sites: a code that exists in one language only, or a message that
no longer matches what the code raises, is a failing test rather than a Dutch
sentence in an English answer.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from string import Formatter
from typing import Final

from plak import i18n

# Every status code the API answers with; see api/errors.py.
TITLES_NL: Final[dict[int, str]] = {
    400: "Ongeldig verzoek",
    401: "Niet geauthenticeerd",
    403: "Geen toegang",
    404: "Niet gevonden",
    409: "Conflict",
    413: "Inhoud te groot",
    422: "Onverwerkbare invoer",
    429: "Te veel verzoeken",
    503: "Niet beschikbaar",
}

TITLES_EN: Final[dict[int, str]] = {
    400: "Bad request",
    401: "Not authenticated",
    403: "Forbidden",
    404: "Not found",
    409: "Conflict",
    413: "Content too large",
    422: "Unprocessable input",
    429: "Too many requests",
    503: "Unavailable",
}

# A status that is not in the table at all (never raised by Plak itself, but
# Starlette can produce one) still needs a title.
FALLBACK_TITLE_NL: Final[str] = "Fout"
FALLBACK_TITLE_EN: Final[str] = "Error"

NL: Final[dict[str, str]] = {
    # -- Session, member, origin ---------------------------------------------
    "NO_SESSION": "Geen geldige sessie.",
    "NO_SESSION.not_logged_in": "Niet ingelogd.",
    "SESSION_NOT_FRESH": (
        "Log opnieuw in om een apparaat te koppelen; je sessie is ouder dan een kwartier."
    ),
    "MEMBER_DEACTIVATED": "Je toegang is ingetrokken door een platformbeheerder.",
    "MEMBER_NOT_ACTIVE": "Geen actief lid.",
    "CSRF_INVALID": "CSRF-token ontbreekt of is ongeldig.",
    "ORIGIN_REFUSED": "Verzoek komt niet van de beheer-origin.",
    "NOT_ADMIN": "Alleen een platformbeheerder mag dit.",
    "INSUFFICIENT_ROLE": "Hiervoor heb je minimaal de rol {role} nodig.",
    "NO_AUTHENTICATION": "Geen geldige sessie of bearer-token.",
    "BEARER_NOT_ACCEPTED": (
        "Bearer-authenticatie wordt alleen op de deploy- en CLI-endpoints, bij het aanmaken van "
        "een groep of site en bij het koppelen van een repository geaccepteerd."
    ),
    # -- Groups, sites, members ----------------------------------------------
    "UNKNOWN_GROUP": "Onbekende groep.",
    "UNKNOWN_SITE": "Onbekende site.",
    "UNKNOWN_SITE.deploy": "Onbekende groep of site.",
    "UNKNOWN_MEMBER": "Onbekend lid.",
    "UNKNOWN_MEMBER.must_sign_in": "Onbekend lid; diegene moet eerst zelf inloggen op het beheer.",
    "UNKNOWN_INVITEE": "Onbekende genodigde.",
    "UNKNOWN_KEY": "Onbekende sleutel.",
    "UNKNOWN_PREVIEW": "Onbekende preview.",
    "UNKNOWN_ASSET": "Onbekend docs-asset.",
    "CLI_SESSION_UNKNOWN": "Onbekende CLI-sessie.",
    "USER_CODE_UNKNOWN": "Deze code is onbekend, verlopen of al gebruikt. Start plak login opnieuw.",
    "NOT_GROUP_MEMBER": "Dit lid zit niet in de groep.",
    "NOT_GROUP_MEMBER.you": "Je bent geen lid van deze groep.",
    "NOT_SITE_MEMBER": "Dit lid heeft geen rol op deze site.",
    "ALREADY_GROUP_MEMBER": "Dit lid zit al in de groep.",
    "ALREADY_SITE_MEMBER": "Dit lid heeft al een eigen rol op deze site.",
    "SLUG_EXISTS.group": "Er bestaat al een groep met slug {slug!r}.",
    "SLUG_EXISTS.site": "Er bestaat al een site met slug {slug!r} in deze groep.",
    "SLUG_INVALID": "Slug is ongeldig of gereserveerd: {slug!r}.",
    "FIELD_EMPTY": "Veld {field!r} mag niet leeg zijn.",
    "FIELD_CONTROL_CHARACTERS": "Veld {field!r} mag geen stuur- of opmaaktekens bevatten.",
    "FIELD_TOO_LONG": "Veld {field!r} mag hoogstens {max} tekens lang zijn.",
    "INVITEE_EXISTS": "{identifier!r} staat al op de genodigdenlijst.",
    "SELF_NOT_ALLOWED.deactivate": (
        "Je kunt jezelf niet deactiveren. Laat een andere platformbeheerder dat doen."
    ),
    "SELF_NOT_ALLOWED.demote": (
        "Je kunt jezelf niet de beheerdersrol afnemen. Laat een andere platformbeheerder dat doen."
    ),
    "BOOTSTRAP_MEMBER": (
        "Dit is het bootstrap-account uit PLAK_BOOTSTRAP_ADMIN_SUB. Het wordt bij elke login "
        "hersteld, dus wijzig die instelling in plaats van dit lid."
    ),
    "LAST_PLATFORM_ADMIN": "Dit is de laatste actieve platformbeheerder. Wijs eerst iemand anders aan.",
    "LAST_GROUP_ADMIN": "Dit is de laatste beheerder van de groep. Wijs eerst iemand anders aan.",
    "LAST_GROUP_MEMBER": "Dit is het laatste lid van de groep. Voeg eerst iemand anders toe.",
    # -- Audit lookups --------------------------------------------------------
    "AUDIT_UNAVAILABLE": "Het auditlog is niet beschikbaar.",
    "AUDIT_IP_UNKNOWN": "Geen auditregel met dit id en een versleuteld IP-adres.",
    "LOOKUP_LIMIT_REACHED": "De dagelijkse limiet voor herleidingen is bereikt.",
    "SEARCH_TOO_SHORT": "Zoek op minstens {minimum} tekens.",
    "CURSOR_INVALID": "De cursor is onleesbaar; vraag de eerste pagina opnieuw op.",
    "ACTOR_PSEUDONYM_INVALID": "Een auditpseudoniem is {length} hexadecimale tekens.",
    "PSEUDONYM_UNKNOWN": "Geen lid, content-viewer of gekoppelde repository gevonden voor dit pseudoniem.",
    "IDENTIFIER_UNKNOWN": "Geen lid of content-viewer met dit e-mailadres.",
    "IDENTIFIER_AMBIGUOUS.email": "Meerdere personen met dit e-mailadres; gebruik het SSO-subject.",
    "IDENTIFIER_AMBIGUOUS.member_email": "Meerdere leden met dit e-mailadres; gebruik het SSO-subject.",
    "IDENTIFIER_AMBIGUOUS.member_name": "Meerdere leden met deze naam; gebruik het e-mailadres.",
    "IDENTIFIER_AMBIGUOUS.repository": (
        "Deze repository is op meer dan één provider gekoppeld; noem de host erbij."
    ),
    # -- Repositories and CI --------------------------------------------------
    "REPOSITORY_NOT_SET": "Aan deze site is geen repository gekoppeld.",
    "REPOSITORY_INVALID": "Eigenaar of repository is geen geldige naam.",
    "REPOSITORY_NOT_FOUND": (
        "Repository {owner}/{repo} niet gevonden op {host}, of niet openbaar. Is ze privé, vul dan het "
        "repository-id en het eigenaar-id zelf in."
    ),
    "REPOSITORY_IDS_INVALID": "Vul het repository-id en het eigenaar-id allebei in, als positief geheel getal.",
    "REPOSITORY_IDS_MISMATCH": (
        "{host} geeft {owner}/{repo} andere ids dan ingevuld. Laat de ids leeg, of neem ze over van {host}."
    ),
    "LIVE_BRANCH_INVALID": "Deze live-branch is geen geldige branchnaam.",
    "SITE_ID_REQUIRED_PERMANENT": "Is het site-ID eenmaal verplicht, dan blijft het verplicht.",
    "LIVE_VERSIONS_KEPT_INVALID": (
        "Het aantal bewaarde vorige versies moet een geheel getal van 0 of meer zijn."
    ),
    "LIVE_VERSIONS_KEPT_TOO_LARGE": "Dit getal is te groot om op te slaan. Kies een kleiner aantal.",
    "HOST_NOT_ALLOWED": "Deze Forgejo-instantie staat niet in PLAK_CI_FORGEJO_HOSTS.",
    "HOST_NOT_ALLOWED.github": "Bij GitHub hoort geen andere host.",
    "CI_PROVIDER_RATE_LIMITED": "{host} laat even geen opzoekingen meer toe; probeer het later opnieuw.",
    "CI_PROVIDER_UNREACHABLE.lookup": "{host} is niet bereikbaar; probeer het later opnieuw.",
    "CI_PROVIDER_UNREACHABLE.keys": (
        "De sleutels van de CI-provider zijn niet op te halen; probeer het later opnieuw."
    ),
    "CI_PROVIDER_UNREACHABLE.repository": (
        "Forgejo is niet bereikbaar om de repository te controleren; probeer het later opnieuw."
    ),
    "CI_TOKEN_INVALID.not_a_jwt": "Het CI-token is geen geldig JWT.",
    "CI_TOKEN_INVALID.algorithm": "Het CI-token moet met RS256 ondertekend zijn.",
    "CI_TOKEN_INVALID.no_kid": "Het CI-token noemt geen sleutel (kid).",
    "CI_TOKEN_INVALID.unknown_key": "Het CI-token is ondertekend met een onbekende sleutel.",
    "CI_TOKEN_INVALID.expired": "Het CI-token is ongeldig of verlopen.",
    "CI_TOKEN_INVALID.no_lifetime": "Het CI-token mist exp of iat.",
    "CI_ISSUER_UNKNOWN": (
        "Het CI-token komt niet van GitHub of van een geconfigureerde Forgejo-instantie."
    ),
    "CI_ISSUER_UNKNOWN.mismatch": "De issuer van het CI-token klopt niet.",
    "CI_AUDIENCE_MISMATCH": (
        "De audience van het CI-token moet precies de beheer-URL van Plak zijn ({audience}), of voor "
        "één site {audience}/-/sites/ met het site-ID erachter."
    ),
    "CI_AUDIENCE_MISMATCH.no_base_url": (
        "De audience van het CI-token moet precies de beheer-URL van Plak zijn "
        "(PLAK_BASE_URL is niet ingesteld)."
    ),
    "CI_AUDIENCE_MISMATCH.site": (
        "De repository van dit ID-token is niet gekoppeld aan de site waar het site-ID bij hoort."
    ),
    "CI_REPOSITORY_NOT_TRUSTED": (
        "Deze repository mag niet naar deze site publiceren. Koppel haar eerst bij de site in het beheer."
    ),
    "CI_BRANCH_NOT_ALLOWED.event": (
        "Live publiceren mag alleen vanuit een push, een handmatige run (workflow_dispatch) of een "
        "schedule; publiceer vanuit dit event als preview."
    ),
    "CI_BRANCH_NOT_ALLOWED.branch": (
        "Alleen de branch {branch} mag live publiceren; publiceer vanaf deze ref als preview."
    ),
    "CI_SITE_ID_REQUIRED": (
        "Deze site accepteert alleen een workflow die haar site-ID noemt. Zet `site-id` (action) of "
        "`--site-id` (CLI) in de workflow, met het ID van het tabblad Deploy van je site in Plak."
    ),
    "SITE_MOVED": (
        "Deze workflow publiceert naar {address}. Gebruik dat adres als site: `site:` in de action, "
        "`--site` bij de CLI."
    ),
    # -- Deploy tokens, keys, expiry -----------------------------------------
    "TOKEN_INVALID": "Het token is ongeldig, ingetrokken of verlopen.",
    "TOKEN_INVALID.missing": "Het token ontbreekt.",
    "TOKEN_INVALID.cli_only": "Hier wordt alleen een CLI-token uit 'plak login' geaccepteerd.",
    "EXPIRY_IN_PAST": "De vervaldatum ligt in het verleden.",
    "EXPIRY_TOO_FAR": "De vervaldatum mag hoogstens {days} dagen vooruit liggen.",
    # -- CLI login ------------------------------------------------------------
    "TOO_MANY_ATTEMPTS": "Te veel pogingen; wacht een paar minuten.",
    "TOO_MANY_CREATIONS": "Hoogstens {limit} nieuwe groepen en sites per uur; probeer het later opnieuw.",
    "TOO_MANY_REQUESTS": "Te veel verzoeken. Probeer het over enkele ogenblikken opnieuw.",
    "TOO_MANY_REQUESTS.cli_login": "Te veel inlogpogingen vanaf dit adres; probeer het later opnieuw.",
    "INVALID_GRANT.device_code_missing": "`deviceCode` ontbreekt.",
    "INVALID_GRANT.device_code_unknown": "Onbekende of al gebruikte apparaatcode.",
    "INVALID_GRANT.refresh_token_missing": "`refreshToken` ontbreekt.",
    "INVALID_GRANT.refresh_token_unknown": "Onbekend of ingetrokken verversingstoken.",
    "INVALID_GRANT.refresh_token_rotated": (
        "Dit verversingstoken is zojuist al ververst; gebruik het nieuwe."
    ),
    "INVALID_GRANT.refresh_token_reused": (
        "Dit verversingstoken is al gebruikt; de CLI-sessie is ingetrokken."
    ),
    "INVALID_GRANT.session_expired": "De CLI-sessie is verlopen; log opnieuw in met plak login.",
    "INVALID_GRANT.member_not_active": "Het lid achter deze CLI-sessie is niet (meer) actief.",
    "EXPIRED_TOKEN.device_code": "De apparaatcode is verlopen; start het inloggen opnieuw.",
    "SLOW_DOWN": "Vraag hoogstens eens per {interval} seconden; wacht langer.",
    "AUTHORIZATION_PENDING": "Nog niet goedgekeurd in het beheer.",
    "ACCESS_DENIED": "Het koppelen is geweigerd.",
    "ACCESS_DENIED.approver_not_active": "Het lid dat dit goedkeurde is niet (meer) actief.",
    # -- Upload and multipart -------------------------------------------------
    "BODY_TOO_LARGE": "Upload groter dan {max_body} bytes.",
    "CLIENT_ABORTED": "Upload afgebroken door de client.",
    "NOT_MULTIPART": "Ongeldige invoer: {field} (multipart/form-data met een bestandsveld vereist).",
    "FILE_MISSING": "Ongeldige invoer: {field}.",
    "MULTIPART_INVALID": "Ongeldig multipart-verzoek.",
    "MULTIPART_INVALID.no_field_name": "Multipart-deel zonder veldnaam.",
    "MULTIPART_INVALID.unexpected_file_field": "Onverwacht bestandsveld {field!r}.",
    "MULTIPART_INVALID.more_than_one_file": "Meer dan één bestand in de upload.",
    "MULTIPART_INVALID.too_many_fields": "Te veel formuliervelden.",
    "MULTIPART_INVALID.field_too_large": "Formulierveld {field!r} is te groot.",
    "MULTIPART_INVALID.incomplete": "Multipart-verzoek is onvolledig.",
    "PREVIEW_REF_INVALID": "Preview-ref is geen geldige slug.",
    # -- Validation FastAPI itself signals ------------------------------------
    "INVALID_INPUT": "Ongeldige invoer: {fields}",
    "INVALID_INPUT.request": "Ongeldige invoer: verzoek",
    # -- Ingest ---------------------------------------------------------------
    "ORIGIN_INVALID": "Precies een van member_id of ci_repository moet gezet zijn.",
    "UNKNOWN_VERSION": "Versie bestaat niet.",
    "UNKNOWN_VERSION.site": "Onbekende versie voor deze site.",
    "VERSION_OTHER_SITE": "Versie hoort niet bij deze site.",
    "ROLLBACK_TARGET_PREVIEW": "Versies met doel 'preview' zijn nooit rollback-doel.",
    # -- Bundles: paths -------------------------------------------------------
    "NULL_BYTE": "Pad bevat een null-byte: {path!r}",
    "ABSOLUTE_PATH": "Absoluut pad geweigerd: {path}",
    "PATH_TRAVERSAL": "Pad met '..'-segment geweigerd: {path}",
    "EMPTY_PATH": "Leeg pad geweigerd: {path!r}",
    "TOO_DEEP": "Pad dieper dan {max_depth} niveaus: {path}",
    "DUPLICATE_PATH": "Pad komt meermaals of als map en bestand voor: {path}",
    "SECRET_FILE": (
        "'{path}' hoort niet op een website en wordt niet gepubliceerd. Publiceer de map met "
        "de gebouwde site (vaak 'dist' of 'build') in plaats van de hele projectmap, of haal "
        "'{secret}' uit de bundel."
    ),
    "RESERVED_SEGMENT": (
        "'{segment}' ligt {where} en is een gereserveerd segment; hernoem de map of het bestand, "
        "of publiceer een andere map"
    ),
    "RESERVED_SEGMENT.base_path": "'{segment}' is een gereserveerd segment",
    "where.base_path": "in de hoofdmap van basispad '{base}'",
    "where.peeled": "na het afpellen van '{peeled}' in de hoofdmap van de site",
    "where.bundle_root": "in de hoofdmap van de bundel",
    # -- Bundles: limits and archives -----------------------------------------
    "TOO_MANY_FILES": "Bundel bevat meer dan {limit} bestanden of mappen.",
    "TOO_MANY_FILES.archive_entries": (
        "Archief bevat meer dan {limit} entries; ook wat buiten de hoofdmap van de site valt telt "
        "daarvoor mee."
    ),
    "FILE_TOO_LARGE": "Bestand '{path}' is uitgepakt groter dan {max_file} bytes.",
    "TOTAL_TOO_LARGE": "Bundel is uitgepakt groter dan {max_total} bytes.",
    "TOTAL_TOO_LARGE.archive": "Archief is uitgepakt groter dan {max_total} bytes.",
    "SITE_QUOTA_EXCEEDED": (
        "Deze site gebruikt al {used} van {max_bytes} bytes; daar passen de {added} bytes van "
        "deze versie niet meer bij. Oude live-versies boven het bewaarde aantal worden 's nachts "
        "opgeruimd. Verwijder anders previews die je niet meer nodig hebt, of vraag de "
        "platformbeheerder om meer ruimte."
    ),
    "STORAGE_UNAVAILABLE": (
        "Er is nu te weinig vrije ruimte om te publiceren. Probeer het later opnieuw; de "
        "platformbeheerder is hiervan op de hoogte."
    ),
    "VOLUME_UNMEASURABLE": "De vulling van het contentvolume kan niet worden gemeten.",
    "SYMLINK_REFUSED": "Symlink in archief geweigerd: {name}",
    "HARDLINK_REFUSED": "Hardlink in archief geweigerd: {name}",
    "SPECIAL_FILE": "Geen gewoon bestand, geweigerd: {name}",
    "INVALID_ARCHIVE.zip": "Zip-archief onleesbaar: {error}",
    "INVALID_ARCHIVE.zip_entry": "Zip-entry onleesbaar: {error}",
    "INVALID_ARCHIVE.tar": "Tar.gz-archief onleesbaar: {error}",
    "INVALID_ARCHIVE.tar_entry": "Tar-entry onleesbaar: {name}",
    "INVALID_ARCHIVE.html": "Html-bestand onleesbaar: {error}",
    "UNKNOWN_FORMAT": "Alleen .html, .zip, .tar.gz of .tgz wordt geaccepteerd.",
    "EMPTY_ARCHIVE": (
        "Bundel bevat geen publiceerbare bestanden; alleen OS-metadata "
        "({junk_dir}, {junk_prefix}*, .DS_Store) telt niet mee."
    ),
    # -- Bundles: the root of the site ---------------------------------------
    "BASE_PATH_INVALID": "Basispad is ongeldig: {cause}",
    "BASE_PATH_UNKNOWN": "Basispad '{base_path}' is geen map in de bundel",
    "BASE_PATH_UNKNOWN.case_variant": (
        "Basispad '{base_path}' is geen map in de bundel; de bundel bevat wel '{variant}', en "
        "hoofdletters tellen mee"
    ),
    "BASE_PATH_UNKNOWN.peeled": (
        "Basispad '{base_path}' is geen map in de bundel; de omhullende map '{peeled}' is al "
        "afgepeld, dus een pad daarbinnen volstaat"
    ),
    "BASE_PATH_UNKNOWN.file": "Basispad '{base_path}' wijst naar een bestand, niet naar een map",
    "BASE_PATH_UNKNOWN.html_file": (
        "Een los html-bestand bevat geen mappen; laat basispad ('{base_path}') weg"
    ),
    "BASE_PATH_WITHOUT_INDEX.empty": "Basispad '{base_path}' bevat geen bestanden",
    "BASE_PATH_WITHOUT_INDEX.empty_with_suggestion": (
        "Basispad '{base_path}' bevat geen bestanden; {suggestion}"
    ),
    "BASE_PATH_WITHOUT_INDEX.no_index": "Basispad '{base_path}' bevat geen {index}",
    "BASE_PATH_WITHOUT_INDEX.no_index_with_suggestion": (
        "Basispad '{base_path}' bevat geen {index}; {suggestion}"
    ),
    "suggestion.in_root": "de {index} staat in de hoofdmap van de bundel; laat het veld basispad weg",
    "suggestion.nearest": "de dichtstbijzijnde staat op '{path}'",
    "NO_INDEX": "De bundel bevat nergens een {index}; de hoofdmap van de bundel heeft er een nodig",
    "NO_INDEX.case_variant": (
        "De bundel bevat nergens een {index}; de hoofdmap van de bundel heeft er een nodig. Let op: "
        "'{variant}' telt niet mee, want de server is hoofdlettergevoelig; hernoem het bestand "
        "naar '{index}'"
    ),
    "NO_INDEX.nearest": (
        "Geen {index} in de hoofdmap van de bundel; de dichtstbijzijnde staat op '{shortest}'. "
        "Publiceer de map '{directory}' zelf, of stuur het veld basispad mee met de waarde "
        "'{directory}'"
    ),
    # -- Examples in the OpenAPI document, one per status code ----------------
    "example.401": "Er is geen actieve sessie.",
    "example.403": "Je hebt hier geen toegang.",
    "example.404": "Dit bestaat niet, of je mag het niet zien.",
    "example.409": "Dit botst met de huidige stand.",
    "example.413": "Het verzoek is groter dan toegestaan.",
    "example.422": "De invoer klopt niet.",
    "example.429": "Te veel verzoeken; probeer het later opnieuw.",
    "example.other": "Er ging iets mis.",
}

EN: Final[dict[str, str]] = {
    # -- Session, member, origin ---------------------------------------------
    "NO_SESSION": "There is no valid session.",
    "NO_SESSION.not_logged_in": "Not signed in.",
    "SESSION_NOT_FRESH": (
        "Sign in again to link a device; your session is more than fifteen minutes old."
    ),
    "MEMBER_DEACTIVATED": "Your access has been withdrawn by a platform administrator.",
    "MEMBER_NOT_ACTIVE": "Not an active member.",
    "CSRF_INVALID": "The CSRF token is missing or invalid.",
    "ORIGIN_REFUSED": "This request does not come from the beheer origin.",
    "NOT_ADMIN": "Only a platform administrator may do this.",
    "INSUFFICIENT_ROLE": "This needs at least the role {role}.",
    "NO_AUTHENTICATION": "No valid session or bearer token.",
    "BEARER_NOT_ACCEPTED": (
        "Bearer authentication is accepted on the deploy and CLI endpoints, for creating a group or "
        "site and for linking a repository only."
    ),
    # -- Groups, sites, members ----------------------------------------------
    "UNKNOWN_GROUP": "Unknown group.",
    "UNKNOWN_SITE": "Unknown site.",
    "UNKNOWN_SITE.deploy": "Unknown group or site.",
    "UNKNOWN_MEMBER": "Unknown member.",
    "UNKNOWN_MEMBER.must_sign_in": "Unknown member; they have to sign in to beheer themselves first.",
    "UNKNOWN_INVITEE": "Unknown invitee.",
    "UNKNOWN_KEY": "Unknown key.",
    "UNKNOWN_PREVIEW": "Unknown preview.",
    "UNKNOWN_ASSET": "Unknown docs asset.",
    "CLI_SESSION_UNKNOWN": "Unknown CLI session.",
    "USER_CODE_UNKNOWN": "This code is unknown, expired or already used. Run plak login again.",
    "NOT_GROUP_MEMBER": "This member is not in the group.",
    "NOT_GROUP_MEMBER.you": "You are not a member of this group.",
    "NOT_SITE_MEMBER": "This member has no role on this site.",
    "ALREADY_GROUP_MEMBER": "This member is already in the group.",
    "ALREADY_SITE_MEMBER": "This member already has a role of their own on this site.",
    "SLUG_EXISTS.group": "A group with slug {slug!r} already exists.",
    "SLUG_EXISTS.site": "A site with slug {slug!r} already exists in this group.",
    "SLUG_INVALID": "Slug is invalid or reserved: {slug!r}.",
    "FIELD_EMPTY": "Field {field!r} must not be empty.",
    "FIELD_CONTROL_CHARACTERS": "Field {field!r} must not contain control or formatting characters.",
    "FIELD_TOO_LONG": "Field {field!r} may be at most {max} characters long.",
    "INVITEE_EXISTS": "{identifier!r} is already on the invitee list.",
    "SELF_NOT_ALLOWED.deactivate": (
        "You cannot deactivate yourself. Have another platform administrator do that."
    ),
    "SELF_NOT_ALLOWED.demote": (
        "You cannot take the administrator role away from yourself. Have another platform "
        "administrator do that."
    ),
    "BOOTSTRAP_MEMBER": (
        "This is the bootstrap account from PLAK_BOOTSTRAP_ADMIN_SUB. It is restored on every "
        "sign-in, so change that setting instead of this member."
    ),
    "LAST_PLATFORM_ADMIN": "This is the last active platform administrator. Appoint someone else first.",
    "LAST_GROUP_ADMIN": "This is the last administrator of the group. Appoint someone else first.",
    "LAST_GROUP_MEMBER": "This is the last member of the group. Add someone else first.",
    # -- Audit lookups --------------------------------------------------------
    "AUDIT_UNAVAILABLE": "The audit log is unavailable.",
    "AUDIT_IP_UNKNOWN": "No audit record with this id and an encrypted IP address.",
    "LOOKUP_LIMIT_REACHED": "The daily limit for identity lookups has been reached.",
    "SEARCH_TOO_SHORT": "Search with at least {minimum} characters.",
    "CURSOR_INVALID": "The cursor is unreadable; ask for the first page again.",
    "ACTOR_PSEUDONYM_INVALID": "An audit pseudonym is {length} hexadecimal characters.",
    "PSEUDONYM_UNKNOWN": "No member, content viewer or linked repository found for this pseudonym.",
    "IDENTIFIER_UNKNOWN": "No member or content viewer with this e-mail address.",
    "IDENTIFIER_AMBIGUOUS.email": "More than one person with this e-mail address; use the SSO subject.",
    "IDENTIFIER_AMBIGUOUS.member_email": (
        "More than one member with this e-mail address; use the SSO subject."
    ),
    "IDENTIFIER_AMBIGUOUS.member_name": (
        "More than one member with this name; use the e-mail address."
    ),
    "IDENTIFIER_AMBIGUOUS.repository": (
        "This repository is linked on more than one provider; name the host as well."
    ),
    # -- Repositories and CI --------------------------------------------------
    "REPOSITORY_NOT_SET": "No repository is linked to this site.",
    "REPOSITORY_INVALID": "Owner or repository is not a valid name.",
    "REPOSITORY_NOT_FOUND": (
        "Repository {owner}/{repo} not found on {host}, or not public. If it is private, enter the "
        "repository ID and the owner ID yourself."
    ),
    "REPOSITORY_IDS_INVALID": "Enter both the repository ID and the owner ID, as positive whole numbers.",
    "REPOSITORY_IDS_MISMATCH": (
        "{host} gives {owner}/{repo} other IDs than the ones entered. Leave the IDs empty, or copy them from "
        "{host}."
    ),
    "LIVE_BRANCH_INVALID": "This live branch is not a valid branch name.",
    "SITE_ID_REQUIRED_PERMANENT": "Once a site id is required, it stays required.",
    "LIVE_VERSIONS_KEPT_INVALID": (
        "The number of previous versions kept must be a whole number of 0 or more."
    ),
    "LIVE_VERSIONS_KEPT_TOO_LARGE": "This number is too large to store. Choose a smaller one.",
    "HOST_NOT_ALLOWED": "This Forgejo instance is not in PLAK_CI_FORGEJO_HOSTS.",
    "HOST_NOT_ALLOWED.github": "GitHub does not come with another host.",
    "CI_PROVIDER_RATE_LIMITED": "{host} is not accepting lookups right now; try again later.",
    "CI_PROVIDER_UNREACHABLE.lookup": "{host} is unreachable; try again later.",
    "CI_PROVIDER_UNREACHABLE.keys": (
        "The keys of the CI provider cannot be fetched; try again later."
    ),
    "CI_PROVIDER_UNREACHABLE.repository": (
        "Forgejo is unreachable to check the repository; try again later."
    ),
    "CI_TOKEN_INVALID.not_a_jwt": "The CI token is not a valid JWT.",
    "CI_TOKEN_INVALID.algorithm": "The CI token has to be signed with RS256.",
    "CI_TOKEN_INVALID.no_kid": "The CI token names no key (kid).",
    "CI_TOKEN_INVALID.unknown_key": "The CI token is signed with an unknown key.",
    "CI_TOKEN_INVALID.expired": "The CI token is invalid or expired.",
    "CI_TOKEN_INVALID.no_lifetime": "The CI token is missing exp or iat.",
    "CI_ISSUER_UNKNOWN": (
        "The CI token does not come from GitHub or from a configured Forgejo instance."
    ),
    "CI_ISSUER_UNKNOWN.mismatch": "The issuer of the CI token is not right.",
    "CI_AUDIENCE_MISMATCH": (
        "The audience of the CI token has to be exactly the beheer URL of Plak ({audience}), or for "
        "one site {audience}/-/sites/ followed by the site id."
    ),
    "CI_AUDIENCE_MISMATCH.no_base_url": (
        "The audience of the CI token has to be exactly the beheer URL of Plak "
        "(PLAK_BASE_URL is not set)."
    ),
    "CI_AUDIENCE_MISMATCH.site": "This ID token's repository is not linked to the site its site id names.",
    "CI_REPOSITORY_NOT_TRUSTED": (
        "This repository may not publish to this site. Link it to the site in beheer first."
    ),
    "CI_BRANCH_NOT_ALLOWED.event": (
        "Publishing live is allowed from a push, a manual run (workflow_dispatch) or a schedule "
        "only; publish from this event as a preview."
    ),
    "CI_BRANCH_NOT_ALLOWED.branch": (
        "Only the branch {branch} may publish live; publish from this ref as a preview."
    ),
    "CI_SITE_ID_REQUIRED": (
        "This site only accepts a workflow that names its site id. Add `site-id` (action) or "
        "`--site-id` (CLI) with the id from the Deploy tab of your site in Plak."
    ),
    "SITE_MOVED": (
        "This workflow publishes to {address}. Use that address as the site: `site:` in the action, "
        "`--site` for the CLI."
    ),
    # -- Deploy tokens, keys, expiry -----------------------------------------
    "TOKEN_INVALID": "The token is invalid, revoked or expired.",
    "TOKEN_INVALID.missing": "The token is missing.",
    "TOKEN_INVALID.cli_only": "Only a CLI token from 'plak login' is accepted here.",
    "EXPIRY_IN_PAST": "The expiry date is in the past.",
    "EXPIRY_TOO_FAR": "The expiry date may be at most {days} days ahead.",
    # -- CLI login ------------------------------------------------------------
    "TOO_MANY_ATTEMPTS": "Too many attempts; wait a few minutes.",
    "TOO_MANY_CREATIONS": "At most {limit} new groups and sites per hour; try again later.",
    "TOO_MANY_REQUESTS": "Too many requests. Try again in a few moments.",
    "TOO_MANY_REQUESTS.cli_login": "Too many sign-in attempts from this address; try again later.",
    "INVALID_GRANT.device_code_missing": "`deviceCode` is missing.",
    "INVALID_GRANT.device_code_unknown": "Unknown or already used device code.",
    "INVALID_GRANT.refresh_token_missing": "`refreshToken` is missing.",
    "INVALID_GRANT.refresh_token_unknown": "Unknown or revoked refresh token.",
    "INVALID_GRANT.refresh_token_rotated": (
        "This refresh token has just been refreshed; use the new one."
    ),
    "INVALID_GRANT.refresh_token_reused": (
        "This refresh token has been used before; the CLI session has been revoked."
    ),
    "INVALID_GRANT.session_expired": "The CLI session has expired; sign in again with plak login.",
    "INVALID_GRANT.member_not_active": "The member behind this CLI session is no longer active.",
    "EXPIRED_TOKEN.device_code": "The device code has expired; start signing in again.",
    "SLOW_DOWN": "Ask at most once every {interval} seconds; wait longer.",
    "AUTHORIZATION_PENDING": "Not approved in beheer yet.",
    "ACCESS_DENIED": "Linking was refused.",
    "ACCESS_DENIED.approver_not_active": "The member who approved this is no longer active.",
    # -- Upload and multipart -------------------------------------------------
    "BODY_TOO_LARGE": "Upload larger than {max_body} bytes.",
    "CLIENT_ABORTED": "Upload aborted by the client.",
    "NOT_MULTIPART": "Invalid input: {field} (multipart/form-data with a file field required).",
    "FILE_MISSING": "Invalid input: {field}.",
    "MULTIPART_INVALID": "Invalid multipart request.",
    "MULTIPART_INVALID.no_field_name": "Multipart part without a field name.",
    "MULTIPART_INVALID.unexpected_file_field": "Unexpected file field {field!r}.",
    "MULTIPART_INVALID.more_than_one_file": "More than one file in the upload.",
    "MULTIPART_INVALID.too_many_fields": "Too many form fields.",
    "MULTIPART_INVALID.field_too_large": "Form field {field!r} is too large.",
    "MULTIPART_INVALID.incomplete": "The multipart request is incomplete.",
    "PREVIEW_REF_INVALID": "The preview ref is not a valid slug.",
    # -- Validation FastAPI itself signals ------------------------------------
    "INVALID_INPUT": "Invalid input: {fields}",
    "INVALID_INPUT.request": "Invalid input: request",
    # -- Ingest ---------------------------------------------------------------
    "ORIGIN_INVALID": "Exactly one of member_id or ci_repository has to be set.",
    "UNKNOWN_VERSION": "This version does not exist.",
    "UNKNOWN_VERSION.site": "Unknown version for this site.",
    "VERSION_OTHER_SITE": "This version does not belong to this site.",
    "ROLLBACK_TARGET_PREVIEW": "Versions targeting 'preview' are never a rollback target.",
    # -- Bundles: paths -------------------------------------------------------
    "NULL_BYTE": "Path contains a null byte: {path!r}",
    "ABSOLUTE_PATH": "Absolute path refused: {path}",
    "PATH_TRAVERSAL": "Path with a '..' segment refused: {path}",
    "EMPTY_PATH": "Empty path refused: {path!r}",
    "TOO_DEEP": "Path deeper than {max_depth} levels: {path}",
    "DUPLICATE_PATH": "Path occurs more than once, or as both a directory and a file: {path}",
    "SECRET_FILE": (
        "'{path}' does not belong on a website and is not published. Publish the directory with "
        "the built site (often 'dist' or 'build') instead of the whole project directory, or take "
        "'{secret}' out of the bundle."
    ),
    "RESERVED_SEGMENT": (
        "'{segment}' sits {where} and is a reserved segment; rename the directory or the file, "
        "or publish another directory"
    ),
    "RESERVED_SEGMENT.base_path": "'{segment}' is a reserved segment",
    "where.base_path": "in the root of base path '{base}'",
    "where.peeled": "in the root of the site after peeling off '{peeled}'",
    "where.bundle_root": "in the root of the bundle",
    # -- Bundles: limits and archives -----------------------------------------
    "TOO_MANY_FILES": "The bundle holds more than {limit} files or directories.",
    "TOO_MANY_FILES.archive_entries": (
        "The archive holds more than {limit} entries; whatever falls outside the root of the site "
        "counts towards that too."
    ),
    "FILE_TOO_LARGE": "File '{path}' is larger than {max_file} bytes unpacked.",
    "TOTAL_TOO_LARGE": "The bundle is larger than {max_total} bytes unpacked.",
    "TOTAL_TOO_LARGE.archive": "The archive is larger than {max_total} bytes unpacked.",
    "SITE_QUOTA_EXCEEDED": (
        "This site already uses {used} of {max_bytes} bytes, which leaves no room for the "
        "{added} bytes of this version. Old live versions beyond the number kept are removed "
        "nightly. Otherwise remove previews you no longer need, or ask the platform "
        "administrator for more room."
    ),
    "STORAGE_UNAVAILABLE": (
        "There is too little free space to publish right now. Try again later; the platform "
        "administrator has been notified."
    ),
    "VOLUME_UNMEASURABLE": "The fill level of the content volume cannot be measured.",
    "SYMLINK_REFUSED": "Symlink in the archive refused: {name}",
    "HARDLINK_REFUSED": "Hard link in the archive refused: {name}",
    "SPECIAL_FILE": "Not a regular file, refused: {name}",
    "INVALID_ARCHIVE.zip": "Zip archive unreadable: {error}",
    "INVALID_ARCHIVE.zip_entry": "Zip entry unreadable: {error}",
    "INVALID_ARCHIVE.tar": "Tar.gz archive unreadable: {error}",
    "INVALID_ARCHIVE.tar_entry": "Tar entry unreadable: {name}",
    "INVALID_ARCHIVE.html": "Html file unreadable: {error}",
    "UNKNOWN_FORMAT": "Only .html, .zip, .tar.gz or .tgz is accepted.",
    "EMPTY_ARCHIVE": (
        "The bundle holds no publishable files; OS metadata alone "
        "({junk_dir}, {junk_prefix}*, .DS_Store) does not count."
    ),
    # -- Bundles: the root of the site ---------------------------------------
    "BASE_PATH_INVALID": "The base path is invalid: {cause}",
    "BASE_PATH_UNKNOWN": "Base path '{base_path}' is not a directory in the bundle",
    "BASE_PATH_UNKNOWN.case_variant": (
        "Base path '{base_path}' is not a directory in the bundle; the bundle does hold "
        "'{variant}', and capitals count"
    ),
    "BASE_PATH_UNKNOWN.peeled": (
        "Base path '{base_path}' is not a directory in the bundle; the enclosing directory "
        "'{peeled}' has been peeled off already, so a path inside it is enough"
    ),
    "BASE_PATH_UNKNOWN.file": "Base path '{base_path}' points at a file, not at a directory",
    "BASE_PATH_UNKNOWN.html_file": (
        "A single html file holds no directories; leave the base path ('{base_path}') out"
    ),
    "BASE_PATH_WITHOUT_INDEX.empty": "Base path '{base_path}' holds no files",
    "BASE_PATH_WITHOUT_INDEX.empty_with_suggestion": (
        "Base path '{base_path}' holds no files; {suggestion}"
    ),
    "BASE_PATH_WITHOUT_INDEX.no_index": "Base path '{base_path}' holds no {index}",
    "BASE_PATH_WITHOUT_INDEX.no_index_with_suggestion": (
        "Base path '{base_path}' holds no {index}; {suggestion}"
    ),
    "suggestion.in_root": "the {index} sits in the root of the bundle; leave the base path field out",
    "suggestion.nearest": "the nearest one sits at '{path}'",
    "NO_INDEX": "The bundle holds no {index} anywhere; the root of the bundle needs one",
    "NO_INDEX.case_variant": (
        "The bundle holds no {index} anywhere; the root of the bundle needs one. Mind you: "
        "'{variant}' does not count, because the server is case sensitive; rename the file to "
        "'{index}'"
    ),
    "NO_INDEX.nearest": (
        "No {index} in the root of the bundle; the nearest one sits at '{shortest}'. Publish the "
        "directory '{directory}' itself, or send along the base path field with the value "
        "'{directory}'"
    ),
    # -- Examples in the OpenAPI document, one per status code ----------------
    "example.401": "There is no active session.",
    "example.403": "You have no access here.",
    "example.404": "This does not exist, or you may not see it.",
    "example.409": "This clashes with the current state.",
    "example.413": "The request is larger than allowed.",
    "example.422": "The input is not right.",
    "example.429": "Too many requests; try again later.",
    "example.other": "Something went wrong.",
}

_CATALOGUES: Final[dict[str, dict[str, str]]] = {"nl": NL, "en": EN}
_TITLES: Final[dict[str, dict[int, str]]] = {"nl": TITLES_NL, "en": TITLES_EN}
_FALLBACK_TITLES: Final[dict[str, str]] = {"nl": FALLBACK_TITLE_NL, "en": FALLBACK_TITLE_EN}

# `UNKNOWN_SITE`, `UNKNOWN_SITE.deploy`; or a lowercase fragment key.
_CODE_KEY_RE: Final = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*(?:\.[a-z0-9_]+)?$")
_FRAGMENT_KEY_RE: Final = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)*$")


@dataclass(frozen=True)
class Msg:
    """One message: the key, plus the values its text interpolates.

    A value may itself be a `Msg`, for a sentence that is built from a
    fragment; it is rendered in the same language as the message holding it.
    """

    key: str
    params: Mapping[str, object] = field(default_factory=dict)


def is_fragment(key: str) -> bool:
    return key[:1].islower()


def code_of(key: str) -> str:
    """The machine-readable code a key carries: everything before the dot."""
    if is_fragment(key):  # pragma: no cover - a guard that only fires while editing
        raise ValueError(f"fragment key has no code: {key}")
    return key.split(".", 1)[0]


def render(locale: str, message: Msg) -> str:
    """The text of one message. An unknown locale falls back to the API
    default rather than raising: an answer in the wrong language is still an
    answer."""
    catalogue = _CATALOGUES.get(locale) or _CATALOGUES[i18n.API_DEFAULT]
    template = catalogue[message.key]
    if not message.params:
        return template
    values = {
        name: render(locale, value) if isinstance(value, Msg) else value
        for name, value in message.params.items()
    }
    return template.format(**values)


def title(locale: str, status: int) -> str:
    """The short, fixed description of a status code."""
    titles = _TITLES.get(locale) or _TITLES[i18n.API_DEFAULT]
    return titles.get(status) or _FALLBACK_TITLES.get(locale, FALLBACK_TITLE_EN)


def placeholders(template: str) -> set[str]:
    """The names a template interpolates, conversions and formats ignored."""
    return {name for _, name, _, _ in Formatter().parse(template) if name}


def _check_catalogues() -> None:
    """Both catalogues hold the same keys, with the same parameters, and every
    key is either a code key or a fragment. A mismatch is a mistake in this
    file, so it fires on import rather than on the request that needs the
    message."""
    missing = set(NL) ^ set(EN)
    if missing:
        raise RuntimeError(f"catalogues disagree on: {sorted(missing)}")
    for key, dutch in NL.items():
        if not (_CODE_KEY_RE.match(key) or _FRAGMENT_KEY_RE.match(key)):
            raise RuntimeError(f"not a usable message key: {key}")
        if placeholders(dutch) != placeholders(EN[key]):
            raise RuntimeError(f"catalogues interpolate different values in: {key}")
    if set(TITLES_NL) != set(TITLES_EN):
        raise RuntimeError("title tables disagree on the status codes they cover")


_check_catalogues()


__all__ = [
    "EN",
    "FALLBACK_TITLE_EN",
    "FALLBACK_TITLE_NL",
    "NL",
    "TITLES_EN",
    "TITLES_NL",
    "Msg",
    "code_of",
    "is_fragment",
    "placeholders",
    "render",
    "title",
]
