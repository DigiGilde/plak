"""Access decision: the outcome of the access gate.

Refusals carry a reason_code internally only; on the outside every refusal is a
byte-identical neutral 404 (spec §5.6, anti-enumeration).
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass

from plak.constants import AccessPolicy

REASON_OK = "OK"
REASON_LOGIN_REQUIRED = "LOGIN_REQUIRED"
REASON_UNKNOWN_GROUP = "UNKNOWN_GROUP"
REASON_UNKNOWN_SITE = "UNKNOWN_SITE"
REASON_NO_LIVE_VERSION = "NO_LIVE_VERSION"
REASON_UNKNOWN_PREVIEW = "UNKNOWN_PREVIEW"
REASON_PREVIEW_EXPIRED = "PREVIEW_EXPIRED"
REASON_KEY_INVALID = "KEY_INVALID"
# The three refusals around a secret link shared without its code
# (serving/code_page.py): the code page was shown instead of the content, a
# wrong code was handed in, and an attempt that ran into the limit per
# selector.
REASON_KEY_CODE_REQUIRED = "KEY_CODE_REQUIRED"
REASON_KEY_CODE_INVALID = "KEY_CODE_INVALID"
REASON_KEY_CODE_THROTTLED = "KEY_CODE_THROTTLED"
REASON_NO_ACCESS = "NO_ACCESS"
REASON_UNKNOWN_VERSION = "UNKNOWN_VERSION"


class DecisionKind(enum.StrEnum):
    ALLOW = "allow"
    NEUTRAL_404 = "neutral_404"
    LOGIN_REDIRECT = "login_redirect"


@dataclass(frozen=True)
class AccessDecision:
    kind: DecisionKind
    version_id: uuid.UUID | None
    # The policy that decided, which on a preview with an override is that
    # override and not the site's own.
    effective_access: AccessPolicy | None
    reason_code: str
    # Selector of the secret link that granted access; never the verifier.
    key_selector: str | None = None
    # The key a `?key=` in the URL proved, for the serving layer to redeem
    # into the key cookie; None when access came in any other way.
    redeem_key_id: uuid.UUID | None = None
    # What serving needs of the allowed version and its site, read with the
    # decision so serving looks nothing up again.
    storage_ref: str | None = None
    external_sources: bool = False
    sandbox: bool = False


def allow(
    version_id: uuid.UUID,
    access: AccessPolicy,
    *,
    storage_ref: str | None = None,
    external_sources: bool = False,
    sandbox: bool = False,
    key_selector: str | None = None,
    redeem_key_id: uuid.UUID | None = None,
) -> AccessDecision:
    return AccessDecision(
        DecisionKind.ALLOW,
        version_id,
        access,
        REASON_OK,
        key_selector=key_selector,
        redeem_key_id=redeem_key_id,
        storage_ref=storage_ref,
        external_sources=external_sources,
        sandbox=sandbox,
    )


def neutral_404(reason_code: str) -> AccessDecision:
    return AccessDecision(DecisionKind.NEUTRAL_404, None, None, reason_code)


def login_redirect(access: AccessPolicy) -> AccessDecision:
    return AccessDecision(DecisionKind.LOGIN_REDIRECT, None, access, REASON_LOGIN_REQUIRED)
