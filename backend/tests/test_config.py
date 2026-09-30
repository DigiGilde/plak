import base64
import os
import traceback
from pathlib import Path

import pytest

from plak.config import ConfigurationError, load_settings

ZAD_OIDC_VARIABLES = ("OIDC_DISCOVERY_URL", "OIDC_CLIENT_ID", "OIDC_CLIENT_SECRET")


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch):
    for key in list(os.environ):
        if key.startswith("PLAK_") or key in ZAD_OIDC_VARIABLES:
            monkeypatch.delenv(key, raising=False)


def _set_required_env(monkeypatch, **overrides):
    values = {
        "PLAK_DB_URL": "postgresql+asyncpg://gebruiker:wachtwoord@localhost/plak",
        "PLAK_CONTENT_ROOT": "/var/lib/plak/content",
        "PLAK_OIDC_ISSUER": "https://idp.example.org",
        "PLAK_OIDC_CLIENT_ID": "plak",
        "PLAK_OIDC_CLIENT_PRIVATE_JWK": '{"kty": "RSA"}',
        "PLAK_OIDC_REQUIRED_ACR": "urn:acr:hoog",
        "PLAK_SESSION_SECRET": "x" * 32,
        "PLAK_AUDIT_PEPPER": "y" * 32,
        "PLAK_AUDIT_IP_KEY": base64.b64encode(b"z" * 32).decode(),
        "PLAK_CONTENT_BASE_URL": "https://plak.example",
        "PLAK_ENVIRONMENT": "dev",
    }
    values.update(overrides)
    for key, value in values.items():
        monkeypatch.setenv(key, value)


def test_missing_required_env_fails_with_clear_message(monkeypatch):
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()
    assert "PLAK_DB_URL" in str(excinfo.value)


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
    assert settings.storage_min_free_bytes == 100 * 1024 * 1024


def test_too_short_session_secret_refused(monkeypatch):
    _set_required_env(monkeypatch, PLAK_SESSION_SECRET="te-kort")
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()
    assert "PLAK_SESSION_SECRET" in str(excinfo.value)


def test_too_short_audit_pepper_refused(monkeypatch):
    _set_required_env(monkeypatch, PLAK_AUDIT_PEPPER="te-kort")
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()
    assert "PLAK_AUDIT_PEPPER" in str(excinfo.value)


def test_audit_lookup_daily_limit_defaults_to_25(monkeypatch):
    _set_required_env(monkeypatch)
    assert load_settings().audit_lookup_daily_limit == 25


def test_audit_lookup_daily_limit_from_env(monkeypatch):
    _set_required_env(monkeypatch, PLAK_AUDIT_LOOKUP_DAILY_LIMIT="5")
    assert load_settings().audit_lookup_daily_limit == 5


def test_audit_lookup_daily_limit_below_one_refused(monkeypatch):
    _set_required_env(monkeypatch, PLAK_AUDIT_LOOKUP_DAILY_LIMIT="0")
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()
    assert "AUDIT_LOOKUP_DAILY_LIMIT" in str(excinfo.value)


def test_audit_lookup_daily_limit_above_upper_bound_refused(monkeypatch):
    _set_required_env(monkeypatch, PLAK_AUDIT_LOOKUP_DAILY_LIMIT="1001")
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()
    assert "AUDIT_LOOKUP_DAILY_LIMIT" in str(excinfo.value)


def test_non_base64_audit_ip_key_refused(monkeypatch):
    _set_required_env(monkeypatch, PLAK_AUDIT_IP_KEY="niet-base64!!!")
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()
    assert "PLAK_AUDIT_IP_KEY" in str(excinfo.value)


def test_wrong_length_audit_ip_key_refused(monkeypatch):
    _set_required_env(monkeypatch, PLAK_AUDIT_IP_KEY=base64.b64encode(b"te-kort").decode())
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()
    assert "PLAK_AUDIT_IP_KEY" in str(excinfo.value)


def test_audit_ip_key_equal_to_audit_pepper_refused(monkeypatch):
    shared = base64.b64encode(b"z" * 32).decode()
    _set_required_env(monkeypatch, PLAK_AUDIT_PEPPER=shared, PLAK_AUDIT_IP_KEY=shared)
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()
    assert "PLAK_AUDIT_IP_KEY" in str(excinfo.value)


def test_audit_ip_key_equal_to_session_secret_refused(monkeypatch):
    shared = base64.b64encode(b"z" * 32).decode()
    _set_required_env(monkeypatch, PLAK_SESSION_SECRET=shared, PLAK_AUDIT_IP_KEY=shared)
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()
    assert "PLAK_AUDIT_IP_KEY" in str(excinfo.value)


def test_audit_pepper_equal_to_session_secret_refused(monkeypatch):
    """One leaked value would then both forge sessions and undo the
    pseudonymisation of every actor in the audit log."""
    shared = "g" * 32
    _set_required_env(monkeypatch, PLAK_SESSION_SECRET=shared, PLAK_AUDIT_PEPPER=shared)
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()
    assert "PLAK_AUDIT_PEPPER moet verschillen van PLAK_SESSION_SECRET" in str(excinfo.value)
    assert shared not in _full_traceback(excinfo.value)


def test_audit_ip_key_previous_absent_by_default(monkeypatch):
    _set_required_env(monkeypatch)
    settings = load_settings()
    assert settings.audit_ip_key_previous is None
    assert settings.audit_ip_key_previous_bytes is None


def test_audit_ip_key_previous_decoded(monkeypatch):
    raw = b"p" * 32
    _set_required_env(monkeypatch, PLAK_AUDIT_IP_KEY_PREVIOUS=base64.b64encode(raw).decode())
    settings = load_settings()
    assert settings.audit_ip_key_previous_bytes == raw


def test_non_base64_audit_ip_key_previous_refused(monkeypatch):
    _set_required_env(monkeypatch, PLAK_AUDIT_IP_KEY_PREVIOUS="niet-base64!!!")
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()
    assert "PLAK_AUDIT_IP_KEY_PREVIOUS" in str(excinfo.value)


def test_audit_ip_key_previous_equal_to_audit_ip_key_refused(monkeypatch):
    shared = base64.b64encode(b"z" * 32).decode()
    _set_required_env(monkeypatch, PLAK_AUDIT_IP_KEY=shared, PLAK_AUDIT_IP_KEY_PREVIOUS=shared)
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()
    assert "PLAK_AUDIT_IP_KEY_PREVIOUS" in str(excinfo.value)


def test_audit_ip_key_previous_equal_to_audit_pepper_refused(monkeypatch):
    shared = base64.b64encode(b"q" * 32).decode()
    _set_required_env(monkeypatch, PLAK_AUDIT_PEPPER=shared, PLAK_AUDIT_IP_KEY_PREVIOUS=shared)
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()
    assert "PLAK_AUDIT_IP_KEY_PREVIOUS" in str(excinfo.value)


def _full_traceback(error: BaseException) -> str:
    return "".join(traceback.format_exception(error))


def test_traceback_of_a_field_error_shows_no_secret(monkeypatch):
    _set_required_env(monkeypatch, PLAK_SESSION_SECRET="SUPERGEHEIM-te-kort")
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__suppress_context__ is True
    text = _full_traceback(excinfo.value)
    assert "PLAK_SESSION_SECRET" in text
    assert "SUPERGEHEIM" not in text
    assert "ValidationError" not in text


def test_traceback_of_model_error_shows_no_settings_values(monkeypatch):
    _set_required_env(
        monkeypatch,
        PLAK_AUDIT_PEPPER="PEPPERGEHEIM" + "y" * 32,
        PLAK_OIDC_CLIENT_SECRET="super-geheim-waarde",
    )
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()
    text = _full_traceback(excinfo.value)
    assert "PLAK_OIDC_CLIENT_SECRET" in text
    assert "super-geheim-waarde" not in text
    assert "PEPPERGEHEIM" not in text
    assert "input_value" not in text


def test_message_of_type_error_shows_no_value(monkeypatch):
    _set_required_env(monkeypatch, PLAK_INGEST_MAX_BODY="GEHEIME-WAARDE")
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()
    text = _full_traceback(excinfo.value)
    assert "PLAK_INGEST_MAX_BODY" in text
    assert "GEHEIME-WAARDE" not in text


def test_production_without_the_proxy_setting_refused(monkeypatch):
    """Both answers are valid, so neither can be the default: unset has to be
    refused, or a deployment behind a proxy silently records the proxy's
    address for every visitor."""
    _set_required_env(monkeypatch, PLAK_ENVIRONMENT="productie")
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()
    assert "BEHIND_PROXY" in str(excinfo.value)


def test_production_without_a_proxy_is_accepted(monkeypatch):
    _set_required_env(
        monkeypatch,
        PLAK_ENVIRONMENT="productie",
        PLAK_BEHIND_PROXY="false",
        PLAK_OIDC_ISS_REQUIRED="true",
        PLAK_BASE_URL="https://beheer.example.nl",
    )
    assert load_settings().behind_proxy is False


def test_production_without_iss_required_refused(monkeypatch):
    _set_required_env(
        monkeypatch,
        PLAK_ENVIRONMENT="productie",
        PLAK_BEHIND_PROXY="true",
    )
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()
    assert "OIDC_ISS_REQUIRED" in str(excinfo.value)


def test_production_without_base_url_refused(monkeypatch):
    _set_required_env(
        monkeypatch,
        PLAK_ENVIRONMENT="productie",
        PLAK_BEHIND_PROXY="true",
        PLAK_OIDC_ISS_REQUIRED="true",
    )
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()
    assert "BASE_URL" in str(excinfo.value)


@pytest.mark.parametrize("value", ["beheer.plak.example", "/sites", "https://", "ftp://beheer.plak.example"])
def test_base_url_without_usable_origin_refused(monkeypatch, value):
    # origin_guard.normalise_origin needs a http(s) scheme and a hostname to
    # turn this into an origin; anything else must not pass validation and
    # fall through to the dev same-origin/localhost fallback unnoticed.
    _set_required_env(monkeypatch, PLAK_BASE_URL=value)
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()
    assert "BASE_URL" in str(excinfo.value)


@pytest.mark.parametrize(
    "value", ["https://beheer.plak.example", "http://beheer.plak.localhost:8080"]
)
def test_base_url_with_usable_origin_accepted(monkeypatch, value):
    _set_required_env(monkeypatch, PLAK_BASE_URL=value)
    assert load_settings().base_url == value


def test_without_content_base_url_refused_in_dev_too(monkeypatch):
    """The content host is required everywhere, not only in production.

    A single-host fallback would put the interface and uploaded content on one
    origin; since the SPA sits on the root of the admin host, it would put
    them on one path space as well.
    """
    _set_required_env(monkeypatch)
    monkeypatch.delenv("PLAK_CONTENT_BASE_URL")
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()
    assert "CONTENT_BASE_URL" in str(excinfo.value)


def test_unknown_environment_value_refused(monkeypatch):
    _set_required_env(monkeypatch, PLAK_ENVIRONMENT="staging")
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()
    assert "PLAK_ENVIRONMENT" in str(excinfo.value)


@pytest.mark.parametrize("value", ["", "plak.example", "/sites", "https://"])
def test_content_base_url_without_hostname_refused(monkeypatch, value):
    # The app derives the content host from this URL; without a hostname
    # every host comparison in the app would compare against the empty string.
    _set_required_env(monkeypatch, PLAK_CONTENT_BASE_URL=value)
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
    _set_required_env(monkeypatch, PLAK_CONTENT_BASE_URL=url)
    assert load_settings().content_host == host


def test_production_full_configured_loads(monkeypatch):
    _set_required_env(
        monkeypatch,
        PLAK_ENVIRONMENT="productie",
        PLAK_BEHIND_PROXY="true",
        PLAK_OIDC_ISS_REQUIRED="true",
        PLAK_BASE_URL="https://beheer.plak.example",
        PLAK_CONTENT_BASE_URL="https://plak.example",
    )
    settings = load_settings()
    assert settings.environment == "productie"
    assert settings.base_url == "https://beheer.plak.example"
    assert settings.content_base_url == "https://plak.example"


def _production_env(monkeypatch, **overrides):
    _set_required_env(
        monkeypatch,
        PLAK_ENVIRONMENT="productie",
        PLAK_BEHIND_PROXY="true",
        PLAK_OIDC_ISS_REQUIRED="true",
        PLAK_BASE_URL="https://beheer.plak.example",
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
    _production_env(monkeypatch, PLAK_OIDC_ISSUER=issuer)
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()
    assert "PLAK_OIDC_ISSUER" in str(excinfo.value)
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
    _production_env(monkeypatch, PLAK_OIDC_ISSUER=issuer)
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()
    assert "PLAK_OIDC_ISSUER" in str(excinfo.value)
    assert "hostnaam" in str(excinfo.value)


@pytest.mark.parametrize(
    "issuer",
    ["https://idp.example.org", "https://idp.example.org/realms/plak", "https://IDP.example.org"],
)
def test_production_accepts_a_https_issuer_on_a_real_host(monkeypatch, issuer):
    _production_env(monkeypatch, PLAK_OIDC_ISSUER=issuer)
    assert load_settings().oidc_issuer == issuer


def test_production_checks_the_issuer_derived_from_the_zad_discovery_url(monkeypatch):
    """On ZAD the issuer comes from OIDC_DISCOVERY_URL; the check has to see
    that value, not the empty PLAK_OIDC_ISSUER it started from."""
    _production_env(monkeypatch, PLAK_OIDC_ISSUER="")
    monkeypatch.setenv(
        "OIDC_DISCOVERY_URL",
        "http://keycloak.zad.local/realms/plak/.well-known/openid-configuration",
    )
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()
    assert "PLAK_OIDC_ISSUER" in str(excinfo.value)


@pytest.mark.parametrize(
    "issuer",
    ["http://oidc.plak.localhost:8080/default", "http://localhost:8081/default"],
)
def test_dev_keeps_accepting_the_http_mock_issuer(monkeypatch, issuer):
    _set_required_env(monkeypatch, PLAK_OIDC_ISSUER=issuer)
    assert load_settings().oidc_issuer == issuer


def test_spa_path_from_env(monkeypatch):
    _set_required_env(monkeypatch, PLAK_SPA_PATH="/app/spa")
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
            PLAK_OIDC_CLIENT_AUTH=method_,
            PLAK_OIDC_CLIENT_PRIVATE_JWK="",
            PLAK_OIDC_CLIENT_SECRET="geheim-123",
        )
        settings = load_settings()
        assert settings.oidc_client_auth == method_
        assert settings.oidc_client_secret == "geheim-123"

    def test_unknown_method_refused(self, monkeypatch):
        _set_required_env(monkeypatch, PLAK_OIDC_CLIENT_AUTH="client_secret_jwt")
        with pytest.raises(ConfigurationError) as excinfo:
            load_settings()
        assert "PLAK_OIDC_CLIENT_AUTH" in str(excinfo.value)

    def test_private_key_jwt_without_jwk_refused(self, monkeypatch):
        _set_required_env(monkeypatch, PLAK_OIDC_CLIENT_PRIVATE_JWK="")
        with pytest.raises(ConfigurationError) as excinfo:
            load_settings()
        assert "PLAK_OIDC_CLIENT_PRIVATE_JWK" in str(excinfo.value)

    def test_private_key_jwt_with_secret_refused_without_secret_value_in_message(self, monkeypatch):
        _set_required_env(monkeypatch, PLAK_OIDC_CLIENT_SECRET="super-geheim-waarde")
        with pytest.raises(ConfigurationError) as excinfo:
            load_settings()
        message = str(excinfo.value)
        assert "PLAK_OIDC_CLIENT_SECRET" in message
        assert "super-geheim-waarde" not in message

    @pytest.mark.parametrize("method_", ["client_secret_post", "client_secret_basic"])
    def test_client_secret_method_without_secret_refused(self, monkeypatch, method_):
        _set_required_env(
            monkeypatch, PLAK_OIDC_CLIENT_AUTH=method_, PLAK_OIDC_CLIENT_PRIVATE_JWK=""
        )
        with pytest.raises(ConfigurationError) as excinfo:
            load_settings()
        assert "PLAK_OIDC_CLIENT_SECRET" in str(excinfo.value)

    def test_client_secret_method_with_jwk_refused_without_key_in_message(self, monkeypatch):
        _set_required_env(
            monkeypatch,
            PLAK_OIDC_CLIENT_AUTH="client_secret_post",
            PLAK_OIDC_CLIENT_PRIVATE_JWK='{"kty": "RSA", "d": "prive-exponent"}',
            PLAK_OIDC_CLIENT_SECRET="geheim-123",
        )
        with pytest.raises(ConfigurationError) as excinfo:
            load_settings()
        message = str(excinfo.value)
        assert "PLAK_OIDC_CLIENT_PRIVATE_JWK" in message
        assert "prive-exponent" not in message
        assert "geheim-123" not in message


class TestRequiredAcr:
    def test_missing_is_empty(self, monkeypatch):
        _set_required_env(monkeypatch)
        monkeypatch.delenv("PLAK_OIDC_REQUIRED_ACR")
        assert load_settings().oidc_required_acr == ""

    def test_empty_in_dev_loads(self, monkeypatch):
        _set_required_env(monkeypatch, PLAK_OIDC_REQUIRED_ACR="")
        assert load_settings().oidc_required_acr == ""

    def test_empty_in_production_loads(self, monkeypatch):
        _set_required_env(
            monkeypatch,
            PLAK_OIDC_REQUIRED_ACR="",
            PLAK_ENVIRONMENT="productie",
            PLAK_BEHIND_PROXY="true",
            PLAK_OIDC_ISS_REQUIRED="true",
            PLAK_BASE_URL="https://beheer.plak.example",
            PLAK_CONTENT_BASE_URL="https://plak.example",
        )
        settings = load_settings()
        assert settings.environment == "productie"
        assert settings.oidc_required_acr == ""

    def test_other_production_requirements_stay_fail_fast_on_empty_acr(self, monkeypatch):
        _set_required_env(
            monkeypatch,
            PLAK_OIDC_REQUIRED_ACR="",
            PLAK_ENVIRONMENT="productie",
            PLAK_BEHIND_PROXY="true",
            PLAK_BASE_URL="https://beheer.plak.example",
            PLAK_CONTENT_BASE_URL="https://plak.example",
        )
        with pytest.raises(ConfigurationError) as excinfo:
            load_settings()
        assert "OIDC_ISS_REQUIRED" in str(excinfo.value)


class TestZadSourceMapping:
    """The ZAD Keycloak service injects OIDC_DISCOVERY_URL, OIDC_CLIENT_ID and
    OIDC_CLIENT_SECRET (without the PLAK_ prefix)."""

    DISCOVERY_URL = "https://keycloak.rijksapp.nl/realms/plak/.well-known/openid-configuration"

    def _set_zad_env(self, monkeypatch, **overrides):
        values = {
            "PLAK_DB_URL": "postgresql+asyncpg://gebruiker:wachtwoord@localhost/plak",
            "PLAK_CONTENT_ROOT": "/var/lib/plak/content",
            "PLAK_SESSION_SECRET": "x" * 32,
            "PLAK_AUDIT_PEPPER": "y" * 32,
            "PLAK_AUDIT_IP_KEY": base64.b64encode(b"z" * 32).decode(),
            "PLAK_OIDC_CLIENT_AUTH": "client_secret_post",
            "PLAK_CONTENT_BASE_URL": "https://plak.example",
            "PLAK_ENVIRONMENT": "dev",
            "OIDC_DISCOVERY_URL": self.DISCOVERY_URL,
            "OIDC_CLIENT_ID": "plak-prd",
            "OIDC_CLIENT_SECRET": "zad-geheim",
        }
        values.update(overrides)
        for key, value in values.items():
            monkeypatch.setenv(key, value)

    def test_zad_variables_fill_plak_oidc_settings(self, monkeypatch):
        self._set_zad_env(monkeypatch)
        settings = load_settings()
        assert settings.oidc_issuer == "https://keycloak.rijksapp.nl/realms/plak"
        assert settings.oidc_client_id == "plak-prd"
        assert settings.oidc_client_secret == "zad-geheim"
        assert settings.oidc_client_auth == "client_secret_post"

    def test_plak_variables_go_for_zad_variables(self, monkeypatch):
        self._set_zad_env(
            monkeypatch,
            PLAK_OIDC_ISSUER="https://idp.example.org",
            PLAK_OIDC_CLIENT_ID="plak-eigen",
            PLAK_OIDC_CLIENT_SECRET="eigen-geheim",
        )
        settings = load_settings()
        assert settings.oidc_issuer == "https://idp.example.org"
        assert settings.oidc_client_id == "plak-eigen"
        assert settings.oidc_client_secret == "eigen-geheim"

    def test_zad_secret_not_taken_over_under_private_key_jwt(self, monkeypatch):
        self._set_zad_env(
            monkeypatch,
            PLAK_OIDC_CLIENT_AUTH="private_key_jwt",
            PLAK_OIDC_CLIENT_PRIVATE_JWK='{"kty": "RSA"}',
        )
        settings = load_settings()
        assert settings.oidc_client_auth == "private_key_jwt"
        assert settings.oidc_client_secret == ""
        assert settings.oidc_issuer == "https://keycloak.rijksapp.nl/realms/plak"

    def test_without_zad_and_without_plak_issuer_refused(self, monkeypatch):
        self._set_zad_env(monkeypatch)
        monkeypatch.delenv("OIDC_DISCOVERY_URL")
        with pytest.raises(ConfigurationError) as excinfo:
            load_settings()
        assert "PLAK_OIDC_ISSUER" in str(excinfo.value)

    def test_without_zad_and_without_plak_client_id_refused(self, monkeypatch):
        self._set_zad_env(monkeypatch)
        monkeypatch.delenv("OIDC_CLIENT_ID")
        with pytest.raises(ConfigurationError) as excinfo:
            load_settings()
        assert "PLAK_OIDC_CLIENT_ID" in str(excinfo.value)

    def test_discovery_url_without_well_known_suffix_refused(self, monkeypatch):
        self._set_zad_env(monkeypatch, OIDC_DISCOVERY_URL="https://keycloak.rijksapp.nl/realms/plak")
        with pytest.raises(ConfigurationError) as excinfo:
            load_settings()
        message = str(excinfo.value)
        assert "OIDC_DISCOVERY_URL" in message
        assert "PLAK_OIDC_ISSUER" in message
