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

PREAMBLE = (
    "# NCSC-NL blijft het centrale CVD-meldpunt voor kwetsbaarheden en incidenten",
    "# voor de Rijksoverheid. Het e-mailadres hieronder is de directe lijn naar",
    "# het team achter Plak.",
    "#",
    "# NCSC-NL remains the central CVD reporting point for vulnerabilities and",
    "# incidents for the Dutch central government. The email address below is",
    "# the direct line to the team behind Plak.",
)

POLICY = (
    "Policy: https://github.com/DigiGilde/plak/blob/beta/SECURITY.md",
    "Policy: https://www.ncsc.nl/producten-en-diensten/kwetsbaarheid-melden-cvd",
    "Policy: https://www.ncsc.nl/en/cvd-report-form",
)

# In order of preference (RFC 9116 section 2.5.4). The GitHub advisory route
# leads: it keeps the report private until there is a fix and asks nothing of
# the reporter beyond an account. It exists only because the repository is
# public with private vulnerability reporting on; while it was private that
# URL answered a 404. See docs/security.md, "Now the repo is on GitHub".
CONTACT = (
    "Contact: https://github.com/DigiGilde/plak/security/advisories/new",
    "Contact: mailto:digigilde@rijksoverheid.nl",
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


def security_txt(settings: Settings, now: datetime) -> str:
    lines = [
        *PREAMBLE,
        "",
        f"Expires: {expires_at(now).strftime(EXPIRES_FORMAT)}",
        *(f"Canonical: {origin}{PATH_SECURITY_TXT}" for origin in canonical_origins(settings)),
        "",
        *POLICY,
        "",
        *CONTACT,
        *CLOSING,
    ]
    return "\n".join(lines) + "\n"


__all__ = [
    "CLOSING",
    "CONTACT",
    "EXPIRES_FORMAT",
    "PATH_SECURITY_TXT",
    "POLICY",
    "PREAMBLE",
    "VALIDITY",
    "canonical_origins",
    "expires_at",
    "security_txt",
]
