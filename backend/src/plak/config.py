"""Configuration through environment variables (12-factor), prefix PLAK_.

Fail-fast: missing or invalid required values produce a clear Dutch error
message naming the variable involved, instead of passing on the bare pydantic
ValidationError.

OIDC on ZAD: the ZAD Keycloak service injects `OIDC_DISCOVERY_URL`,
`OIDC_CLIENT_ID` and `OIDC_CLIENT_SECRET` (without the PLAK_ prefix) into the
container. Those serve as the source for `PLAK_OIDC_ISSUER`,
`PLAK_OIDC_CLIENT_ID` and `PLAK_OIDC_CLIENT_SECRET` as long as the PLAK_
variable itself is not set.
"""

from __future__ import annotations

import base64
import binascii
import ipaddress
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MIN_SECRET_BYTES = 32
AUDIT_IP_KEY_BYTES = 32

OIDC_CLIENT_AUTH_METHODS = ("private_key_jwt", "client_secret_post", "client_secret_basic")
OIDC_DISCOVERY_SUFFIX = "/.well-known/openid-configuration"


class ConfigurationError(RuntimeError):
    """Raised on missing or invalid configuration."""


def issuer_from_discovery_url(discovery_url: str) -> str:
    """OIDC Discovery 1.0 §4: discovery URL = issuer + /.well-known/openid-configuration."""
    if not discovery_url.endswith(OIDC_DISCOVERY_SUFFIX):
        raise ValueError(
            "OIDC_DISCOVERY_URL eindigt niet op /.well-known/openid-configuration; "
            "zet PLAK_OIDC_ISSUER expliciet"
        )
    return discovery_url[: -len(OIDC_DISCOVERY_SUFFIX)]


def normalise_https_base_url(value: str, field_name: str) -> str:
    """`https://host[:port]`, lowercase and without trailing slash; anything
    with a path, query, fragment or credentials is refused."""
    parts = urlsplit(value.strip())
    try:
        port = parts.port
    except ValueError as error:
        raise ValueError(f"{field_name}: ongeldige poort in {value.strip()!r}") from error
    if parts.scheme != "https" or not parts.hostname:
        raise ValueError(f"{field_name}: {value.strip()!r} is geen https-URL met hostnaam")
    if parts.path not in ("", "/") or parts.query or parts.fragment or parts.username or parts.password:
        raise ValueError(f"{field_name}: {value.strip()!r} mag alleen schema en host bevatten")
    host = parts.hostname
    return f"https://{host}" if port in (None, 443) else f"https://{host}:{port}"


def is_loopback_host(host: str) -> bool:
    """`localhost`, any name under `.localhost` (RFC 6761 section 6.3) and the
    loopback IP ranges (127.0.0.0/8, ::1)."""
    host = host.strip("[]").lower()
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _decode_audit_ip_key(field_name: str, value: str) -> str:
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError(f"{field_name} moet base64-gecodeerd zijn") from error
    if len(decoded) != AUDIT_IP_KEY_BYTES:
        raise ValueError(f"{field_name} moet base64 van {AUDIT_IP_KEY_BYTES} bytes zijn")
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PLAK_", extra="ignore")

    db_url: str
    content_root: Path
    oidc_issuer: str = ""
    oidc_client_id: str = ""
    oidc_client_auth: str = "private_key_jwt"
    oidc_client_private_jwk: str = ""
    oidc_client_secret: str = ""
    # Comma-separated list of accepted acr values; empty = no acr check (the
    # OidcClient logs that at startup, as a warning in production).
    oidc_required_acr: str = ""
    oidc_iss_required: bool = False
    # RP-initiated logout (OIDC RP-Initiated Logout 1.0). Off unless set: the
    # OP refuses a post_logout_redirect_uri that is not registered with it, and
    # the visitor then lands on an error page of the IdP.
    oidc_rp_logout: bool = False
    # Periodic re-validation of a browser session against the IdP
    # (auth/revalidation.py): at most one check per session per this many
    # seconds, 0 turns it off. It bounds how long someone blocked or removed
    # at the IdP keeps their Plak session.
    idp_recheck_seconds: int = Field(default=900, ge=0)
    # Injected by the ZAD Keycloak service; a validation_alias falls outside
    # env_prefix, so these are read without PLAK_.
    zad_oidc_discovery_url: str = Field(default="", validation_alias="OIDC_DISCOVERY_URL")
    zad_oidc_client_id: str = Field(default="", validation_alias="OIDC_CLIENT_ID")
    zad_oidc_client_secret: str = Field(default="", validation_alias="OIDC_CLIENT_SECRET")
    bootstrap_admin_sub: str = ""
    session_secret: str
    audit_pepper: str
    # Base64 of 32 random bytes (AES-256-GCM key, audit/ip_crypto.py): a
    # separate key from audit_pepper on purpose, so revealing a full IP and
    # pseudonymising an actor are two different secrets to compromise.
    audit_ip_key: str
    # Only set while rotating PLAK_AUDIT_IP_KEY: the key it replaced, kept
    # around long enough to still decrypt rows written under it. Remove it
    # once nothing left needs revealing from before the rotation.
    audit_ip_key_previous: str | None = None
    audit_lookup_daily_limit: int = Field(default=25, ge=1, le=1000)
    # Fails closed: a deployment that forgets to set PLAK_ENVIRONMENT refuses
    # to start (see _production_requirements below) instead of silently
    # skipping every production validator (trusted proxies, OIDC issuer
    # requirements, base url, https). dev/compose.yml sets PLAK_ENVIRONMENT=
    # dev explicitly for local development.
    environment: str = "productie"
    base_url: str | None = None
    # Two origins, always: the admin host and the content host are
    # separate origins, and the app derives the content host from this URL.
    # There is no single-host fallback: the SPA sits on the root of the admin
    # host, so one origin would put the interface and uploaded content
    # together. Dev compose, the ZAD deployment and production all set it. So:
    # required, everywhere.
    content_base_url: str
    # Whether a proxy stands in front of the pod, and thereby whether the last
    # X-Forwarded-For entry is the client address or the socket is (net.py).
    # None means nobody said; production refuses that, because either answer
    # can be the wrong one and neither fails loudly: without a proxy the header
    # is the client's to write, and behind one the socket is the proxy.
    behind_proxy: bool | None = None
    # Built admin SPA (Vite base /); by default relative to the
    # working directory of `just dev` (backend/). The app image sets this to
    # /app/spa.
    spa_path: Path = Path("../frontend/dist")
    # Per-bundle limits, sized for static sites on a small (1 GiB) content
    # volume. containers/nginx-dev/nginx.conf and the frontend's packing.ts
    # mirror them; the nginx body limit stays a little above ingest_max_body so
    # an oversized upload gets the app's 413 rather than nginx's.
    ingest_max_body: int = Field(default=100 * 1024 * 1024)
    ingest_max_file: int = Field(default=50 * 1024 * 1024)
    ingest_max_total: int = Field(default=200 * 1024 * 1024)
    ingest_max_files: int = Field(default=1000)
    ingest_max_depth: int = Field(default=10)
    # What every version of one site together may occupy on the content
    # volume; 0 turns the quota off. The per-bundle limits above bound one
    # deploy, this one bounds the history they leave behind: live versions are
    # kept forever, so without it a single site fills the volume by publishing
    # often enough.
    site_max_bytes: int = Field(default=500 * 1024 * 1024)
    # Free space the content volume must keep. A deploy is refused with a 503
    # before spooling when its declared size would cross it, and stopped (its
    # spool and work directory cleaned up) as soon as spooling or unpacking
    # would, rather than running the volume dry, which would take the serving
    # of every other site down with it. 0 turns the check off.
    storage_min_free_bytes: int = Field(default=100 * 1024 * 1024)

    # Forgejo instances whose Actions ID tokens are accepted for CI deploys,
    # comma-separated base URLs (https only). GitHub is always accepted.
    # Only these issuers are ever contacted: the token names its issuer, but
    # that claim only selects from this list.
    ci_forgejo_hosts: str = "https://code.overheid.nl"

    ratelimit_login_max: int = Field(default=10)
    ratelimit_login_window_s: int = Field(default=60)
    ratelimit_login_global_max: int = Field(default=1000)
    ratelimit_api_max: int = Field(default=60)
    ratelimit_api_window_s: int = Field(default=60)
    ratelimit_api_global_max: int = Field(default=5000)
    ratelimit_content_max: int = Field(default=600)
    ratelimit_content_window_s: int = Field(default=60)
    ratelimit_content_global_max: int = Field(default=20000)
    # Handing in the code of a secret link (serving/code_page.py): ten
    # attempts per quarter of an hour, per client IP here and per selector in
    # the route itself.
    ratelimit_code_max: int = Field(default=10)
    ratelimit_code_window_s: int = Field(default=900)
    ratelimit_code_global_max: int = Field(default=500)

    @field_validator("ci_forgejo_hosts")
    @classmethod
    def _ci_forgejo_hosts_valid(cls, value: str) -> str:
        hosts = [
            normalise_https_base_url(entry, "PLAK_CI_FORGEJO_HOSTS") for entry in value.split(",") if entry.strip()
        ]
        return ",".join(dict.fromkeys(hosts))

    @property
    def forgejo_hosts(self) -> tuple[str, ...]:
        """The configured Forgejo base URLs, normalised (lowercase, no trailing slash)."""
        return tuple(host for host in self.ci_forgejo_hosts.split(",") if host)

    @field_validator("session_secret", "audit_pepper")
    @classmethod
    def _min_32_bytes(cls, value: str, info) -> str:
        if len(value.encode("utf-8")) < MIN_SECRET_BYTES:
            field_name = f"PLAK_{info.field_name.upper()}"
            raise ValueError(f"{field_name} moet minstens {MIN_SECRET_BYTES} bytes lang zijn")
        return value

    @field_validator("audit_ip_key")
    @classmethod
    def _audit_ip_key_valid(cls, value: str) -> str:
        return _decode_audit_ip_key("PLAK_AUDIT_IP_KEY", value)

    @field_validator("audit_ip_key_previous")
    @classmethod
    def _audit_ip_key_previous_valid(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _decode_audit_ip_key("PLAK_AUDIT_IP_KEY_PREVIOUS", value)

    @model_validator(mode="after")
    def _audit_pepper_distinct(self) -> Settings:
        if self.audit_pepper == self.session_secret:
            raise ValueError("PLAK_AUDIT_PEPPER moet verschillen van PLAK_SESSION_SECRET")
        return self

    @model_validator(mode="after")
    def _audit_ip_key_distinct(self) -> Settings:
        other_secrets = (self.session_secret, self.audit_pepper)
        if self.audit_ip_key in other_secrets:
            raise ValueError(
                "PLAK_AUDIT_IP_KEY moet verschillen van PLAK_SESSION_SECRET en PLAK_AUDIT_PEPPER"
            )
        if self.audit_ip_key_previous is not None:
            if self.audit_ip_key_previous == self.audit_ip_key:
                raise ValueError("PLAK_AUDIT_IP_KEY_PREVIOUS moet verschillen van PLAK_AUDIT_IP_KEY")
            if self.audit_ip_key_previous in other_secrets:
                raise ValueError(
                    "PLAK_AUDIT_IP_KEY_PREVIOUS moet verschillen van PLAK_SESSION_SECRET en "
                    "PLAK_AUDIT_PEPPER"
                )
        return self

    @property
    def audit_ip_key_bytes(self) -> bytes:
        return base64.b64decode(self.audit_ip_key)

    @property
    def audit_ip_key_previous_bytes(self) -> bytes | None:
        return base64.b64decode(self.audit_ip_key_previous) if self.audit_ip_key_previous else None

    @field_validator("base_url")
    @classmethod
    def _base_url_valid(cls, value: str | None) -> str | None:
        """Same condition origin_guard.normalise_origin needs to turn this
        into an origin (http(s) scheme, a hostname); a value it cannot use
        must not pass validation silently and fall into the dev fallback."""
        if value is None:
            return None
        parts = urlsplit(value.strip())
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError(f"PLAK_BASE_URL: {value.strip()!r} is geen http(s)-URL met hostnaam")
        return value

    @field_validator("content_base_url")
    @classmethod
    def _content_base_url_with_host(cls, value: str) -> str:
        if not urlsplit(value).hostname:
            raise ValueError(
                "PLAK_CONTENT_BASE_URL moet een absolute URL met hostnaam zijn, "
                "bijvoorbeeld https://plak.example.org"
            )
        return value

    @property
    def content_host(self) -> str:
        """Hostname of the content origin, lowercase. The validator above
        guarantees the URL carries one."""
        return urlsplit(self.content_base_url).hostname or ""

    @field_validator("environment")
    @classmethod
    def _environment_valid(cls, value: str) -> str:
        if value not in ("dev", "productie"):
            raise ValueError("PLAK_ENVIRONMENT moet 'dev' of 'productie' zijn")
        return value

    @field_validator("oidc_client_auth")
    @classmethod
    def _client_auth_valid(cls, value: str) -> str:
        if value not in OIDC_CLIENT_AUTH_METHODS:
            raise ValueError(
                "PLAK_OIDC_CLIENT_AUTH moet een van "
                + ", ".join(OIDC_CLIENT_AUTH_METHODS)
                + " zijn"
            )
        return value

    @model_validator(mode="after")
    def _oidc_source_and_secrets(self) -> Settings:
        used_secret = self.oidc_client_auth != "private_key_jwt"

        if not self.oidc_issuer and self.zad_oidc_discovery_url:
            self.oidc_issuer = issuer_from_discovery_url(self.zad_oidc_discovery_url)
        if not self.oidc_client_id and self.zad_oidc_client_id:
            self.oidc_client_id = self.zad_oidc_client_id
        # The ZAD secret belongs to a client_secret method only; under
        # private_key_jwt it stays unused instead of raising an error.
        if used_secret and not self.oidc_client_secret and self.zad_oidc_client_secret:
            self.oidc_client_secret = self.zad_oidc_client_secret

        if not self.oidc_issuer:
            raise ValueError(
                "PLAK_OIDC_ISSUER is verplicht (op ZAD volstaat OIDC_DISCOVERY_URL "
                "van de Keycloak-dienst)"
            )
        if not self.oidc_client_id:
            raise ValueError(
                "PLAK_OIDC_CLIENT_ID is verplicht (op ZAD volstaat OIDC_CLIENT_ID "
                "van de Keycloak-dienst)"
            )

        method_ = self.oidc_client_auth
        if used_secret:
            if not self.oidc_client_secret:
                raise ValueError(
                    f"PLAK_OIDC_CLIENT_SECRET is verplicht bij PLAK_OIDC_CLIENT_AUTH={method_} "
                    "(op ZAD volstaat OIDC_CLIENT_SECRET van de Keycloak-dienst)"
                )
            if self.oidc_client_private_jwk:
                raise ValueError(
                    "PLAK_OIDC_CLIENT_PRIVATE_JWK hoort niet bij "
                    f"PLAK_OIDC_CLIENT_AUTH={method_}; verwijder de sleutel of kies "
                    "private_key_jwt"
                )
        else:
            if not self.oidc_client_private_jwk:
                raise ValueError(
                    "PLAK_OIDC_CLIENT_PRIVATE_JWK is verplicht bij "
                    "PLAK_OIDC_CLIENT_AUTH=private_key_jwt"
                )
            if self.oidc_client_secret:
                raise ValueError(
                    "PLAK_OIDC_CLIENT_SECRET hoort niet bij PLAK_OIDC_CLIENT_AUTH="
                    "private_key_jwt; verwijder het secret of kies client_secret_post "
                    "of client_secret_basic"
                )
        return self

    @model_validator(mode="after")
    def _production_requirements(self) -> Settings:
        if self.environment == "productie":
            if self.behind_proxy is None:
                raise ValueError(
                    "PLAK_BEHIND_PROXY is verplicht wanneer "
                    "PLAK_ENVIRONMENT=productie; 'false' betekent: geen proxy ervoor"
                )
            if not self.oidc_iss_required:
                raise ValueError(
                    "PLAK_OIDC_ISS_REQUIRED moet 'true' zijn wanneer "
                    "PLAK_ENVIRONMENT=productie"
                )
            if not self.base_url:
                raise ValueError(
                    "PLAK_BASE_URL is verplicht wanneer PLAK_ENVIRONMENT=productie"
                )
            # The issuer is already filled in by _oidc_source_and_secrets above,
            # whether it came from PLAK_OIDC_ISSUER or from OIDC_DISCOVERY_URL.
            issuer = urlsplit(self.oidc_issuer)
            if issuer.scheme != "https":
                raise ValueError(
                    "PLAK_OIDC_ISSUER moet een https-URL zijn wanneer "
                    "PLAK_ENVIRONMENT=productie; http hoort bij de dev-mock"
                )
            if not issuer.hostname or is_loopback_host(issuer.hostname):
                raise ValueError(
                    "PLAK_OIDC_ISSUER moet een echte hostnaam hebben wanneer "
                    "PLAK_ENVIRONMENT=productie; loopback en .localhost horen bij "
                    "de dev-mock"
                )
        return self


def _field_name_from_location(location: tuple) -> str:
    if location:
        return f"PLAK_{str(location[0]).upper()}"
    return "PLAK_<onbekend>"


def load_settings() -> Settings:
    """Loads the settings, or fails with a clear Dutch message."""
    try:
        return Settings()
    except ValidationError as error:
        messages = []
        for fields_error in error.errors():
            field_name = _field_name_from_location(fields_error.get("loc", ()))
            description_ = fields_error.get("msg", "ongeldige waarde")
            messages.append(f"{field_name}: {description_}")
        # Never render str(fout) or chain the ValidationError (from None):
        # pydantic shows input_value in there and that can be a secret
        # (sessie_geheim, audit_pepper, oidc_client_secret).
        details = "; ".join(messages) if messages else (
            f"{error.error_count()} validatiefout(en) zonder veldlocatie"
        )
        raise ConfigurationError(f"Configuratiefout in omgevingsvariabelen: {details}") from None
