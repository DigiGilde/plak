"""security.txt under /.well-known/ (RFC 9116; BIO2 5.24.08).

One document for two origins. RFC 9116 section 2.5.3 says a file whose
retrieval URI appears in none of its Canonical fields should not be trusted,
so the content origin and the admin origin each get a Canonical line of their
own and both hosts serve the same body. A Canonical web URI must be https, so
an http origin (the dev and e2e stacks) yields no Canonical line rather than an
invalid one: the field is optional, Contact and Expires are not.

Expires is computed per request rather than written down. A date that is fixed
once is in the past the day it passes, and an expired security.txt is invalid,
not merely old.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from urllib.parse import quote

from plak.security_headers import is_https

if TYPE_CHECKING:
    from plak.config import Settings

PATH_SECURITY_TXT = "/.well-known/security.txt"

# Well under the year RFC 9116 section 2.5.5 recommends, counted from the start
# of the current UTC day: the body then changes once a day instead of on every
# request. Short because it costs nothing when the value is computed anyway,
# and a long window reads as a file that may sit still for a year.
VALIDITY = timedelta(days=90)

EXPIRES_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

# The NCSC-NL lines (PREAMBLE_NCSC, POLICY_NCSC, CONTACT_NCSC) hold only for an
# installation inside the Dutch central government, so they appear only when
# PLAK_SECURITY_TXT_NCSC is on.
PREAMBLE_NCSC = (
    "# NCSC-NL blijft het centrale CVD-meldpunt voor kwetsbaarheden en incidenten",
    "# voor de Rijksoverheid.",
    "#",
    "# NCSC-NL remains the central CVD reporting point for vulnerabilities and",
    "# incidents for the Dutch central government.",
)

# Only when PLAK_OPERATOR_EMAIL is set: without it there is no address of the
# operator below, and the sentence would point at NCSC-NL's or at none.
PREAMBLE_OPERATOR_EMAIL = (
    "# Het e-mailadres hieronder is de directe lijn naar de organisatie die deze",
    "# installatie van Plak beheert.",
    "#",
    "# The email address below is the direct line to the organisation that runs",
    "# this installation of Plak.",
)

POLICY = ("Policy: https://github.com/DigiGilde/plak/blob/beta/SECURITY.md",)

POLICY_NCSC = (
    "Policy: https://www.ncsc.nl/producten-en-diensten/kwetsbaarheid-melden-cvd",
    "Policy: https://www.ncsc.nl/en/cvd-report-form",
)

# In order of preference (RFC 9116 section 2.5.3). The GitHub advisory route
# leads: it keeps the report private until there is a fix and asks nothing of
# the reporter beyond an account. It exists only because the repository is
# public with private vulnerability reporting on; while it was private that
# URL answered a 404. See docs/security.md, "Now the repo is on GitHub".
# The operator's address (PLAK_OPERATOR_EMAIL) goes between this and
# CONTACT_NCSC.
CONTACT = ("Contact: https://github.com/DigiGilde/plak/security/advisories/new",)

CONTACT_NCSC = (
    "Contact: https://www.ncsc.nl/producten-en-diensten/kwetsbaarheid-melden-cvd",
    "Contact: https://www.ncsc.nl/en/cvd-report-form",
    "Contact: mailto:security@ncsc.nl",
)

# No Encryption field: RFC 9116 cannot tie a key to one Contact, so a reporter
# would take it for the key of the first contacts, the Plak team, who have none.
CLOSING = ("Preferred-Languages: nl, en",)


def expires_at(now: datetime) -> datetime:
    # The Z in EXPIRES_FORMAT is a claim about the time zone, so the moment is
    # converted before the day is truncated.
    start_of_day = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return start_of_day + VALIDITY


def canonical_origins(settings: Settings) -> tuple[str, ...]:
    origins = (settings.content_base_url, settings.base_url or "")
    return tuple(origin.rstrip("/") for origin in origins if is_https(origin))


def preamble(settings: Settings) -> tuple[str, ...]:
    blocks = [PREAMBLE_NCSC] if settings.security_txt_ncsc else []
    if settings.operator_email:
        blocks.append(PREAMBLE_OPERATOR_EMAIL)
    lines: list[str] = []
    for block in blocks:
        if lines:
            lines.append("#")
        lines.extend(block)
    # Without a comment there is no blank line either: the file opens with Expires.
    return (*lines, "") if lines else ()


def operator_contact(settings: Settings) -> tuple[str, ...]:
    if not settings.operator_email:
        return ()
    # RFC 6068 section 2: outside the unreserved characters and these
    # some-delims, an addr-spec in a mailto URI is percent-encoded UTF-8. The
    # setting has already refused the delimiters that would split the URI.
    address = quote(settings.operator_email, safe="@!$'*+")
    return (f"Contact: mailto:{address}",)


def security_txt(settings: Settings, now: datetime) -> str:
    ncsc = settings.security_txt_ncsc
    lines = [
        *preamble(settings),
        f"Expires: {expires_at(now).strftime(EXPIRES_FORMAT)}",
        *(f"Canonical: {origin}{PATH_SECURITY_TXT}" for origin in canonical_origins(settings)),
        "",
        *POLICY,
        *(POLICY_NCSC if ncsc else ()),
        "",
        *CONTACT,
        *operator_contact(settings),
        *(CONTACT_NCSC if ncsc else ()),
        *CLOSING,
    ]
    return "\n".join(lines) + "\n"


__all__ = [
    "CLOSING",
    "CONTACT",
    "CONTACT_NCSC",
    "EXPIRES_FORMAT",
    "PATH_SECURITY_TXT",
    "POLICY",
    "POLICY_NCSC",
    "PREAMBLE_NCSC",
    "PREAMBLE_OPERATOR_EMAIL",
    "VALIDITY",
    "canonical_origins",
    "expires_at",
    "operator_contact",
    "preamble",
    "security_txt",
]
