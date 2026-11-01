import base64
import os
import traceback
from datetime import UTC, datetime
from pathlib import Path

import pytest

from toon.audit import chain, checkpoint, retention
from toon.config import ConfigurationError, load_settings, server_variable

ZAD_OIDC_VARIABLES = ("OIDC_DISCOVERY_URL", "OIDC_CLIENT_ID", "OIDC_CLIENT_SECRET")


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch):
    for key in list(os.environ):
        if key.upper().startswith(("TOON_", "PLAK_")) or key in ZAD_OIDC_VARIABLES:
            monkeypatch.delenv(key, raising=False)


def _set_required_env(monkeypatch, **overrides):
    values = {
        "TOON_DB_URL": "postgresql+asyncpg://gebruiker:wachtwoord@localhost/plak",
        "TOON_CONTENT_ROOT": "/var/lib/plak/content",
        "TOON_OIDC_ISSUER": "https://idp.example.org",
        "TOON_OIDC_CLIENT_ID": "plak",
        "TOON_OIDC_CLIENT_PRIVATE_JWK": '{"kty": "RSA"}',
        "TOON_OIDC_REQUIRED_ACR": "urn:acr:hoog",
        "TOON_SESSION_SECRET": "x" * 32,
        "TOON_AUDIT_PEPPER": "y" * 32,
        "TOON_AUDIT_IP_KEY": base64.b64encode(b"z" * 32).decode(),
        "TOON_CONTENT_BASE_URL": "https://plak.example",
        "TOON_ENVIRONMENT": "dev",
    }
    values.update(overrides)
    for key, value in values.items():
        monkeypatch.setenv(key, value)


def test_missing_required_env_fails_with_clear_message(monkeypatch):
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()
    assert "TOON_DB_URL" in str(excinfo.value)


def test_valid_configuration_loads_with_expected_defaults(monkeypatch):
    _set_required_env(monkeypatch)
    settings = load_settings()
    assert str(settings.db_url) == "postgresql+asyncpg://gebruiker:wachtwoord@localhost/plak"
    assert settings.environment == "dev"
    assert settings.base_url is None
    assert settings.spa_path == Path("../frontend/dist")
    assert settings.oidc_iss_required is False
    assert settings.ingest_max_body == 100 * 1024 * 1024
    assert settings.ingest_max_file == 50 * 1024 * 1024
    assert settings.ingest_max_total == 200 * 1024 * 1024
    assert settings.site_max_bytes == 500 * 1024 * 1024
    assert settings.live_versions_kept == 5
    assert settings.storage_min_free_bytes == 100 * 1024 * 1024


def test_too_short_session_secret_refused(monkeypatch):
    _set_required_env(monkeypatch, TOON_SESSION_SECRET="te-kort")
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()
    assert "TOON_SESSION_SECRET" in str(excinfo.value)


def test_too_short_audit_pepper_refused(monkeypatch):
    _set_required_env(monkeypatch, TOON_AUDIT_PEPPER="te-kort")
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()
    assert "TOON_AUDIT_PEPPER" in str(excinfo.value)


def test_audit_lookup_daily_limit_defaults_to_25(monkeypatch):
    _set_required_env(monkeypatch)
    assert load_settings().audit_lookup_daily_limit == 25


def test_audit_lookup_daily_limit_from_env(monkeypatch):
    _set_required_env(monkeypatch, TOON_AUDIT_LOOKUP_DAILY_LIMIT="5")
    assert load_settings().audit_lookup_daily_limit == 5


def test_audit_lookup_daily_limit_below_one_refused(monkeypatch):
    _set_required_env(monkeypatch, TOON_AUDIT_LOOKUP_DAILY_LIMIT="0")
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()
    assert "AUDIT_LOOKUP_DAILY_LIMIT" in str(excinfo.value)


def test_audit_lookup_daily_limit_above_upper_bound_refused(monkeypatch):
    _set_required_env(monkeypatch, TOON_AUDIT_LOOKUP_DAILY_LIMIT="1001")
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()
    assert "AUDIT_LOOKUP_DAILY_LIMIT" in str(excinfo.value)


def test_live_versions_kept_from_env_and_zero_keeps_all(monkeypatch):
    _set_required_env(monkeypatch, TOON_LIVE_VERSIONS_KEPT="0")
    assert load_settings().live_versions_kept == 0


def test_negative_live_versions_kept_refused(monkeypatch):
    _set_required_env(monkeypatch, TOON_LIVE_VERSIONS_KEPT="-1")
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()
    assert "LIVE_VERSIONS_KEPT" in str(excinfo.value)


def test_non_base64_audit_ip_key_refused(monkeypatch):
    _set_required_env(monkeypatch, TOON_AUDIT_IP_KEY="niet-base64!!!")
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()
    assert "TOON_AUDIT_IP_KEY" in str(excinfo.value)


def test_wrong_length_audit_ip_key_refused(monkeypatch):
    _set_required_env(monkeypatch, TOON_AUDIT_IP_KEY=base64.b64encode(b"te-kort").decode())
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()
    assert "TOON_AUDIT_IP_KEY" in str(excinfo.value)


def test_audit_ip_key_equal_to_audit_pepper_refused(monkeypatch):
    shared = base64.b64encode(b"z" * 32).decode()
    _set_required_env(monkeypatch, TOON_AUDIT_PEPPER=shared, TOON_AUDIT_IP_KEY=shared)
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()
    assert "TOON_AUDIT_IP_KEY" in str(excinfo.value)


def test_audit_ip_key_equal_to_session_secret_refused(monkeypatch):
    shared = base64.b64encode(b"z" * 32).decode()
    _set_required_env(monkeypatch, TOON_SESSION_SECRET=shared, TOON_AUDIT_IP_KEY=shared)
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()
    assert "TOON_AUDIT_IP_KEY" in str(excinfo.value)


def test_audit_pepper_equal_to_session_secret_refused(monkeypatch):
    """One leaked value would then both forge sessions and undo the
    pseudonymisation of every actor in the audit log."""
    shared = "g" * 32
    _set_required_env(monkeypatch, TOON_SESSION_SECRET=shared, TOON_AUDIT_PEPPER=shared)
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()
    assert "TOON_AUDIT_PEPPER must differ from TOON_SESSION_SECRET" in str(excinfo.value)
    assert shared not in _full_traceback(excinfo.value)


def test_audit_ip_key_previous_absent_by_default(monkeypatch):
    _set_required_env(monkeypatch)
    settings = load_settings()
    assert settings.audit_ip_key_previous == ""
    assert settings.audit_ip_keys_previous_bytes == ()


def test_audit_ip_key_previous_decoded(monkeypatch):
    """One key, the only form before the list, still reads: as a list of one."""
    raw = b"p" * 32
    _set_required_env(monkeypatch, TOON_AUDIT_IP_KEY_PREVIOUS=base64.b64encode(raw).decode())
    settings = load_settings()
    assert settings.audit_ip_keys_previous_bytes == (raw,)


def test_non_base64_audit_ip_key_previous_refused(monkeypatch):
    _set_required_env(monkeypatch, TOON_AUDIT_IP_KEY_PREVIOUS="niet-base64!!!")
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()
    assert "TOON_AUDIT_IP_KEY_PREVIOUS" in str(excinfo.value)


def test_audit_ip_key_previous_equal_to_audit_ip_key_refused(monkeypatch):
    shared = base64.b64encode(b"z" * 32).decode()
    _set_required_env(monkeypatch, TOON_AUDIT_IP_KEY=shared, TOON_AUDIT_IP_KEY_PREVIOUS=shared)
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()
    assert "TOON_AUDIT_IP_KEY_PREVIOUS" in str(excinfo.value)


def test_audit_ip_key_previous_equal_to_audit_pepper_refused(monkeypatch):
    shared = base64.b64encode(b"q" * 32).decode()
    _set_required_env(monkeypatch, TOON_AUDIT_PEPPER=shared, TOON_AUDIT_IP_KEY_PREVIOUS=shared)
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()
    assert "TOON_AUDIT_IP_KEY_PREVIOUS" in str(excinfo.value)


_BASE64_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"


def _ip_key(fill: str) -> str:
    return base64.b64encode(fill.encode() * 32).decode()


def _respelled(key: str) -> str:
    """The same 32-byte key with another last character: base64 ignores the
    two unused bits that character carries."""
    last = _BASE64_ALPHABET.index(key[-2])
    respelled = key[:-2] + _BASE64_ALPHABET[last ^ 1] + key[-1]
    assert respelled != key
    assert base64.b64decode(respelled, validate=True) == base64.b64decode(key)
    return respelled


def test_audit_ip_key_previous_is_a_list_newest_first(monkeypatch):
    """Kept in the configured order; whitespace around an entry and empty
    entries are what a hand-edited list picks up, not part of a key."""
    newer, older = b"n" * 32, b"o" * 32
    encoded_newer, encoded_older = base64.b64encode(newer).decode(), base64.b64encode(older).decode()
    _set_required_env(monkeypatch, TOON_AUDIT_IP_KEY_PREVIOUS=f" {encoded_newer} ,,\t{encoded_older}, ")
    settings = load_settings()
    assert settings.audit_ip_keys_previous_bytes == (newer, older)
    assert settings.audit_ip_key_previous == f"{encoded_newer},{encoded_older}"


@pytest.mark.parametrize("variable", ["TOON_AUDIT_PEPPER_PREVIOUS", "TOON_AUDIT_IP_KEY_PREVIOUS"])
@pytest.mark.parametrize("value", ["", " ", " , ,\t"])
def test_a_previous_list_of_only_separators_is_no_rotation(monkeypatch, variable, value):
    _set_required_env(monkeypatch, **{variable: value})
    settings = load_settings()
    assert settings.audit_peppers == ("y" * 32,)
    assert settings.audit_ip_keys_previous_bytes == ()


@pytest.mark.parametrize("bad", ["niet-base64!!!", base64.b64encode(b"te-kort").decode()])
def test_a_bad_later_entry_of_audit_ip_key_previous_refused(monkeypatch, bad):
    """Every entry is checked, not only the first."""
    _set_required_env(monkeypatch, TOON_AUDIT_IP_KEY_PREVIOUS=f"{_ip_key('p')},{bad}")
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()
    assert "every entry of TOON_AUDIT_IP_KEY_PREVIOUS must be" in str(excinfo.value)
    assert "base64" in str(excinfo.value)


@pytest.mark.parametrize("respelled", [False, True], ids=["same-text", "other-spelling"])
def test_audit_ip_key_previous_listing_one_key_twice_refused(monkeypatch, respelled):
    key = _ip_key("p")
    again = _respelled(key) if respelled else key
    _set_required_env(monkeypatch, TOON_AUDIT_IP_KEY_PREVIOUS=f"{key},{_ip_key('o')},{again}")
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()
    assert "TOON_AUDIT_IP_KEY_PREVIOUS lists the same key twice" in str(excinfo.value)
    assert key not in _full_traceback(excinfo.value)


@pytest.mark.parametrize("respelled", [False, True], ids=["same-text", "other-spelling"])
def test_audit_ip_key_previous_holding_the_current_key_further_on_refused(monkeypatch, respelled):
    current = _ip_key("z")
    listed = _respelled(current) if respelled else current
    _set_required_env(monkeypatch, TOON_AUDIT_IP_KEY=current, TOON_AUDIT_IP_KEY_PREVIOUS=f"{_ip_key('p')},{listed}")
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()
    assert "TOON_AUDIT_IP_KEY_PREVIOUS must differ from TOON_AUDIT_IP_KEY" in str(excinfo.value)


def test_audit_ip_key_previous_equal_to_session_secret_refused(monkeypatch):
    shared = _ip_key("s")
    _set_required_env(
        monkeypatch, TOON_SESSION_SECRET=shared, TOON_AUDIT_IP_KEY_PREVIOUS=f"{_ip_key('p')},{shared}"
    )
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()
    assert "TOON_AUDIT_IP_KEY_PREVIOUS must differ from TOON_SESSION_SECRET and TOON_AUDIT_PEPPER" in str(
        excinfo.value
    )
    assert shared not in _full_traceback(excinfo.value)


def test_two_audit_ip_keys_with_one_key_id_refused(monkeypatch):
    """A row names its key by the key id alone, so two keys behind one id would
    leave the rows of one of them unreadable. A real collision of 8 bytes of
    SHA-256 cannot be made for a test: a key id that is the same for every key
    stands in for it."""
    monkeypatch.setattr("toon.config._key_id", lambda key: b"\x00" * 8)
    _set_required_env(monkeypatch, TOON_AUDIT_IP_KEY_PREVIOUS=_ip_key("p"))
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()
    message = str(excinfo.value)
    assert "TOON_AUDIT_IP_KEY and TOON_AUDIT_IP_KEY_PREVIOUS hold two keys with the same key id" in message


def test_audit_pepper_previous_absent_by_default(monkeypatch):
    _set_required_env(monkeypatch)
    settings = load_settings()
    assert settings.audit_pepper_previous == ""
    assert settings.audit_peppers == ("y" * 32,)


def test_audit_peppers_are_the_current_one_then_the_previous_ones_in_order(monkeypatch):
    _set_required_env(monkeypatch, TOON_AUDIT_PEPPER_PREVIOUS=f" {'b' * 32} ,, \t{'a' * 32},")
    settings = load_settings()
    assert settings.audit_peppers == ("y" * 32, "b" * 32, "a" * 32)
    assert settings.audit_pepper_previous == f"{'b' * 32},{'a' * 32}"


def test_the_current_audit_pepper_is_still_taken_verbatim(monkeypatch):
    """Only the previous peppers are a list. The current one reads exactly as
    it always did, commas and outer whitespace included: reading it any other
    way would change every pseudonym it ever made."""
    pepper = "  een pepper, met een komma en spaties eromheen  "
    _set_required_env(monkeypatch, TOON_AUDIT_PEPPER=pepper)
    settings = load_settings()
    assert settings.audit_pepper == pepper
    assert settings.audit_peppers == (pepper,)


@pytest.mark.parametrize("value", ["a" * 32, "é" * 16])
def test_audit_pepper_previous_counts_bytes_not_characters(monkeypatch, value):
    _set_required_env(monkeypatch, TOON_AUDIT_PEPPER_PREVIOUS=value)
    assert load_settings().audit_peppers == ("y" * 32, value)


@pytest.mark.parametrize("value", ["te-kort-geheim", "a" * 31, "é" * 15, f"{'a' * 32},te-kort-geheim"])
def test_too_short_audit_pepper_previous_refused(monkeypatch, value):
    _set_required_env(monkeypatch, TOON_AUDIT_PEPPER_PREVIOUS=value)
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()
    assert "every entry of TOON_AUDIT_PEPPER_PREVIOUS must be at least 32 bytes long" in str(excinfo.value)
    assert "te-kort-geheim" not in _full_traceback(excinfo.value)


@pytest.mark.parametrize("value", ["y" * 32, f"{'a' * 32},{'y' * 32}", f"  {'y' * 32}\t"])
def test_audit_pepper_previous_holding_the_current_pepper_refused(monkeypatch, value):
    """The likely slip in a rotation: the old pepper copied into the new
    variable without replacing the current one."""
    _set_required_env(monkeypatch, TOON_AUDIT_PEPPER_PREVIOUS=value)
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()
    assert "TOON_AUDIT_PEPPER_PREVIOUS must differ from TOON_AUDIT_PEPPER" in str(excinfo.value)


def test_audit_pepper_previous_equal_to_session_secret_refused(monkeypatch):
    _set_required_env(monkeypatch, TOON_AUDIT_PEPPER_PREVIOUS=f"{'a' * 32},{'x' * 32}")
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()
    assert "TOON_AUDIT_PEPPER_PREVIOUS must differ from TOON_SESSION_SECRET" in str(excinfo.value)


@pytest.mark.parametrize("ip_key_variable", ["TOON_AUDIT_IP_KEY", "TOON_AUDIT_IP_KEY_PREVIOUS"])
def test_audit_pepper_previous_equal_to_an_audit_ip_key_refused(monkeypatch, ip_key_variable):
    """One leaked value must not both undo the pseudonymisation and reveal full
    IP addresses, in whichever list of the two it was left behind."""
    shared = _ip_key("q")
    _set_required_env(monkeypatch, TOON_AUDIT_PEPPER_PREVIOUS=shared, **{ip_key_variable: shared})
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()
    assert "TOON_AUDIT_PEPPER_PREVIOUS must differ from TOON_AUDIT_IP_KEY and TOON_AUDIT_IP_KEY_PREVIOUS" in str(
        excinfo.value
    )
    assert shared not in _full_traceback(excinfo.value)


def test_audit_pepper_previous_listing_one_pepper_twice_refused(monkeypatch):
    _set_required_env(monkeypatch, TOON_AUDIT_PEPPER_PREVIOUS=f"{'a' * 32},{'b' * 32}, {'a' * 32}")
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()
    assert "TOON_AUDIT_PEPPER_PREVIOUS lists the same pepper twice" in str(excinfo.value)
    assert "a" * 32 not in _full_traceback(excinfo.value)


def _full_traceback(error: BaseException) -> str:
    return "".join(traceback.format_exception(error))


def test_traceback_of_a_field_error_shows_no_secret(monkeypatch):
    _set_required_env(monkeypatch, TOON_SESSION_SECRET="SUPERGEHEIM-te-kort")
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__suppress_context__ is True
    text = _full_traceback(excinfo.value)
    assert "TOON_SESSION_SECRET" in text
    assert "SUPERGEHEIM" not in text
    assert "ValidationError" not in text


def test_traceback_of_model_error_shows_no_settings_values(monkeypatch):
    _set_required_env(
        monkeypatch,
        TOON_AUDIT_PEPPER="PEPPERGEHEIM" + "y" * 32,
        TOON_OIDC_CLIENT_SECRET="super-geheim-waarde",
    )
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()
    text = _full_traceback(excinfo.value)
    assert "TOON_OIDC_CLIENT_SECRET" in text
    assert "super-geheim-waarde" not in text
    assert "PEPPERGEHEIM" not in text
    assert "input_value" not in text


def test_message_of_type_error_shows_no_value(monkeypatch):
    _set_required_env(monkeypatch, TOON_INGEST_MAX_BODY="GEHEIME-WAARDE")
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()
    text = _full_traceback(excinfo.value)
    assert "TOON_INGEST_MAX_BODY" in text
    assert "GEHEIME-WAARDE" not in text


def test_production_without_the_proxy_setting_refused(monkeypatch):
    """Both answers are valid, so neither can be the default: unset has to be
    refused, or a deployment behind a proxy silently records the proxy's
    address for every visitor."""
    _set_required_env(monkeypatch, TOON_ENVIRONMENT="productie")
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()
    assert "BEHIND_PROXY" in str(excinfo.value)


def test_production_without_a_proxy_is_accepted(monkeypatch):
    _set_required_env(
        monkeypatch,
        TOON_ENVIRONMENT="productie",
        TOON_BEHIND_PROXY="false",
        TOON_OIDC_ISS_REQUIRED="true",
        TOON_BASE_URL="https://beheer.example.nl",
    )
    assert load_settings().behind_proxy is False


def test_production_without_iss_required_refused(monkeypatch):
    _set_required_env(
        monkeypatch,
        TOON_ENVIRONMENT="productie",
        TOON_BEHIND_PROXY="true",
    )
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()
    assert "OIDC_ISS_REQUIRED" in str(excinfo.value)


def test_production_without_base_url_refused(monkeypatch):
    _set_required_env(
        monkeypatch,
        TOON_ENVIRONMENT="productie",
        TOON_BEHIND_PROXY="true",
        TOON_OIDC_ISS_REQUIRED="true",
    )
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()
    assert "BASE_URL" in str(excinfo.value)


@pytest.mark.parametrize("value", ["beheer.plak.example", "/sites", "https://", "ftp://beheer.plak.example"])
def test_base_url_without_usable_origin_refused(monkeypatch, value):
    # origin_guard.normalise_origin needs a http(s) scheme and a hostname to
    # turn this into an origin; anything else must not pass validation and
    # fall through to the dev same-origin/localhost fallback unnoticed.
    _set_required_env(monkeypatch, TOON_BASE_URL=value)
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()
    assert "BASE_URL" in str(excinfo.value)


@pytest.mark.parametrize(
    "value", ["https://beheer.plak.example", "http://beheer.plak.localhost:8080"]
)
def test_base_url_with_usable_origin_accepted(monkeypatch, value):
    _set_required_env(monkeypatch, TOON_BASE_URL=value)
    assert load_settings().base_url == value


def test_without_content_base_url_refused_in_dev_too(monkeypatch):
    """The content host is required everywhere, not only in production.

    A single-host fallback would put the interface and uploaded content on one
    origin; since the SPA sits on the root of the admin host, it would put
    them on one path space as well.
    """
    _set_required_env(monkeypatch)
    monkeypatch.delenv("TOON_CONTENT_BASE_URL")
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()
    assert "CONTENT_BASE_URL" in str(excinfo.value)


def test_unknown_environment_value_refused(monkeypatch):
    _set_required_env(monkeypatch, TOON_ENVIRONMENT="staging")
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()
    assert "TOON_ENVIRONMENT" in str(excinfo.value)


@pytest.mark.parametrize("value", ["", "plak.example", "/sites", "https://"])
def test_content_base_url_without_hostname_refused(monkeypatch, value):
    # The app derives the content host from this URL; without a hostname
    # every host comparison in the app would compare against the empty string.
    _set_required_env(monkeypatch, TOON_CONTENT_BASE_URL=value)
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()
    assert "CONTENT_BASE_URL" in str(excinfo.value)


@pytest.mark.parametrize(
    ("url", "host"),
    [
        ("https://plak.example", "plak.example"),
        ("http://plak.localhost:8080", "plak.localhost"),
        ("https://PLAK.example/sites/", "plak.example"),
    ],
)
def test_content_host_is_the_hostname_of_the_content_origin(monkeypatch, url, host):
    _set_required_env(monkeypatch, TOON_CONTENT_BASE_URL=url)
    assert load_settings().content_host == host


def test_operator_is_not_configured_by_default(monkeypatch):
    _set_required_env(monkeypatch)
    settings = load_settings()
    assert settings.operator_name == ""
    assert settings.operator_email == ""


def test_operator_name_and_email_are_read_and_stripped(monkeypatch):
    _set_required_env(
        monkeypatch,
        TOON_OPERATOR_NAME="  Digi Gilde \t",
        TOON_OPERATOR_EMAIL=" digigilde@rijksoverheid.nl\n",
    )
    settings = load_settings()
    assert settings.operator_name == "Digi Gilde"
    assert settings.operator_email == "digigilde@rijksoverheid.nl"


@pytest.mark.parametrize("value", ["", "   ", "\t\n"])
def test_operator_email_empty_after_stripping_is_not_configured(monkeypatch, value):
    _set_required_env(monkeypatch, TOON_OPERATOR_EMAIL=value)
    assert load_settings().operator_email == ""


@pytest.mark.parametrize(
    "value",
    [
        "digigilde@rijksoverheid.nl",
        "Digi.Gilde+plak@Rijksoverheid.NL",
        "beheer@localhost",
        "a" * 64 + "@" + "b" * 186 + ".nl",
    ],
)
def test_operator_email_plain_address_accepted(monkeypatch, value):
    _set_required_env(monkeypatch, TOON_OPERATOR_EMAIL=value)
    assert load_settings().operator_email == value


@pytest.mark.parametrize(
    "value",
    [
        "digigilde",
        "@rijksoverheid.nl",
        "digigilde@",
        "digi@gilde@rijksoverheid.nl",
        "digi gilde@rijksoverheid.nl",
        "digigilde@rijksoverheid.nl\nBcc: iemand@example.org",
        "digigilde@rijks\toverheid.nl",
        "digigilde\u00a0@rijksoverheid.nl",
        "\u202edigigilde@rijksoverheid.nl",
        "digi\x07gilde@rijksoverheid.nl",
        "<digigilde@rijksoverheid.nl>",
        "Digi Gilde <digigilde@rijksoverheid.nl>",
        '"digi gilde"@rijksoverheid.nl',
        "digigilde@rijksoverheid.nl,iemand@example.org",
        "digigilde@rijksoverheid.nl;iemand@example.org",
        "digigilde@rijksoverheid.nl?subject=hoi",
        "digigilde@rijksoverheid.nl&cc=iemand@example.org",
        "digigilde@rijksoverheid.nl#x",
        "digi%40gilde@rijksoverheid.nl",
        "mailto:digigilde@rijksoverheid.nl",
        "a" * 64 + "@" + "b" * 187 + ".nl",
    ],
)
def test_operator_email_that_is_not_a_plain_address_refused(monkeypatch, value):
    # The SPA puts the address in a mailto: link: anything that would end,
    # extend or disguise that link stops the app at startup.
    _set_required_env(monkeypatch, TOON_OPERATOR_EMAIL=value)
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()
    assert "TOON_OPERATOR_EMAIL" in str(excinfo.value)


def test_security_txt_ncsc_is_off_by_default(monkeypatch):
    _set_required_env(monkeypatch)
    assert load_settings().security_txt_ncsc is False


@pytest.mark.parametrize(("value", "expected"), [("true", True), ("false", False)])
def test_security_txt_ncsc_is_read_from_env(monkeypatch, value, expected):
    _set_required_env(monkeypatch, TOON_SECURITY_TXT_NCSC=value)
    assert load_settings().security_txt_ncsc is expected


def test_production_full_configured_loads(monkeypatch):
    _set_required_env(
        monkeypatch,
        TOON_ENVIRONMENT="productie",
        TOON_BEHIND_PROXY="true",
        TOON_OIDC_ISS_REQUIRED="true",
        TOON_BASE_URL="https://beheer.plak.example",
        TOON_CONTENT_BASE_URL="https://plak.example",
    )
    settings = load_settings()
    assert settings.environment == "productie"
    assert settings.base_url == "https://beheer.plak.example"
    assert settings.content_base_url == "https://plak.example"


def _production_env(monkeypatch, **overrides):
    _set_required_env(
        monkeypatch,
        TOON_ENVIRONMENT="productie",
        TOON_BEHIND_PROXY="true",
        TOON_OIDC_ISS_REQUIRED="true",
        TOON_BASE_URL="https://beheer.plak.example",
        **overrides,
    )


@pytest.mark.parametrize(
    "issuer",
    [
        "http://idp.example.org",
        "http://idp.example.org/realms/plak",
        "http://oidc.plak.localhost:8080/default",
    ],
)
def test_production_refuses_a_http_issuer(monkeypatch, issuer):
    _production_env(monkeypatch, TOON_OIDC_ISSUER=issuer)
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()
    assert "TOON_OIDC_ISSUER" in str(excinfo.value)
    assert "https" in str(excinfo.value)


@pytest.mark.parametrize(
    "issuer",
    [
        "https://localhost/realms/plak",
        "https://oidc.plak.localhost:8080/default",
        "https://127.0.0.1:8443/default",
        "https://127.9.9.9/default",
        "https://[::1]:8443/default",
    ],
)
def test_production_refuses_a_loopback_issuer(monkeypatch, issuer):
    """An https issuer on loopback is the dev mock behind a tunnel, not an IdP:
    in production it would hand the whole login to whatever listens locally."""
    _production_env(monkeypatch, TOON_OIDC_ISSUER=issuer)
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()
    assert "TOON_OIDC_ISSUER" in str(excinfo.value)
    assert "hostname" in str(excinfo.value)


@pytest.mark.parametrize(
    "issuer",
    ["https://idp.example.org", "https://idp.example.org/realms/plak", "https://IDP.example.org"],
)
def test_production_accepts_a_https_issuer_on_a_real_host(monkeypatch, issuer):
    _production_env(monkeypatch, TOON_OIDC_ISSUER=issuer)
    assert load_settings().oidc_issuer == issuer


def test_production_checks_the_issuer_derived_from_the_zad_discovery_url(monkeypatch):
    """On ZAD the issuer comes from OIDC_DISCOVERY_URL; the check has to see
    that value, not the empty TOON_OIDC_ISSUER it started from."""
    _production_env(monkeypatch, TOON_OIDC_ISSUER="")
    monkeypatch.setenv(
        "OIDC_DISCOVERY_URL",
        "http://keycloak.zad.local/realms/plak/.well-known/openid-configuration",
    )
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()
    assert "TOON_OIDC_ISSUER" in str(excinfo.value)


@pytest.mark.parametrize(
    "issuer",
    ["http://oidc.plak.localhost:8080/default", "http://localhost:8081/default"],
)
def test_dev_keeps_accepting_the_http_mock_issuer(monkeypatch, issuer):
    _set_required_env(monkeypatch, TOON_OIDC_ISSUER=issuer)
    assert load_settings().oidc_issuer == issuer


def test_spa_path_from_env(monkeypatch):
    _set_required_env(monkeypatch, TOON_SPA_PATH="/app/spa")
    assert load_settings().spa_path == Path("/app/spa")


class TestOidcClientAuth:
    def test_default_is_private_key_jwt(self, monkeypatch):
        _set_required_env(monkeypatch)
        settings = load_settings()
        assert settings.oidc_client_auth == "private_key_jwt"
        assert settings.oidc_client_secret == ""

    @pytest.mark.parametrize("method_", ["client_secret_post", "client_secret_basic"])
    def test_client_secret_methods_load_with_secret(self, monkeypatch, method_):
        _set_required_env(
            monkeypatch,
            TOON_OIDC_CLIENT_AUTH=method_,
            TOON_OIDC_CLIENT_PRIVATE_JWK="",
            TOON_OIDC_CLIENT_SECRET="geheim-123",
        )
        settings = load_settings()
        assert settings.oidc_client_auth == method_
        assert settings.oidc_client_secret == "geheim-123"

    def test_unknown_method_refused(self, monkeypatch):
        _set_required_env(monkeypatch, TOON_OIDC_CLIENT_AUTH="client_secret_jwt")
        with pytest.raises(ConfigurationError) as excinfo:
            load_settings()
        assert "TOON_OIDC_CLIENT_AUTH" in str(excinfo.value)

    def test_private_key_jwt_without_jwk_refused(self, monkeypatch):
        _set_required_env(monkeypatch, TOON_OIDC_CLIENT_PRIVATE_JWK="")
        with pytest.raises(ConfigurationError) as excinfo:
            load_settings()
        assert "TOON_OIDC_CLIENT_PRIVATE_JWK" in str(excinfo.value)

    def test_private_key_jwt_with_secret_refused_without_secret_value_in_message(self, monkeypatch):
        _set_required_env(monkeypatch, TOON_OIDC_CLIENT_SECRET="super-geheim-waarde")
        with pytest.raises(ConfigurationError) as excinfo:
            load_settings()
        message = str(excinfo.value)
        assert "TOON_OIDC_CLIENT_SECRET" in message
        assert "super-geheim-waarde" not in message

    @pytest.mark.parametrize("method_", ["client_secret_post", "client_secret_basic"])
    def test_client_secret_method_without_secret_refused(self, monkeypatch, method_):
        _set_required_env(
            monkeypatch, TOON_OIDC_CLIENT_AUTH=method_, TOON_OIDC_CLIENT_PRIVATE_JWK=""
        )
        with pytest.raises(ConfigurationError) as excinfo:
            load_settings()
        assert "TOON_OIDC_CLIENT_SECRET" in str(excinfo.value)

    def test_client_secret_method_with_jwk_refused_without_key_in_message(self, monkeypatch):
        _set_required_env(
            monkeypatch,
            TOON_OIDC_CLIENT_AUTH="client_secret_post",
            TOON_OIDC_CLIENT_PRIVATE_JWK='{"kty": "RSA", "d": "prive-exponent"}',
            TOON_OIDC_CLIENT_SECRET="geheim-123",
        )
        with pytest.raises(ConfigurationError) as excinfo:
            load_settings()
        message = str(excinfo.value)
        assert "TOON_OIDC_CLIENT_PRIVATE_JWK" in message
        assert "prive-exponent" not in message
        assert "geheim-123" not in message


class TestRequiredAcr:
    def test_missing_is_empty(self, monkeypatch):
        _set_required_env(monkeypatch)
        monkeypatch.delenv("TOON_OIDC_REQUIRED_ACR")
        assert load_settings().oidc_required_acr == ""

    def test_empty_in_dev_loads(self, monkeypatch):
        _set_required_env(monkeypatch, TOON_OIDC_REQUIRED_ACR="")
        assert load_settings().oidc_required_acr == ""

    def test_empty_in_production_loads(self, monkeypatch):
        _set_required_env(
            monkeypatch,
            TOON_OIDC_REQUIRED_ACR="",
            TOON_ENVIRONMENT="productie",
            TOON_BEHIND_PROXY="true",
            TOON_OIDC_ISS_REQUIRED="true",
            TOON_BASE_URL="https://beheer.plak.example",
            TOON_CONTENT_BASE_URL="https://plak.example",
        )
        settings = load_settings()
        assert settings.environment == "productie"
        assert settings.oidc_required_acr == ""

    def test_other_production_requirements_stay_fail_fast_on_empty_acr(self, monkeypatch):
        _set_required_env(
            monkeypatch,
            TOON_OIDC_REQUIRED_ACR="",
            TOON_ENVIRONMENT="productie",
            TOON_BEHIND_PROXY="true",
            TOON_BASE_URL="https://beheer.plak.example",
            TOON_CONTENT_BASE_URL="https://plak.example",
        )
        with pytest.raises(ConfigurationError) as excinfo:
            load_settings()
        assert "OIDC_ISS_REQUIRED" in str(excinfo.value)


class TestZadSourceMapping:
    """The ZAD Keycloak service injects OIDC_DISCOVERY_URL, OIDC_CLIENT_ID and
    OIDC_CLIENT_SECRET (without a prefix)."""

    DISCOVERY_URL = "https://keycloak.rijksapp.nl/realms/plak/.well-known/openid-configuration"

    def _set_zad_env(self, monkeypatch, **overrides):
        values = {
            "TOON_DB_URL": "postgresql+asyncpg://gebruiker:wachtwoord@localhost/plak",
            "TOON_CONTENT_ROOT": "/var/lib/plak/content",
            "TOON_SESSION_SECRET": "x" * 32,
            "TOON_AUDIT_PEPPER": "y" * 32,
            "TOON_AUDIT_IP_KEY": base64.b64encode(b"z" * 32).decode(),
            "TOON_OIDC_CLIENT_AUTH": "client_secret_post",
            "TOON_CONTENT_BASE_URL": "https://plak.example",
            "TOON_ENVIRONMENT": "dev",
            "OIDC_DISCOVERY_URL": self.DISCOVERY_URL,
            "OIDC_CLIENT_ID": "plak-prd",
            "OIDC_CLIENT_SECRET": "zad-geheim",
        }
        values.update(overrides)
        for key, value in values.items():
            monkeypatch.setenv(key, value)

    def test_zad_variables_fill_the_oidc_settings(self, monkeypatch):
        self._set_zad_env(monkeypatch)
        settings = load_settings()
        assert settings.oidc_issuer == "https://keycloak.rijksapp.nl/realms/plak"
        assert settings.oidc_client_id == "plak-prd"
        assert settings.oidc_client_secret == "zad-geheim"
        assert settings.oidc_client_auth == "client_secret_post"

    def test_toon_variables_go_for_zad_variables(self, monkeypatch):
        self._set_zad_env(
            monkeypatch,
            TOON_OIDC_ISSUER="https://idp.example.org",
            TOON_OIDC_CLIENT_ID="plak-eigen",
            TOON_OIDC_CLIENT_SECRET="eigen-geheim",
        )
        settings = load_settings()
        assert settings.oidc_issuer == "https://idp.example.org"
        assert settings.oidc_client_id == "plak-eigen"
        assert settings.oidc_client_secret == "eigen-geheim"

    def test_the_old_variables_go_for_zad_variables_too(self, monkeypatch):
        self._set_zad_env(
            monkeypatch,
            PLAK_OIDC_ISSUER="https://old-idp.example.org",
            PLAK_OIDC_CLIENT_ID="old-client",
            PLAK_OIDC_CLIENT_SECRET="old-secret",
        )
        settings = load_settings()
        assert settings.oidc_issuer == "https://old-idp.example.org"
        assert settings.oidc_client_id == "old-client"
        assert settings.oidc_client_secret == "old-secret"

    def test_zad_secret_not_taken_over_under_private_key_jwt(self, monkeypatch):
        self._set_zad_env(
            monkeypatch,
            TOON_OIDC_CLIENT_AUTH="private_key_jwt",
            TOON_OIDC_CLIENT_PRIVATE_JWK='{"kty": "RSA"}',
        )
        settings = load_settings()
        assert settings.oidc_client_auth == "private_key_jwt"
        assert settings.oidc_client_secret == ""
        assert settings.oidc_issuer == "https://keycloak.rijksapp.nl/realms/plak"

    def test_without_zad_and_without_toon_issuer_refused(self, monkeypatch):
        self._set_zad_env(monkeypatch)
        monkeypatch.delenv("OIDC_DISCOVERY_URL")
        with pytest.raises(ConfigurationError) as excinfo:
            load_settings()
        assert "TOON_OIDC_ISSUER" in str(excinfo.value)

    def test_without_zad_and_without_toon_client_id_refused(self, monkeypatch):
        self._set_zad_env(monkeypatch)
        monkeypatch.delenv("OIDC_CLIENT_ID")
        with pytest.raises(ConfigurationError) as excinfo:
            load_settings()
        assert "TOON_OIDC_CLIENT_ID" in str(excinfo.value)

    def test_discovery_url_without_well_known_suffix_refused(self, monkeypatch):
        self._set_zad_env(monkeypatch, OIDC_DISCOVERY_URL="https://keycloak.rijksapp.nl/realms/plak")
        with pytest.raises(ConfigurationError) as excinfo:
            load_settings()
        message = str(excinfo.value)
        assert "OIDC_DISCOVERY_URL" in message
        assert "TOON_OIDC_ISSUER" in message


def test_store_compressed_defaults_to_on(monkeypatch):
    _set_required_env(monkeypatch)
    assert load_settings().store_compressed is True


def test_store_compressed_can_be_turned_off(monkeypatch):
    _set_required_env(monkeypatch, TOON_STORE_COMPRESSED="false")
    assert load_settings().store_compressed is False


# --- The old PLAK_ names --------------------------------------------------------------


def _set_old_env(monkeypatch, **overrides):
    """The required settings under the PLAK_ names alone, the way a deployment
    from before the rename has them."""
    _set_required_env(monkeypatch)
    for key in [key for key in os.environ if key.startswith("TOON_")]:
        monkeypatch.setenv("PLAK_" + key.removeprefix("TOON_"), os.environ[key])
        monkeypatch.delenv(key)
    for key, value in overrides.items():
        monkeypatch.setenv(key, value)


def _refusal_without(*secrets: str) -> str:
    """The message of the refusal, after checking that nothing of it, traceback
    included, shows one of these values."""
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()
    text = _full_traceback(excinfo.value)
    for secret in secrets:
        assert secret not in text
    return str(excinfo.value)


class TestOldPrefix:
    """A setting reads TOON_<NAME> when that is present, else PLAK_<NAME>, else
    its default."""

    # Name, attribute, then a value under each prefix: as written and as read.
    SETTINGS = (
        ("OPERATOR_NAME", "operator_name", "New Operator", "New Operator", "Old Operator", "Old Operator"),
        ("SITE_MAX_BYTES", "site_max_bytes", "2000", 2000, "1000", 1000),
        ("STORE_COMPRESSED", "store_compressed", "true", True, "false", False),
        ("BEHIND_PROXY", "behind_proxy", "false", False, "true", True),
        ("SPA_PATH", "spa_path", "/new/spa", Path("/new/spa"), "/old/spa", Path("/old/spa")),
        (
            "BASE_URL",
            "base_url",
            "https://new.example.org",
            "https://new.example.org",
            "https://old.example.org",
            "https://old.example.org",
        ),
        ("AUDIT_PEPPER_PREVIOUS", "audit_pepper_previous", "n" * 32, "n" * 32, "o" * 32, "o" * 32),
        ("AUDIT_IP_KEY_PREVIOUS", "audit_ip_key_previous", _ip_key("n"), _ip_key("n"), _ip_key("o"), _ip_key("o")),
    )

    # A production pepper can be anything of 32 bytes or more.
    AWKWARD_PEPPERS = (
        "  an old pepper, with a comma and spaces around it  ",
        "," * 32,
        " " * 32,
        "\t\n" * 16,
        "é" * 16,
        "'a pepper in quotes, of 32 bytes or more'",
        '{"looks": "like JSON, but is a pepper"}',
    )

    def test_a_deployment_with_only_the_old_names_keeps_starting(self, monkeypatch):
        _set_old_env(monkeypatch)
        settings = load_settings()
        assert settings.db_url == "postgresql+asyncpg://gebruiker:wachtwoord@localhost/plak"
        assert settings.content_root == Path("/var/lib/plak/content")
        assert settings.session_secret == "x" * 32
        assert settings.audit_peppers == ("y" * 32,)
        assert settings.audit_ip_key_bytes == b"z" * 32
        assert settings.audit_ip_keys_previous_bytes == ()
        assert settings.environment == "dev"

    @pytest.mark.parametrize(("name", "attribute", "_new", "_read_new", "old", "read_old"), SETTINGS)
    def test_the_old_name_alone_is_read(self, monkeypatch, name, attribute, _new, _read_new, old, read_old):
        _set_required_env(monkeypatch, **{f"PLAK_{name}": old})
        assert getattr(load_settings(), attribute) == read_old

    @pytest.mark.parametrize(("name", "attribute", "new", "read_new", "old", "_read_old"), SETTINGS)
    def test_the_new_name_wins_over_the_old_one(self, monkeypatch, name, attribute, new, read_new, old, _read_old):
        _set_required_env(monkeypatch, **{f"TOON_{name}": new, f"PLAK_{name}": old})
        assert getattr(load_settings(), attribute) == read_new

    def test_an_empty_new_value_is_present_and_wins(self, monkeypatch):
        _set_required_env(monkeypatch, TOON_OIDC_REQUIRED_ACR="", PLAK_OIDC_REQUIRED_ACR="urn:acr:old")
        assert load_settings().oidc_required_acr == ""

    def test_a_new_audit_secret_shadows_the_old_one_and_leaves_no_previous_value(self, monkeypatch):
        """The two audit secrets have no rule of their own: under a new value the
        old one is not read at all. Reaching rows written under it takes listing
        it as a previous value, or setting the same value under the new name."""
        _set_required_env(monkeypatch, PLAK_AUDIT_PEPPER="o" * 32, PLAK_AUDIT_IP_KEY=_ip_key("o"))
        settings = load_settings()
        assert settings.audit_peppers == ("y" * 32,)
        assert settings.audit_ip_key_bytes == b"z" * 32
        assert settings.audit_ip_keys_previous_bytes == ()

    @pytest.mark.parametrize("pepper", AWKWARD_PEPPERS)
    @pytest.mark.parametrize("variable", ["TOON_AUDIT_PEPPER", "PLAK_AUDIT_PEPPER"])
    def test_any_value_of_32_bytes_works_as_the_pepper_under_either_name(self, monkeypatch, variable, pepper):
        """Taken verbatim under both names: read any other way, a pepper with a
        comma or outer whitespace would change every pseudonym it ever made."""
        _set_required_env(monkeypatch)
        monkeypatch.delenv("TOON_AUDIT_PEPPER")
        monkeypatch.setenv(variable, pepper)
        settings = load_settings()
        assert settings.audit_pepper == pepper
        assert settings.audit_peppers == (pepper,)

    def test_a_setting_missing_under_both_names_is_refused_under_the_new_name(self, monkeypatch):
        _set_old_env(monkeypatch)
        monkeypatch.delenv("PLAK_DB_URL")
        message = _refusal_without()
        assert "TOON_DB_URL: Field required" in message
        assert "PLAK_DB_URL" not in message

    @pytest.mark.parametrize(
        ("old_name", "value", "new_name"),
        [
            ("PLAK_ENVIRONMENT", "staging", "TOON_ENVIRONMENT"),
            ("PLAK_INGEST_MAX_BODY", "not-a-number", "TOON_INGEST_MAX_BODY"),
            ("PLAK_BASE_URL", "not-a-url", "TOON_BASE_URL"),
        ],
    )
    def test_a_bad_value_under_the_old_name_goes_through_the_same_validation(
        self, monkeypatch, old_name, value, new_name
    ):
        _set_old_env(monkeypatch, **{old_name: value})
        message = _refusal_without()
        assert new_name in message
        assert old_name not in message

    @pytest.mark.parametrize(
        ("old_name", "value", "new_name"),
        [
            ("PLAK_SESSION_SECRET", "a-secret-too-short", "TOON_SESSION_SECRET"),
            ("PLAK_AUDIT_PEPPER", "a-secret-too-short", "TOON_AUDIT_PEPPER"),
            ("PLAK_AUDIT_PEPPER_PREVIOUS", "a-secret-too-short", "TOON_AUDIT_PEPPER_PREVIOUS"),
            ("PLAK_AUDIT_IP_KEY", "a-secret-not-base64", "TOON_AUDIT_IP_KEY"),
            ("PLAK_AUDIT_IP_KEY_PREVIOUS", "a-secret-not-base64", "TOON_AUDIT_IP_KEY_PREVIOUS"),
        ],
    )
    def test_a_bad_secret_under_the_old_name_is_refused_without_showing_it(
        self, monkeypatch, old_name, value, new_name
    ):
        _set_old_env(monkeypatch, **{old_name: value})
        message = _refusal_without(value)
        assert new_name in message
        assert old_name not in message

    @pytest.mark.parametrize(
        ("overrides", "refusal"),
        [
            ({"PLAK_AUDIT_PEPPER": "x" * 32}, "TOON_AUDIT_PEPPER must differ from TOON_SESSION_SECRET"),
            ({"TOON_SESSION_SECRET": "y" * 32}, "TOON_AUDIT_PEPPER must differ from TOON_SESSION_SECRET"),
            (
                {"PLAK_AUDIT_PEPPER": _ip_key("z")},
                "TOON_AUDIT_IP_KEY must differ from TOON_SESSION_SECRET and TOON_AUDIT_PEPPER",
            ),
            (
                {"TOON_AUDIT_IP_KEY": _ip_key("q"), "PLAK_AUDIT_PEPPER": _ip_key("q")},
                "TOON_AUDIT_IP_KEY must differ from TOON_SESSION_SECRET and TOON_AUDIT_PEPPER",
            ),
        ],
        ids=["old-with-old", "old-with-new", "old-pepper-old-key", "old-pepper-new-key"],
    )
    def test_the_secrets_stay_distinct_across_the_two_prefixes(self, monkeypatch, overrides, refusal):
        """What counts is the value each setting ends up with, under whichever
        name it came."""
        _set_old_env(monkeypatch, **overrides)
        assert refusal in _refusal_without(*overrides.values())

    @pytest.mark.parametrize(
        "old_values",
        [
            {"PLAK_AUDIT_PEPPER": "too-short"},
            {"PLAK_AUDIT_PEPPER": "x" * 32},
            {"PLAK_AUDIT_IP_KEY": "not-base64!!!"},
            {"PLAK_AUDIT_IP_KEY": "y" * 44},
            {"PLAK_SESSION_SECRET": "too-short"},
            {"PLAK_ENVIRONMENT": "staging"},
        ],
        ids=["short-pepper", "pepper-is-session-secret", "key-no-base64", "key-is-pepper", "short-secret", "bad-value"],
    )
    def test_a_shadowed_old_value_is_not_read_so_not_refused(self, monkeypatch, old_values):
        _set_required_env(monkeypatch, **old_values)
        settings = load_settings()
        assert settings.audit_peppers == ("y" * 32,)
        assert settings.audit_ip_keys_previous_bytes == ()
        assert settings.environment == "dev"

    def test_the_production_requirements_hold_under_the_old_names(self, monkeypatch):
        _set_old_env(monkeypatch, PLAK_ENVIRONMENT="productie")
        assert "TOON_BEHIND_PROXY is required" in _refusal_without()

    def test_a_field_read_by_its_alias_is_not_read_under_a_prefix(self, monkeypatch):
        _set_required_env(monkeypatch, TOON_ZAD_OIDC_CLIENT_ID="new-client", PLAK_ZAD_OIDC_CLIENT_ID="old-client")
        assert load_settings().zad_oidc_client_id == ""


class TestOldVariablesInUse:
    def test_none_with_only_the_new_names(self, monkeypatch):
        _set_required_env(monkeypatch)
        assert load_settings().old_variables_in_use() == []

    def test_every_setting_read_through_the_fallback_is_listed_and_sorted(self, monkeypatch):
        _set_old_env(monkeypatch)
        assert load_settings().old_variables_in_use() == [
            "PLAK_AUDIT_IP_KEY",
            "PLAK_AUDIT_PEPPER",
            "PLAK_CONTENT_BASE_URL",
            "PLAK_CONTENT_ROOT",
            "PLAK_DB_URL",
            "PLAK_ENVIRONMENT",
            "PLAK_OIDC_CLIENT_ID",
            "PLAK_OIDC_CLIENT_PRIVATE_JWK",
            "PLAK_OIDC_ISSUER",
            "PLAK_OIDC_REQUIRED_ACR",
            "PLAK_SESSION_SECRET",
        ]

    def test_an_empty_old_value_read_through_the_fallback_is_listed(self, monkeypatch):
        """It beats the default just as a value does, so taking it away changes
        what the app does."""
        _set_required_env(monkeypatch, PLAK_SITE_MAX_BYTES="0", PLAK_OPERATOR_NAME="")
        assert load_settings().old_variables_in_use() == ["PLAK_OPERATOR_NAME", "PLAK_SITE_MAX_BYTES"]

    @pytest.mark.parametrize(
        "old_values",
        [
            {"PLAK_DB_URL": "postgresql+asyncpg://old@localhost/old"},
            {"PLAK_SESSION_SECRET": "o" * 32},
            {"PLAK_AUDIT_PEPPER": "o" * 32},
            {"PLAK_AUDIT_PEPPER": "y" * 32},
            {"PLAK_AUDIT_IP_KEY": _ip_key("o")},
            {"PLAK_AUDIT_IP_KEY": _ip_key("z")},
        ],
        ids=["setting", "session-secret", "other-pepper", "same-pepper", "other-key", "same-key"],
    )
    def test_an_old_name_that_is_shadowed_is_not_listed(self, monkeypatch, old_values):
        _set_required_env(monkeypatch, **old_values)
        assert load_settings().old_variables_in_use() == []

    def test_an_old_name_shadowed_by_an_empty_new_value_is_not_listed(self, monkeypatch):
        _set_required_env(monkeypatch, TOON_OPERATOR_NAME="", PLAK_OPERATOR_NAME="Old Operator")
        assert load_settings().old_variables_in_use() == []

    @pytest.mark.parametrize(
        "name",
        [
            "PLAK_HOST",
            "PLAK_SITE_ID",
            "PLAK_ACCESS_TOKEN",
            "PLAK_CONFIG_DIR",
            "PLAK_BASE_PATH",
            "PLAK_TOKEN",
            "PLAK_ADMIN_URL",
            "PLAK_TRUSTED_PROXIES",
            "PLAK_ZAD_OIDC_CLIENT_ID",
        ],
    )
    def test_an_old_name_that_is_no_server_setting_is_not_listed(self, monkeypatch, name):
        _set_required_env(monkeypatch, **{name: "something"})
        assert load_settings().old_variables_in_use() == []

    def test_a_name_counts_whatever_its_case_as_the_settings_read_it(self, monkeypatch):
        """pydantic-settings matches a variable whatever its case, so the report
        has to: by exact name it would miss the first and list the second."""
        _set_required_env(
            monkeypatch,
            plak_operator_name="Old Operator",
            PLAK_SITE_MAX_BYTES="1000",
            toon_site_max_bytes="2000",
        )
        settings = load_settings()
        assert settings.operator_name == "Old Operator"
        assert settings.site_max_bytes == 2000
        assert settings.old_variables_in_use() == ["PLAK_OPERATOR_NAME"]


class TestOldAuditVariablesLeftBehind:
    """`_set_required_env` holds pepper `y` and IP key `z` under the new names."""

    OTHER_KEY = _ip_key("o")
    ANOTHER_KEY = _ip_key("p")

    def test_none_with_only_the_new_names(self, monkeypatch):
        _set_required_env(monkeypatch)
        assert load_settings().old_audit_variables_left_behind() == []

    def test_none_with_only_the_old_names(self, monkeypatch):
        _set_old_env(monkeypatch, PLAK_AUDIT_PEPPER_PREVIOUS="o" * 32, PLAK_AUDIT_IP_KEY_PREVIOUS=self.OTHER_KEY)
        assert load_settings().old_audit_variables_left_behind() == []

    def test_a_move_with_the_same_values_leaves_nothing_behind(self, monkeypatch):
        _set_required_env(monkeypatch, PLAK_AUDIT_PEPPER="y" * 32, PLAK_AUDIT_IP_KEY=_ip_key("z"))
        assert load_settings().old_audit_variables_left_behind() == []

    @pytest.mark.parametrize("pepper", TestOldPrefix.AWKWARD_PEPPERS)
    def test_an_awkward_pepper_compares_verbatim(self, monkeypatch, pepper):
        """Split or stripped, a correct move of such a pepper would be reported."""
        _set_required_env(monkeypatch, TOON_AUDIT_PEPPER=pepper, PLAK_AUDIT_PEPPER=pepper)
        assert load_settings().old_audit_variables_left_behind() == []

    def test_another_spelling_of_the_same_ip_key_is_the_same_key(self, monkeypatch):
        _set_required_env(monkeypatch, PLAK_AUDIT_IP_KEY=_respelled(_ip_key("z")))
        assert load_settings().old_audit_variables_left_behind() == []

    def test_another_new_value_leaves_the_old_one_behind(self, monkeypatch):
        _set_required_env(monkeypatch, PLAK_AUDIT_PEPPER="o" * 32, PLAK_AUDIT_IP_KEY=self.OTHER_KEY)
        assert load_settings().old_audit_variables_left_behind() == ["PLAK_AUDIT_IP_KEY", "PLAK_AUDIT_PEPPER"]

    @pytest.mark.parametrize(
        ("old_values", "expected"),
        [
            ({"PLAK_AUDIT_PEPPER": "o" * 32}, ["PLAK_AUDIT_PEPPER"]),
            ({"PLAK_AUDIT_IP_KEY": OTHER_KEY}, ["PLAK_AUDIT_IP_KEY"]),
        ],
        ids=["pepper", "ip-key"],
    )
    def test_one_value_left_behind_names_that_one_only(self, monkeypatch, old_values, expected):
        _set_required_env(monkeypatch, **old_values)
        assert load_settings().old_audit_variables_left_behind() == expected

    def test_a_rotation_that_lists_the_old_value_leaves_nothing_behind(self, monkeypatch):
        _set_required_env(
            monkeypatch,
            PLAK_AUDIT_PEPPER="o" * 32,
            PLAK_AUDIT_IP_KEY=self.OTHER_KEY,
            TOON_AUDIT_PEPPER_PREVIOUS="o" * 32,
            TOON_AUDIT_IP_KEY_PREVIOUS=self.OTHER_KEY,
        )
        assert load_settings().old_audit_variables_left_behind() == []

    def test_an_old_list_still_read_through_the_fallback_leaves_nothing_behind(self, monkeypatch):
        _set_required_env(
            monkeypatch,
            PLAK_AUDIT_PEPPER="o" * 32,
            PLAK_AUDIT_IP_KEY=self.OTHER_KEY,
            PLAK_AUDIT_PEPPER_PREVIOUS="o" * 32,
            PLAK_AUDIT_IP_KEY_PREVIOUS=self.OTHER_KEY,
        )
        assert load_settings().old_audit_variables_left_behind() == []

    def test_an_empty_new_list_leaves_the_old_list_behind(self, monkeypatch):
        """Present includes empty, so this list wins and holds nothing."""
        _set_required_env(
            monkeypatch,
            TOON_AUDIT_PEPPER_PREVIOUS="",
            TOON_AUDIT_IP_KEY_PREVIOUS="",
            PLAK_AUDIT_PEPPER_PREVIOUS="o" * 32,
            PLAK_AUDIT_IP_KEY_PREVIOUS=self.OTHER_KEY,
        )
        assert load_settings().old_audit_variables_left_behind() == [
            "PLAK_AUDIT_IP_KEY_PREVIOUS",
            "PLAK_AUDIT_PEPPER_PREVIOUS",
        ]

    def test_a_new_list_that_drops_one_entry_leaves_the_old_list_behind(self, monkeypatch):
        _set_required_env(
            monkeypatch,
            TOON_AUDIT_PEPPER_PREVIOUS="o" * 32,
            TOON_AUDIT_IP_KEY_PREVIOUS=self.OTHER_KEY,
            PLAK_AUDIT_PEPPER_PREVIOUS=f"{'o' * 32},{'p' * 32}",
            PLAK_AUDIT_IP_KEY_PREVIOUS=f"{self.OTHER_KEY},{self.ANOTHER_KEY}",
        )
        assert load_settings().old_audit_variables_left_behind() == [
            "PLAK_AUDIT_IP_KEY_PREVIOUS",
            "PLAK_AUDIT_PEPPER_PREVIOUS",
        ]

    def test_a_new_list_in_another_order_leaves_nothing_behind(self, monkeypatch):
        _set_required_env(
            monkeypatch,
            TOON_AUDIT_PEPPER_PREVIOUS=f"{'p' * 32},{'o' * 32}",
            TOON_AUDIT_IP_KEY_PREVIOUS=f"{self.ANOTHER_KEY},{self.OTHER_KEY}",
            PLAK_AUDIT_PEPPER_PREVIOUS=f"{'o' * 32},{'p' * 32}",
            PLAK_AUDIT_IP_KEY_PREVIOUS=f"{self.OTHER_KEY},{self.ANOTHER_KEY}",
        )
        assert load_settings().old_audit_variables_left_behind() == []

    def test_an_old_value_moved_to_the_other_place_is_still_there(self, monkeypatch):
        """The old previous value became the current one under the new name. The
        empty new lists keep the settings from reading the old ones, which they
        would refuse for holding the current value."""
        _set_required_env(
            monkeypatch,
            TOON_AUDIT_PEPPER_PREVIOUS="",
            TOON_AUDIT_IP_KEY_PREVIOUS="",
            PLAK_AUDIT_PEPPER_PREVIOUS="y" * 32,
            PLAK_AUDIT_IP_KEY_PREVIOUS=_ip_key("z"),
        )
        assert load_settings().old_audit_variables_left_behind() == []

    @pytest.mark.parametrize(
        "old_values",
        [
            {"PLAK_AUDIT_PEPPER": ""},
            {"PLAK_AUDIT_IP_KEY": ""},
            {"PLAK_AUDIT_PEPPER_PREVIOUS": ""},
            {"PLAK_AUDIT_IP_KEY_PREVIOUS": " , "},
        ],
        ids=["pepper", "ip-key", "pepper-list", "ip-key-list"],
    )
    def test_an_empty_old_value_held_nothing(self, monkeypatch, old_values):
        _set_required_env(monkeypatch, **old_values)
        assert load_settings().old_audit_variables_left_behind() == []

    @pytest.mark.parametrize(
        "old_values",
        [
            {"PLAK_AUDIT_IP_KEY": "not-base64!!!"},
            {"PLAK_AUDIT_IP_KEY": "y" * 44},
            {"TOON_AUDIT_IP_KEY_PREVIOUS": "", "PLAK_AUDIT_IP_KEY_PREVIOUS": "not-base64!!!"},
            {"TOON_AUDIT_IP_KEY_PREVIOUS": "", "PLAK_AUDIT_IP_KEY_PREVIOUS": "y" * 44},
        ],
        ids=["not-base64", "wrong-length", "list-not-base64", "list-wrong-length"],
    )
    def test_an_old_value_that_is_no_ip_key_held_no_rows(self, monkeypatch, old_values):
        """The app refuses such a value, so nothing was written under it. Shadowed
        it is not read, and it must not stop the start here either."""
        _set_required_env(monkeypatch, **old_values)
        assert load_settings().old_audit_variables_left_behind() == []

    def test_a_name_counts_whatever_its_case_as_the_settings_read_it(self, monkeypatch):
        _set_required_env(monkeypatch, plak_audit_pepper="o" * 32)
        assert load_settings().old_audit_variables_left_behind() == ["PLAK_AUDIT_PEPPER"]


class TestServerVariable:
    """The rule of Settings, for what reads a variable without loading them all."""

    def test_the_new_name_wins(self, monkeypatch):
        monkeypatch.setenv("TOON_DB_URL", "new")
        monkeypatch.setenv("PLAK_DB_URL", "old")
        assert server_variable("TOON_DB_URL") == "new"

    def test_an_empty_new_value_is_present_and_wins(self, monkeypatch):
        monkeypatch.setenv("TOON_DB_URL", "")
        monkeypatch.setenv("PLAK_DB_URL", "old")
        assert server_variable("TOON_DB_URL") == ""

    def test_the_old_name_alone_is_read(self, monkeypatch):
        monkeypatch.setenv("PLAK_DB_URL", "old")
        assert server_variable("TOON_DB_URL") == "old"

    def test_neither_name_is_none(self):
        assert server_variable("TOON_DB_URL") is None

    def test_another_name_is_not_read(self, monkeypatch):
        monkeypatch.setenv("PLAK_CONTENT_ROOT", "old")
        assert server_variable("TOON_DB_URL") is None


_NO_CHAINS = checkpoint.Checkpoint(taken_at=datetime(2026, 11, 1, tzinfo=UTC), heads=())
_AUDIT_JOBS = pytest.mark.parametrize(
    ("module", "job", "result", "run"),
    [
        (chain, "verify", [], chain.main),
        (checkpoint, "collect", _NO_CHAINS, lambda: checkpoint.main([])),
        (retention, "purge", 0, retention.main),
    ],
    ids=["chain", "checkpoint", "retention"],
)


def _record_dsn(monkeypatch, module, job, result) -> list[str]:
    seen: list[str] = []

    async def recording_job(dsn: str):
        seen.append(dsn)
        return result

    monkeypatch.setattr(module, job, recording_job)
    return seen


@_AUDIT_JOBS
@pytest.mark.parametrize(
    ("environment", "expected"),
    [({"TOON_DB_URL": "new", "PLAK_DB_URL": "old"}, "new"), ({"PLAK_DB_URL": "old"}, "old")],
    ids=["both-names", "old-name-alone"],
)
def test_an_audit_job_reads_the_database_url_by_that_rule(monkeypatch, module, job, result, run, environment, expected):
    seen = _record_dsn(monkeypatch, module, job, result)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    assert run() == 0
    assert seen == [expected]


@_AUDIT_JOBS
@pytest.mark.parametrize(
    "environment",
    [{}, {"TOON_DB_URL": "", "PLAK_DB_URL": "old"}],
    ids=["neither-name", "empty-new-value"],
)
def test_an_audit_job_without_a_database_url_refuses_and_names_the_new_one(
    monkeypatch, caplog, module, job, result, run, environment
):
    seen = _record_dsn(monkeypatch, module, job, result)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    assert run() == 1
    assert seen == []
    assert "TOON_DB_URL is not set" in caplog.text
