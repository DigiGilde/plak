"""The parts CI runs the backend suite in, side by side (.github/workflows/ci.yml).

PLAK_TEST_PART picks one; without it everything runs, as locally. `rest` takes
every test file the other parts do not name, so a new file always runs
somewhere and never silently drops out of CI.
"""

from __future__ import annotations

PART_VAR = "PLAK_TEST_PART"
REST = "rest"

NAMED: dict[str, frozenset[str]] = {
    # The access gate and what sits behind it: serving, ingest, deploys.
    "access": frozenset(
        {
            "test_access_gate.py",
            "test_cleanup_job.py",
            "test_code_page.py",
            "test_deploy_api.py",
            "test_front_door.py",
            "test_host_separation.py",
            "test_ingest_service.py",
            "test_keys.py",
            "test_security_headers.py",
            "test_security_txt.py",
            "test_serving.py",
            "test_spa.py",
            "test_unpacker.py",
        }
    ),
    # Who someone is: OIDC, sessions, members, the CLI and CI tokens.
    "auth": frozenset(
        {
            "test_backchannel_logout.py",
            "test_ci_binding.py",
            "test_ci_providers.py",
            "test_ci_tokens.py",
            "test_ci_trust.py",
            "test_cli_api.py",
            "test_cli_creation.py",
            "test_cli_service.py",
            "test_members.py",
            "test_oidc.py",
            "test_session_revalidation.py",
            "test_sessions.py",
        }
    ),
}

PARTS = (*NAMED, REST)


def runs_in(part: str, filename: str) -> bool:
    if part == REST:
        return not any(filename in names for names in NAMED.values())
    return filename in NAMED[part]
