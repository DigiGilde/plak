"""The audit vocabulary: every action, result and retention tier in one place.

audit_log_entries is append-only, so these strings are a contract: renaming
one afterwards leaves an environment with two vocabularies. docs/audit-log.md
is the human-readable side; test_audit_vocabulary.py keeps the two in step.
"""

from __future__ import annotations

from typing import Final

ALLOWED: Final = "allowed"
REFUSED: Final = "refused"
LOGIN_REDIRECT: Final = "login_redirect"

CONTENT_ACCESS: Final = "content_access"
LOGIN: Final = "login"
LOGOUT: Final = "logout"
ADMIN_ACCESS: Final = "admin_access"
DEPLOY: Final = "deploy"
PREVIEW_TEARDOWN: Final = "preview_teardown"
AUDIT_READ: Final = "audit_read"
AUDIT_ACTOR_LOOKUP: Final = "audit_actor_lookup"
AUDIT_ACTOR_IDENTITY: Final = "audit_actor_identity"
AUDIT_IP_REVEAL: Final = "audit_ip_reveal"
AUDIT_PURGE: Final = "audit_purge"
CLI_LOGIN: Final = "cli_login"
CLI_LOGIN_DENIED: Final = "cli_login_denied"
CLI_TOKEN_ISSUED: Final = "cli_token_issued"  # noqa: S105 - an action name, not a secret
CLI_LOGOUT: Final = "cli_logout"
CLI_SESSION_REVOKE: Final = "cli_session_revoke"
MEMBER_LANGUAGE: Final = "member_language"
CLI_REFRESH_REUSE: Final = "cli_refresh_reuse"
IDP_SESSION_ENDED_ACTION: Final = "idp_session_ended"
VERSION_CLEANUP: Final = "version_cleanup"
SITE_REPOSITORY_RENAME: Final = "site_repository_rename"

ADMIN_ACTIONS: Final = frozenset(
    {
        "group_create",
        "group_delete",
        "group_name",
        "group_default_visibility",
        "group_member_add",
        "group_member_remove",
        "group_member_role",
        "site_create",
        "site_delete",
        "site_title",
        "site_visibility",
        "site_external_sources",
        "site_sandbox",
        "site_live_versions_kept",
        "version_set_live",
        "site_member_add",
        "site_member_remove",
        "site_member_role",
        "preview_visibility",
        "invitee_add",
        "invitee_remove",
        "key_create",
        "key_revoke",
        "site_repository_set",
        "site_repository_remove",
        "site_repository_site_id_required",
        "member_activate",
        "member_deactivate",
        "member_platform_role",
    }
)

ACTIONS: Final = frozenset(
    {
        CONTENT_ACCESS,
        LOGIN,
        LOGOUT,
        ADMIN_ACCESS,
        DEPLOY,
        PREVIEW_TEARDOWN,
        AUDIT_READ,
        AUDIT_ACTOR_LOOKUP,
        AUDIT_ACTOR_IDENTITY,
        AUDIT_IP_REVEAL,
        AUDIT_PURGE,
        CLI_LOGIN,
        CLI_LOGIN_DENIED,
        CLI_TOKEN_ISSUED,
        CLI_LOGOUT,
        CLI_SESSION_REVOKE,
        CLI_REFRESH_REUSE,
        MEMBER_LANGUAGE,
        IDP_SESSION_ENDED_ACTION,
        VERSION_CLEANUP,
        SITE_REPOSITORY_RENAME,
        *ADMIN_ACTIONS,
    }
)

RESULTS: Final = frozenset({ALLOWED, REFUSED, LOGIN_REDIRECT})

# refs["kind"] on login and logout: which of the two session kinds.
SESSION_ADMIN: Final = "admin"
SESSION_CONTENT: Final = "content"

# Why a login callback was refused. Coarse on purpose: a peak is what BIO2
# 5.17.01 asks to detect, and which check tripped adds nothing to that. Never
# the value that tripped it (BIO2 8.15.02).
LOGIN_IDP_ERROR: Final = "IDP_ERROR"
LOGIN_IDP_UNREACHABLE: Final = "IDP_UNREACHABLE"
LOGIN_ATTEMPT_MISSING: Final = "ATTEMPT_MISSING"
LOGIN_STATE_MISMATCH: Final = "STATE_MISMATCH"
LOGIN_CODE_MISSING: Final = "CODE_MISSING"
LOGIN_ISS_MISMATCH: Final = "ISS_MISMATCH"
LOGIN_ACR_INSUFFICIENT: Final = "ACR_INSUFFICIENT"
LOGIN_TOKEN_INVALID: Final = "TOKEN_INVALID"  # noqa: S105 - a reason code, not a secret

LOGIN_REASONS: Final = frozenset(
    {
        LOGIN_IDP_ERROR,
        LOGIN_IDP_UNREACHABLE,
        LOGIN_ATTEMPT_MISSING,
        LOGIN_STATE_MISMATCH,
        LOGIN_CODE_MISSING,
        LOGIN_ISS_MISMATCH,
        LOGIN_ACR_INSUFFICIENT,
        LOGIN_TOKEN_INVALID,
    }
)

# Why a session was ended by the IdP rather than by the person themselves
# (auth/revalidation.py, platform/backchannel.py).
IDP_SESSION_ENDED: Final = "IDP_SESSION_ENDED"
IDP_SUB_MISMATCH: Final = "IDP_SUB_MISMATCH"
IDP_TOKEN_INVALID: Final = "IDP_TOKEN_INVALID"  # noqa: S105 - a reason code, not a secret
IDP_BACKCHANNEL_LOGOUT: Final = "IDP_BACKCHANNEL_LOGOUT"

IDP_SESSION_REASONS: Final = frozenset(
    {
        IDP_SESSION_ENDED,
        IDP_SUB_MISMATCH,
        IDP_TOKEN_INVALID,
        IDP_BACKCHANNEL_LOGOUT,
    }
)

# refs["via"] on a deploy, a preview teardown, or a group or site creation by a
# member through `plak login`.
VIA_CLI: Final = "cli"

# refs["ip_unvouched"], set to true on a row whose IP address was derived over
# a skipped X-Forwarded-For entry (net.py, ClientAddress). Absent means the
# address was observed, so a reader may never take the absence for "unknown".
IP_UNVOUCHED: Final = "ip_unvouched"

# Why a CI ID token was refused on a deploy or preview teardown.
CI_ISSUER_UNKNOWN: Final = "CI_ISSUER_UNKNOWN"
CI_TOKEN_INVALID: Final = "CI_TOKEN_INVALID"  # noqa: S105 - a reason code, not a secret
CI_AUDIENCE_MISMATCH: Final = "CI_AUDIENCE_MISMATCH"
CI_REPOSITORY_NOT_TRUSTED: Final = "CI_REPOSITORY_NOT_TRUSTED"
CI_BRANCH_NOT_ALLOWED: Final = "CI_BRANCH_NOT_ALLOWED"
CI_PROVIDER_UNREACHABLE: Final = "CI_PROVIDER_UNREACHABLE"
CI_SITE_ID_REQUIRED: Final = "CI_SITE_ID_REQUIRED"
SITE_MOVED: Final = "SITE_MOVED"

CI_REASONS: Final = frozenset(
    {
        CI_ISSUER_UNKNOWN,
        CI_TOKEN_INVALID,
        CI_AUDIENCE_MISMATCH,
        CI_REPOSITORY_NOT_TRUSTED,
        CI_BRANCH_NOT_ALLOWED,
        CI_PROVIDER_UNREACHABLE,
        CI_SITE_ID_REQUIRED,
        SITE_MOVED,
    }
)

# Retention, in days. The database is authoritative: audit_log_retention() in
# 0001_base is what the delete guard and the purge enforce, and
# test_migrations.py holds these two numbers to it. 1096 days is three years
# with a leap day, so the long term never falls short of three calendar years.
SHORT_RETENTION_DAYS: Final = 90
LONG_RETENTION_DAYS: Final = 1096

# (action, result) pairs that are about looking and presence rather than about
# security: these go after SHORT_RETENTION_DAYS, everything else after LONG.
SHORT_RETENTION: Final = frozenset(
    {
        (CONTENT_ACCESS, ALLOWED),
        (LOGIN, ALLOWED),
        (LOGOUT, ALLOWED),
        (CLI_LOGOUT, ALLOWED),
    }
)
