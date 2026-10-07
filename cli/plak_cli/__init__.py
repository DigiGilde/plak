"""Plak CLI: publish static sites and clean up previews.

Install with `uv tool install`, or run it from a checkout without
installing with `uv run --project cli plak ...`; see README.md.

Usage:
    plak login [--host <host>] [--no-open] [--insecure-storage]
    plak logout [--host <host>]
    plak whoami [--host <host>]
    plak publish <dist-dir-or-file> --site <group/site> [--host <host>] \
        [--preview <ref>] [--base-path <dir>] [--output-file <path>]
    plak preview-remove <ref> --site <group/site> [--host <host>]
    plak group create <group> --name <name> [--access <base>] \
        [--secret-links | --no-secret-links] [--invitees | --no-invitees] \
        [--host <host>]
    plak site create <group/site> --title <title> [--access <base>] \
        [--secret-links | --no-secret-links] [--invitees | --no-invitees] \
        [--host <host>]
    plak site link <group/site> [<repository>] [--live-branch <branch>] \
        [--repository-id <id> --owner-id <id>] [--no-gh] [--host <host>]

Signing in happens with 'plak login': the session belongs to your user
account, for every directory. The tokens go into the system keyring (macOS
Keychain, Secret Service on Linux, Windows Credential Manager); without a
usable keyring, or with --insecure-storage, they go into hosts.json in the
config directory ($PLAK_CONFIG_DIR, else $XDG_CONFIG_HOME/plak, else
%APPDATA%\\plak on Windows and ~/.config/plak elsewhere), with mode 0600
outside Windows. That file also remembers the host you last logged in
to. In CI an OIDC token is used automatically (GitHub Actions with
'id-token: write', Forgejo Actions with 'enable-openid-connect: true'), or
supply a token yourself through the environment variable
PLAK_ACCESS_TOKEN.

Every command finds its host in this order: --host, PLAK_HOST in the
environment, the host you last logged in to, then DEFAULT_HOST, the
DigiGilde instance.

Exit codes:
    0 - success
    1 - error from the server (a network or API error, say); detail on
        stderr
    2 - wrong usage (a missing or invalid argument, an unknown path, no
        session)
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import platform
import re
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.parse
import webbrowser
from collections.abc import Callable
from pathlib import Path
from typing import IO, Any

import httpx
import keyring
import keyring.core
import keyring.errors

VERSION = importlib.metadata.version("plak")

# The major of the server API contract (the `API-Version` response header)
# this CLI understands. A server with a higher major is refused.
SUPPORTED_API_MAJOR = 1
API_VERSION_RE = re.compile(r"^(\d+)\.\d+\.\d+$")

DEFAULT_HOST = "https://beheer.plak.rijks.app"
HOST_HELP = (
    "Admin origin of Plak; defaults to PLAK_HOST, then the host you last "
    f"logged in to, then {DEFAULT_HOST}"
)

HOSTS_FILENAME = "hosts.json"
# Its own name, so a test can run the Windows path on any platform.
WINDOWS = sys.platform == "win32"
KEYRING_SERVICE_PREFIX = "plak:"
KEYRING_USERNAME = "session"
LEGACY_ENV_FILENAME = ".env.plak"

ALLOWED_ARCHIVE_EXTENSIONS = {".html", ".zip", ".tar.gz", ".tgz"}

# Slug for group, site and preview ref: identical to the server contract
# regex, so a ref or site part can never leak into the URL as an extra path
# segment.
SLUG_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
VERSION_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
# The URL characters of RFC 3986 without ' ( ) [ ]: the URL goes into
# GITHUB_OUTPUT, and from there into Markdown in the job summary and the
# pull request comment.
DEPLOY_URL_RE = re.compile(r"^https?://[A-Za-z0-9\-._~:/?#@!$&*+,;=%]+$")

# Suggested paths come from the server and go to the terminal: only ordinary
# relative paths are shown, so no answer can push line endings or control
# characters into the CI output.
SUGGESTION_PATH_RE = re.compile(r"^[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*$")
MAX_SUGGESTIONS = 5
MAX_SUGGESTION_LENGTH = 255

# The same holds for the error message itself, which is free text and these
# days carries archive paths: C0 and C1 control characters (line endings and
# ANSI escapes included) become a space, so a line of its own never appears in
# the CI log.
CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")
MAX_DETAIL_LENGTH = 2000

CONTENT_TYPES = {
    ".html": "text/html",
    ".zip": "application/zip",
    ".tar.gz": "application/gzip",
    ".tgz": "application/gzip",
}


class IncompatibleServer(Exception):
    """The server speaks a newer API major than this CLI supports."""


class UsageError(Exception):
    """An error in the call itself: a wrong argument, a missing path, an invalid file type."""


def _valid_slug(value: str, what: str) -> str:
    if not SLUG_RE.match(value):
        raise UsageError(
            f"{what} must be a valid slug (a-z, 0-9, hyphen), got: {value!r}"
        )
    return value


def _split_site(site: str, label: str = "--site") -> tuple[str, str]:
    parts = site.split("/")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise UsageError(
            f"{label} must have the form 'group/site', got: {site!r}"
        )
    return _valid_slug(parts[0], "group"), _valid_slug(parts[1], "site")


def _valid_base_path(value: str) -> str:
    """Directory inside the archive that becomes the root of the site. The
    same shape the server demands: relative, without '..' and without odd
    characters."""
    if "\x00" in value or "\\" in value or value.startswith("/"):
        raise UsageError(
            f"--base-path must be a relative path inside the archive, got: {value!r}"
        )
    parts = [part for part in value.split("/") if part not in ("", ".")]
    if not parts or ".." in parts:
        raise UsageError(
            f"--base-path must be a relative path inside the archive, got: {value!r}"
        )
    return "/".join(parts)


# Loopback per RFC 6761 §6.3: `localhost` and everything under `.localhost`
# always resolve to the loopback, so `beheer.plak.localhost` of the dev stack
# does too. Beyond that the whole 127.0.0.0/8 block and the IPv6 loopback
# address.
_LOOPBACK_RE = re.compile(
    r"^http://(?:(?:[a-z0-9-]+\.)*localhost|127(?:\.\d{1,3}){3}|\[::1\])(?::\d+)?(?:/|$)",
    re.IGNORECASE,
)


def _require_https(host: str) -> None:
    # Credentials in the host itself: httpx sends them, and every message that
    # names the host afterwards puts them in a terminal or a CI log. The check
    # comes first, so an https:// URL carrying them is refused too.
    if urllib.parse.urlsplit(host).username is not None:
        raise UsageError("Do not put a username or password in --host")
    if host.startswith("https://"):
        return
    if _LOOPBACK_RE.match(host):
        return
    raise UsageError(f"Use https:// for {host} (http only for loopback)")


def _resolve_host(args: argparse.Namespace) -> str:
    """Host from --host, PLAK_HOST in the environment, the host you last
    logged in to, or DEFAULT_HOST."""
    host = (
        getattr(args, "host", None)
        or os.environ.get("PLAK_HOST")
        or _stored_default_host()
        or DEFAULT_HOST
    )
    return host.rstrip("/")


def _same_origin(url: str, host: str) -> bool:
    """Whether url and host share the same scheme, hostname and port. Used to
    check a sign-in URL from the server before it is shown or opened: a
    server returning a URL to another domain (or file://) is ignored."""
    try:
        url_parts = urllib.parse.urlsplit(url)
        host_parts = urllib.parse.urlsplit(host)
    except ValueError:
        return False
    if url_parts.scheme not in ("http", "https") or url_parts.scheme != host_parts.scheme:
        return False
    if not url_parts.hostname or url_parts.hostname != host_parts.hostname:
        return False
    default_port = 443 if url_parts.scheme == "https" else 80
    return (url_parts.port or default_port) == (host_parts.port or default_port)


def _config_dir() -> Path:
    explicit = os.environ.get("PLAK_CONFIG_DIR")
    if explicit:
        return Path(explicit)
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "plak"
    if WINDOWS:
        appdata = os.environ.get("APPDATA")
        return (Path(appdata) if appdata else Path.home() / "AppData" / "Roaming") / "plak"
    return Path.home() / ".config" / "plak"


def _hosts_path() -> Path:
    return _config_dir() / HOSTS_FILENAME


def _read_hosts() -> dict:
    """hosts.json, or an empty config if there is none. A file that anyone
    other than this user could have written is ignored as a whole: its
    default host decides where a token goes, and its plain-text sessions
    could be someone else's.

    On Windows there are no mode bits or uid to check: the file is trusted on
    its location. %APPDATA% sits in the user profile, whose default ACL lets
    in only the user, SYSTEM and the Administrators; a directory named in
    PLAK_CONFIG_DIR or XDG_CONFIG_HOME is protected as well as its own ACL
    protects it."""
    path = _hosts_path()
    if WINDOWS:
        refusal = (
            f"Warning: ignoring {path}: it must be a regular file you can read. "
            "Log in again to rewrite it."
        )
        # No O_NOFOLLOW on Windows. Planting a link here takes write access
        # to the directory, which the profile ACL keeps to this user.
        flags = os.O_RDONLY
    else:
        refusal = (
            f"Warning: ignoring {path}: it must be a file of yours with mode 0600, in a "
            "directory only you can write to. Log in again to rewrite it."
        )
        # No symlink, and the checks below run on the very file that is read:
        # a path checked first and opened later can be swapped in between.
        flags = os.O_RDONLY | os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        return {}
    except OSError:
        print(refusal, file=sys.stderr)
        return {}
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or (
            not WINDOWS and not _private_to_this_user(info, path.parent)
        ):
            print(refusal, file=sys.stderr)
            return {}
        try:
            data = json.loads(b"".join(iter(lambda: os.read(fd, 65536), b"")))
        except (OSError, ValueError):
            print(f"Warning: ignoring {path}: it is not readable JSON.", file=sys.stderr)
            return {}
    finally:
        os.close(fd)
    return data if isinstance(data, dict) else {}


def _private_to_this_user(info: os.stat_result, directory: Path) -> bool:
    """POSIX only: the file is this user's with mode 0600, in a directory of
    this user's that nobody else can write to."""
    uid = os.getuid()
    directory_info = directory.stat()
    return (
        info.st_uid == uid
        and stat.S_IMODE(info.st_mode) == 0o600
        and directory_info.st_uid == uid
        and not directory_info.st_mode & 0o022
    )


def _write_hosts(config: dict) -> None:
    path = _hosts_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Can hold tokens: first a 0600 file next to it (mkstemp), then an atomic
    # replace. Writing and chmodding afterwards leaves the file readable to
    # others for a moment, and an existing 0644 file even with fresh tokens.
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{HOSTS_FILENAME}.")
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(config, handle, indent=2)
            handle.write("\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _host_entries(config: dict) -> dict:
    hosts = config.get("hosts")
    return hosts if isinstance(hosts, dict) else {}


def _host_entry(config: dict, host: str) -> dict:
    entry = _host_entries(config).get(host)
    return entry if isinstance(entry, dict) else {}


def _keyring_service(host: str) -> str:
    return KEYRING_SERVICE_PREFIX + host


def _save_session(
    host: str,
    access_token: str,
    refresh_token: str,
    expires_at: float,
    *,
    insecure: bool,
    make_default: bool,
) -> str | None:
    """Stores the session for host: the tokens in the system keyring, or in
    hosts.json when insecure is set or the keyring fails. Returns None when
    the keyring took them, else why they went into the file."""
    config = _read_hosts()
    previous = _host_entry(config, host)
    entry: dict[str, object] = {"access_expires_at": expires_at}
    fallback_reason: str | None = "--insecure-storage"
    if not insecure and not keyring.core.recommended(keyring.get_keyring()):
        # PYTHON_KEYRING_BACKEND or keyringrc.cfg can select the null backend,
        # which drops the secret without an error, or a plain-text one such as
        # keyrings.alt: neither is a system keyring.
        fallback_reason = "no system keyring found"
    elif not insecure:
        secret = json.dumps({"access_token": access_token, "refresh_token": refresh_token})
        try:
            keyring.set_password(_keyring_service(host), KEYRING_USERNAME, secret)
            fallback_reason = None
        except keyring.errors.NoKeyringError:
            # Its message recommends installing keyrings.alt, which only
            # stores the same plain text somewhere else.
            fallback_reason = "no system keyring found"
        except keyring.errors.KeyringError as error:
            fallback_reason = f"the system keyring refused: {error}"
    if fallback_reason is None:
        entry["storage"] = "keyring"
    else:
        entry |= {"storage": "file", "access_token": access_token, "refresh_token": refresh_token}
        if insecure:
            entry["insecure_storage"] = True
        # Otherwise the old keyring entry outlives the next logout, which
        # only looks where the entry says the tokens are.
        if previous.get("storage") == "keyring":
            _delete_keyring_entry(host)
    hosts = _host_entries(config)
    hosts[host] = entry
    config["hosts"] = hosts
    if make_default:
        config["default_host"] = host
    _write_hosts(config)
    return fallback_reason


def _session_tokens(host: str, entry: dict) -> tuple[str | None, str | None]:
    """The access and refresh token of a stored session, from wherever the
    entry says they live."""
    if entry.get("storage") == "keyring":
        try:
            secret = keyring.get_password(_keyring_service(host), KEYRING_USERNAME)
        except keyring.errors.KeyringError as error:
            raise UsageError(
                f"Could not read the session from the system keyring: {error}"
            ) from error
        try:
            data = json.loads(secret) if secret else {}
        except ValueError:
            data = {}
        if not isinstance(data, dict):
            data = {}
    else:
        data = entry
    access = data.get("access_token")
    refresh = data.get("refresh_token")
    return (
        access if isinstance(access, str) and access else None,
        refresh if isinstance(refresh, str) and refresh else None,
    )


def _delete_keyring_entry(host: str) -> keyring.errors.KeyringError | None:
    """Removes the keyring entry for host. Returns the error if that failed;
    an entry that is already gone is no failure."""
    try:
        keyring.delete_password(_keyring_service(host), KEYRING_USERNAME)
    except keyring.errors.PasswordDeleteError as error:
        # The macOS backend raises this for a refused delete too, not only
        # for a missing entry: only a read that finds nothing tells them apart.
        try:
            gone = keyring.get_password(_keyring_service(host), KEYRING_USERNAME) is None
        except keyring.errors.KeyringError:
            gone = False
        return None if gone else error
    except keyring.errors.KeyringError as error:
        return error
    return None


def _warn_plain_text(reason: str) -> None:
    print(
        f"Warning: {_clean(reason)}; the session is stored in plain text in {_hosts_path()}.",
        file=sys.stderr,
    )


def _forget_session(host: str) -> None:
    config = _read_hosts()
    entry = _host_entry(config, host)
    if entry.get("storage") == "keyring":
        error = _delete_keyring_entry(host)
        if error is not None:
            print(
                f"Warning: could not remove the session from the system keyring: {error}",
                file=sys.stderr,
            )
    hosts = _host_entries(config)
    if host not in hosts:
        return
    # The default host stays, so a later 'plak login' needs no --host.
    del hosts[host]
    config["hosts"] = hosts
    _write_hosts(config)


def _stored_default_host() -> str | None:
    host = _read_hosts().get("default_host")
    return host if isinstance(host, str) and host else None


def _note_legacy_env_file() -> None:
    if (Path.cwd() / LEGACY_ENV_FILENAME).exists():
        print(
            f"Note: plak no longer reads {LEGACY_ENV_FILENAME} in this directory; "
            "the session now belongs to your user account. Log in again with "
            f"'plak login' and delete {LEGACY_ENV_FILENAME}.",
            file=sys.stderr,
        )


def _check_api_version(response: httpx.Response) -> None:
    match = API_VERSION_RE.match(response.headers.get("API-Version", ""))
    if match and int(match.group(1)) > SUPPORTED_API_MAJOR:
        raise IncompatibleServer(
            f"this server speaks API {match.group(1)}.x, this plak CLI "
            f"({VERSION}) supports API {SUPPORTED_API_MAJOR}.x. Install a "
            "newer CLI: uv tool install --force "
            '"git+https://github.com/DigiGilde/plak@<tag>#subdirectory=cli"'
        )


_client: Any = None

# What a retry may follow, for calls that pass `retry_on`. A request that
# never left (connect errors) is safe to repeat whatever it does; any
# transport error, a read timeout included, only for a call that is
# idempotent.
RETRY_NOT_SENT = (httpx.ConnectError, httpx.ConnectTimeout)
RETRY_ANY_TRANSPORT = (httpx.TransportError,)
RETRY_STATUSES = frozenset({502, 503, 504})
RETRY_DELAY_S = 1.0


def _http(
    method: str,
    url: str,
    retry_on: tuple[type[Exception], ...] = (),
    retry_headers: Callable[[], dict[str, str]] | None = None,
    **kwargs: Any,
) -> httpx.Response:
    """The one way out to the server: sets the User-Agent and refuses a server
    with a newer API major before any caller reads the response.

    One client for the whole run, so a login poll or a refresh followed by a
    request reuses the connection instead of a new TLS handshake each time.
    A call that passes `retry_on` is tried once more after RETRY_DELAY_S
    when it fails with one of those errors or the server answers 502, 503
    or 504; nothing else is ever retried. `retry_headers` replaces headers
    for that second try."""
    global _client
    if _client is None:
        _client = httpx.Client()
    client = _client
    headers = {**kwargs.pop("headers", {}), "User-Agent": f"plak-cli/{VERSION}"}

    def send(extra: dict[str, str] | None = None) -> httpx.Response:
        return client.request(method, url, headers={**headers, **(extra or {})}, **kwargs)

    def send_again() -> httpx.Response:
        time.sleep(RETRY_DELAY_S)
        return send(retry_headers() if retry_headers else None)

    try:
        response = send()
    except retry_on:
        response = send_again()
    else:
        if retry_on and response.status_code in RETRY_STATUSES:
            response = send_again()
    _check_api_version(response)
    return response


def _request(host: str, method: str, url: str, **kwargs: Any) -> httpx.Response | None:
    """`_http`, except that a connection failure is printed and answered with
    None instead of raised."""
    try:
        return _http(method, url, **kwargs)
    except httpx.HTTPError as error:
        print(f"Error: could not connect to {host}: {error}", file=sys.stderr)
        return None


def _setup(args: argparse.Namespace) -> str:
    """The host a command talks to, checked for https; a UsageError ends in
    `main` as an error line and exit code 2."""
    host = _resolve_host(args)
    _require_https(host)
    return host


def _problem_data(response: httpx.Response) -> dict:
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _refresh_token(host: str, refresh_token: str, entry: dict) -> str:
    try:
        response = _http(
            "POST",
            f"{host}/-/api/v1/cli/tokens",
            json={"grantType": "refresh_token", "refreshToken": refresh_token},
            timeout=30.0,
            retry_on=RETRY_NOT_SENT,
        )
    except httpx.HTTPError as error:
        raise UsageError(f"Could not refresh the session: {error}") from error
    if response.status_code != 200:
        raise UsageError("Session expired or revoked: log in again with 'plak login'")
    payload = _problem_data(response)
    access_token = payload.get("accessToken")
    if not isinstance(access_token, str) or not access_token:
        raise UsageError("Unexpected answer while refreshing the session")
    refresh_out = payload.get("refreshToken") or refresh_token
    expires_at = time.time() + float(payload.get("expiresIn") or 0)
    # Only a choice for --insecure-storage keeps the file; a session that fell
    # back to it tries the keyring again, so a locked keychain once does not
    # leave it in plain text for good.
    fallback_reason = _save_session(
        host,
        access_token,
        refresh_out,
        expires_at,
        insecure=entry.get("insecure_storage") is True,
        make_default=False,
    )
    # The server has rotated the refresh token, so the new pair has to be kept
    # somewhere; a session that just left the keyring must not do so silently.
    if fallback_reason is not None and entry.get("storage") == "keyring":
        _warn_plain_text(fallback_reason)
    return access_token


def _stored_token(host: str) -> str | None:
    """Stored session for this host, refreshed if need be. None if there is none."""
    entry = _host_entry(_read_hosts(), host)
    if not entry:
        return None
    token, refresh = _session_tokens(host, entry)
    if not token:
        return None
    expires_at = entry.get("access_expires_at")
    # 60 seconds of slack: a token that expires while the request is on its
    # way is as useless as one that has already expired. An expiry that is
    # not a number (bool is an int in Python) counts as none.
    if (
        isinstance(expires_at, (int, float))
        and not isinstance(expires_at, bool)
        and expires_at - 60 <= time.time()
    ):
        if not refresh:
            raise UsageError("Session expired: log in again with 'plak login'")
        token = _refresh_token(host, refresh, entry)
    return token


def _fetch_oidc_token(host: str) -> str | None:
    """CI mode: exchange the runner OIDC token for an ID token with this host
    as audience. None if the environment offers no OIDC request (so not CI, or
    'id-token: write'/'enable-openid-connect' is missing)."""
    request_url = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL")
    request_token = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN")
    if not request_url or not request_token:
        return None
    separator = "&" if "?" in request_url else "?"
    url = f"{request_url}{separator}audience={urllib.parse.quote(host, safe='')}"
    try:
        response = _http(
            "GET",
            url,
            headers={"Authorization": f"bearer {request_token}"},
            timeout=30.0,
            retry_on=RETRY_ANY_TRANSPORT,
        )
    except httpx.HTTPError as error:
        raise UsageError(f"Could not fetch an OIDC token: {error}") from error
    if response.status_code != 200:
        raise UsageError(
            f"Could not fetch an OIDC token (HTTP {response.status_code}). Set "
            "'permissions: id-token: write' in the workflow (GitHub Actions) or "
            "'enable-openid-connect: true' on the runner (Forgejo Actions)."
        )
    data = _problem_data(response)
    token = data.get("value")
    if not isinstance(token, str) or not token:
        raise UsageError("Unexpected answer while fetching the OIDC token")
    _mask_in_ci_log(token)
    return token


def _mask_in_ci_log(secret: str) -> None:
    """Asks the runner to mask `secret` in the rest of its log.

    GitHub Actions and Forgejo Actions read these workflow commands from
    stdout, so there is no way to send this anywhere else. That makes the
    command only worth writing when stdout really is the runner's log: a
    regular file means someone redirected it (`> log.txt`), the runner never
    sees the command, and printing it would write the token into that file for
    nothing. A pipe is indistinguishable from the runner's own pipe, so
    `$(...)` and `| tee` still capture it; see docs/publishing.md.
    """
    try:
        redirected = stat.S_ISREG(os.fstat(sys.stdout.fileno()).st_mode)
    except (OSError, ValueError, AttributeError):
        # No real file descriptor to judge by. Keep the mask: a runner that
        # does read this is the case worth protecting, and nothing else in
        # this CLI puts the token on stdout.
        redirected = False
    if redirected:
        return
    print(f"::add-mask::{secret}")


def _get_bearer_token(host: str) -> str:
    """The token for the deploy endpoints and /cli/whoami, in order: an
    explicit PLAK_ACCESS_TOKEN, a CI OIDC token, then the stored session."""
    env_token = os.environ.get("PLAK_ACCESS_TOKEN")
    if env_token:
        return env_token
    oidc_token = _fetch_oidc_token(host)
    if oidc_token:
        return oidc_token
    stored = _stored_token(host)
    if stored:
        return stored
    _note_legacy_env_file()
    raise UsageError(
        "No token: log in with 'plak login --host <host>', or set "
        "PLAK_ACCESS_TOKEN (for instance an OIDC token in CI, see README)"
    )


def _get_member_token(host: str) -> str:
    """The token for creating a group or a site: an explicit PLAK_ACCESS_TOKEN,
    then the stored session. No CI OIDC token: the server takes only a
    member's CLI token there."""
    token = os.environ.get("PLAK_ACCESS_TOKEN") or _stored_token(host)
    if token:
        return token
    _note_legacy_env_file()
    raise UsageError(
        "No session: log in with 'plak login --host <host>', or set PLAK_ACCESS_TOKEN"
    )


def _file_extension(path: Path) -> str:
    name = path.name.lower()
    for ext in (".tar.gz", ".tgz", ".zip", ".html"):
        if name.endswith(ext):
            return ext
    return path.suffix.lower()


def _pack_folder(dist_folder: Path) -> IO[bytes]:
    files = []
    for path in sorted(dist_folder.rglob("*")):
        relative = path.relative_to(dist_folder)
        if ".git" in relative.parts:
            continue
        if path.is_symlink():
            raise UsageError(f"Symlink not allowed in the dist folder: {relative}")
        if path.is_file():
            files.append((path, relative))
    if not files:
        raise UsageError(f"Folder is empty, nothing to publish: {dist_folder}")
    # A file on disk, not a buffer in memory: httpx reads it in chunks while
    # it uploads, so a large dist folder is never held whole.
    archive = tempfile.TemporaryFile()  # noqa: SIM115 - the caller closes it
    with tarfile.open(fileobj=archive, mode="w:gz") as tar:
        for file_path, relative_path in files:
            tar.add(file_path, arcname=str(relative_path))
    archive.seek(0)
    return archive


def _determine_upload(dist_path: Path) -> tuple[IO[bytes], str, str]:
    """Returns (open file, filename, content type) for the upload; the caller
    closes the file.

    A directory is always packed into dist.tar.gz with paths relative to the
    directory itself; a single file with an allowed extension goes along
    unchanged.
    """
    if dist_path.is_dir():
        content = _pack_folder(dist_path)
        return content, "dist.tar.gz", CONTENT_TYPES[".tar.gz"]

    if not dist_path.is_file():
        raise UsageError(f"Path does not exist: {dist_path}")

    extension = _file_extension(dist_path)
    if extension not in ALLOWED_ARCHIVE_EXTENSIONS:
        raise UsageError(
            "Unknown file type, expected a folder or a "
            f".html/.zip/.tar.gz/.tgz file: {dist_path}"
        )
    content = dist_path.open("rb")
    content_type = CONTENT_TYPES.get(extension, "application/octet-stream")
    return content, dist_path.name, content_type


def _suggestions(data: object) -> list[str]:
    """The index.html paths the server suggests, stripped of everything that
    is not an ordinary relative path."""
    raw = data.get("indexCandidates") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        return []
    return [
        path
        for path in raw[:MAX_SUGGESTIONS]
        if isinstance(path, str)
        and len(path) <= MAX_SUGGESTION_LENGTH
        and SUGGESTION_PATH_RE.match(path)
    ]


def _clean(text: str) -> str:
    """One line without control characters, bounded in length: everything
    coming from the server passes through here before it reaches the
    output."""
    return CONTROL_CHAR_RE.sub(" ", text[:MAX_DETAIL_LENGTH]).strip()


def _print_problem_detail(response: httpx.Response) -> None:
    detail = None
    try:
        data = response.json()
    except ValueError:
        data = None
    if isinstance(data, dict):
        detail = data.get("detail") or data.get("title")
    if not detail:
        detail = response.text or f"HTTP {response.status_code}"
    print(f"Error: {_clean(str(detail))}", file=sys.stderr)

    suggestions = _suggestions(data)
    if not suggestions:
        return
    print("Found index.html in the bundle:", file=sys.stderr)
    for path in suggestions:
        print(f"  {path}", file=sys.stderr)
    shortest = suggestions[0]
    # Both forms included: whoever reads this in an action log has no flag to
    # set, but an input in their workflow YAML.
    if "/" in shortest:
        folder_path, _, _ = shortest.rpartition("/")
        print(
            f"Publish again with: --base-path {folder_path} "
            f"(in the action: base-path: {folder_path})",
            file=sys.stderr,
        )
    else:
        print(
            "Publish again without --base-path (in the action: without the "
            "base-path input): the index.html is in the root.",
            file=sys.stderr,
        )


def cmd_publish(args: argparse.Namespace) -> int:
    dist_path = Path(args.dist_path)
    host = _setup(args)
    token = _get_bearer_token(host)
    group, site = _split_site(args.site)
    if args.preview:
        _valid_slug(args.preview, "preview-ref")
    base_path = (
        _valid_base_path(args.base_path) if args.base_path is not None else None
    )
    content, file_name, content_type = _determine_upload(dist_path)

    url = f"{host}/-/api/v1/sites/{group}/{site}/deploys"

    form_fields: dict[str, str] = {}
    if args.preview:
        form_fields["preview"] = args.preview
    if base_path is not None:
        form_fields["basePath"] = base_path

    with content:
        response = _request(
            host,
            "POST",
            url,
            headers={"Authorization": f"Bearer {token}"},
            files={"file": (file_name, content, content_type)},
            data=form_fields,
            timeout=120.0,
        )
    if response is None:
        return 1

    if response.status_code == 201:
        try:
            version_id = response.json()["versionId"]
        except (ValueError, KeyError):
            print("Error: unexpected answer without versionId", file=sys.stderr)
            return 1
        # Validate the format: a malicious server must not be able to inject
        # newlines or extra keys into the CI through GITHUB_OUTPUT.
        if not isinstance(version_id, str) or not VERSION_ID_RE.match(version_id):
            print("Error: unexpected versionId format", file=sys.stderr)
            return 1
        # A server from before the url field answers without it.
        deploy_url = response.json().get("url")
        if deploy_url is not None and (
            not isinstance(deploy_url, str) or not DEPLOY_URL_RE.match(deploy_url)
        ):
            print("Error: unexpected url format", file=sys.stderr)
            return 1
        # Only a hint for the report, so an access this CLI does not know (a
        # newer base, say) is left out rather than failing a deploy that went
        # through. A server from before the access field answers without it.
        raw_access = response.json().get("access")
        deploy_access = _parse_access(raw_access)
        if raw_access is not None and deploy_access is None:
            print("Warning: leaving out an access this CLI does not know", file=sys.stderr)
        if args.output_file:
            # Straight to the output file ($GITHUB_OUTPUT, say): in CI stdout
            # also carries the '::add-mask::' command, and a shell capturing
            # stdout for the version id would take that command along.
            try:
                with open(args.output_file, "a", encoding="utf-8") as handle:
                    handle.write(f"version-id={version_id}\n")
                    if deploy_url is not None:
                        handle.write(f"url={deploy_url}\n")
                    if deploy_access is not None:
                        handle.write(f"access={format_access(deploy_access)}\n")
            except OSError as error:
                print(
                    f"Error: publish succeeded (version {version_id}) but could not "
                    f"write to {args.output_file}: {error}",
                    file=sys.stderr,
                )
                return 1
        else:
            print(version_id)
        if deploy_url is None:
            print(f"Published version {version_id}", file=sys.stderr)
        else:
            print(f"Published: {deploy_url} (version {version_id})", file=sys.stderr)
        return 0

    _print_problem_detail(response)
    return 1


def cmd_preview_remove(args: argparse.Namespace) -> int:
    host = _setup(args)
    token = _get_bearer_token(host)
    group, site = _split_site(args.site)
    _valid_slug(args.ref, "preview-ref")

    url = f"{host}/-/api/v1/sites/{group}/{site}/previews/{args.ref}"

    # The server accepts a CI ID token with a `jti` once, and the first try
    # may have reached it, so the retry asks for the token again: in CI that
    # is a fresh one.
    response = _request(
        host,
        "DELETE",
        url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=60.0,
        retry_on=RETRY_ANY_TRANSPORT,
        retry_headers=lambda: {"Authorization": f"Bearer {_get_bearer_token(host)}"},
    )
    if response is None:
        return 1

    if response.status_code in (200, 204):
        print(f"Preview '{args.ref}' removed (or was already gone).", file=sys.stderr)
        return 0

    _print_problem_detail(response)
    return 1


ACCESS_BASES = ("public", "sso", "site_team", "nobody")

# Who a base lets in, for the terminal. Mirrors the server's ACCESS_BASE_HINT.
ACCESS_BASE_WHO = {
    "public": "anyone",
    "sso": "anyone who signs in with SSO Rijk",
    "site_team": "members of the site and its group",
    "nobody": "nobody by default",
}


def _access_body(args: argparse.Namespace) -> dict[str, object] | None:
    """Only the flags that were given: the server fills in the rest from the
    default (for a site: the group's default access)."""
    body: dict[str, object] = {}
    if args.access is not None:
        body["base"] = args.access
    if args.secret_links is not None:
        body["keys"] = args.secret_links
    if args.invitees is not None:
        body["invitees"] = args.invitees
    return body or None


def _parse_access(data: object) -> tuple[str, bool, bool] | None:
    if not isinstance(data, dict):
        return None
    base, keys, invitees = data.get("base"), data.get("keys"), data.get("invitees")
    if base not in ACCESS_BASE_WHO or not isinstance(keys, bool) or not isinstance(invitees, bool):
        return None
    return base, keys, invitees


def _on_off(value: bool) -> str:
    return "on" if value else "off"


def format_access(access: tuple[str, bool, bool]) -> str:
    """The base, then the extras that are on: `site_team,invitees`."""
    base, keys, invitees = access
    return ",".join([base] + ["keys"] * keys + ["invitees"] * invitees)


def _print_access(access: tuple[str, bool, bool], label: str, change_url: str) -> None:
    base, keys, invitees = access
    print(
        f"{label}: {base} ({ACCESS_BASE_WHO[base]}), "
        f"secret links {_on_off(keys)}, invitees {_on_off(invitees)}."
    )
    if base == "public":
        return
    who = [] if base == "nobody" else [ACCESS_BASE_WHO[base]]
    if keys:
        who.append("anyone with a secret link")
    if invitees:
        who.append("invitees, once signed in")
    print(f"Who can see it: {', '.join(who) if who else 'nobody yet'}.")
    print(f"Change it at: {change_url}")


def _create(host: str, token: str, path: str, body: dict[str, object]) -> dict | None:
    """POSTs to a creation route; the answer on 201, None after an error has
    been printed."""
    response = _request(
        host,
        "POST",
        f"{host}{path}",
        headers={"Authorization": f"Bearer {token}"},
        json=body,
        timeout=30.0,
    )
    if response is None:
        return None
    if response.status_code != 201:
        _print_problem_detail(response)
        return None
    return _problem_data(response)


def cmd_group_create(args: argparse.Namespace) -> int:
    host = _setup(args)
    token = _get_member_token(host)
    group = _valid_slug(args.group, "group")

    body: dict[str, object] = {"slug": group, "name": args.name}
    access = _access_body(args)
    if access is not None:
        body["defaultAccess"] = access
    data = _create(host, token, "/-/api/v1/groups", body)
    if data is None:
        return 1
    default_access = _parse_access(data.get("defaultAccess"))
    if default_access is None:
        print("Error: unexpected answer without the group's access", file=sys.stderr)
        return 1
    name = _clean(data["name"]) if isinstance(data.get("name"), str) else group
    print(f"Created group '{group}' ({name}). You are its admin.")
    _print_access(default_access, "Default access for new sites", f"{host}/{group}/-/settings")
    return 0


def cmd_site_create(args: argparse.Namespace) -> int:
    host = _setup(args)
    token = _get_member_token(host)
    group, site = _split_site(args.site, "The site")

    body: dict[str, object] = {"slug": site, "title": args.title}
    access = _access_body(args)
    if access is not None:
        body["access"] = access
    data = _create(host, token, f"/-/api/v1/groups/{group}/sites", body)
    if data is None:
        return 1
    site_access = _parse_access(data.get("access"))
    if site_access is None:
        print("Error: unexpected answer without the site's access", file=sys.stderr)
        return 1
    title = _clean(data["title"]) if isinstance(data.get("title"), str) else site
    print(f"Created site '{group}/{site}' ({title}). You are its admin.")
    _print_access(site_access, "Access", f"{host}/{group}/{site}/access")
    print(f"Publish to it with: plak publish <dist> --host {host} --site {group}/{site}")
    return 0


# GitHub and Forgejo owner and repository names, as the server accepts them.
REPOSITORY_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
GITHUB_HOSTNAME = "github.com"
HOSTNAME_RE = re.compile(r"^[a-z0-9]([a-z0-9.-]*[a-z0-9])?(:[0-9]{1,5})?$")
MAX_PROVIDER_ID = 2**63 - 1
SUBPROCESS_TIMEOUT_S = 30


def _repository_name(value: str) -> str | None:
    return value if REPOSITORY_NAME_RE.match(value) and value not in (".", "..") else None


def _parse_repository(reference: str) -> tuple[str, str, str]:
    """`owner/repo`, an https or ssh URL, or a git@ address into (hostname,
    owner, repo); a bare `owner/repo` is on github.com."""
    value = reference.strip()
    hostname = GITHUB_HOSTNAME
    scp = re.match(r"^[^@/\s]+@([^:/\s]+):(.+)$", value)
    if scp:
        hostname, path = scp.group(1), scp.group(2)
    elif "://" in value:
        parsed = urllib.parse.urlsplit(value)
        try:
            port = parsed.port
        except ValueError:
            port = -1
        if parsed.scheme not in ("https", "ssh", "git") or not parsed.hostname or port == -1:
            raise UsageError(f"Not a repository URL: {reference!r}")
        hostname, path = parsed.hostname, parsed.path
        # An ssh port says nothing about where the web host listens.
        if parsed.scheme == "https" and port is not None:
            hostname = f"{hostname}:{port}"
    else:
        path = value
    if not HOSTNAME_RE.match(hostname.lower()):
        raise UsageError(f"Not a valid host in {reference!r}")
    parts = [part for part in path.split("/") if part]
    if "://" not in value and not scp and len(parts) != 2:
        raise UsageError(f"The repository must be 'owner/repo' or a URL, got: {reference!r}")
    if len(parts) < 2:
        raise UsageError(f"The URL names no owner and repository: {reference!r}")
    owner, repo = _repository_name(parts[0]), _repository_name(parts[1].removesuffix(".git"))
    if owner is None or repo is None:
        raise UsageError(f"Not a valid owner or repository name: {reference!r}")
    return hostname.lower(), owner, repo


def _git_origin() -> str:
    """The URL of the remote 'origin' of the git checkout we are in."""
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        result = None
    if result is None or result.returncode != 0 or not result.stdout.strip():
        raise UsageError(
            "No repository given and no git remote 'origin' here: pass 'owner/repo' or the repository URL"
        )
    return result.stdout.strip()


class NoIdsFromGh(Exception):
    """gh gave no IDs; the message says why and what to do about it."""


IDS_BY_HAND = "pass --repository-id and --owner-id"


def _github_repository(owner: str, repo: str) -> tuple[str, str, int, int]:
    """(owner, repo, repository id, owner id) as GitHub has them, asked with
    your own gh login, so a private repository resolves too."""
    try:
        result = subprocess.run(
            ["gh", "api", "--hostname", GITHUB_HOSTNAME, f"repos/{owner}/{repo}"],
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_S,
            check=False,
        )
    except FileNotFoundError:
        raise NoIdsFromGh(f"gh is not installed: install it and run 'gh auth login', or {IDS_BY_HAND}.") from None
    except (OSError, subprocess.TimeoutExpired) as error:
        raise NoIdsFromGh(f"gh gave no IDs ({_clean(str(error))}): {IDS_BY_HAND}.") from None
    # gh exits with 4 when it has no login at all; a token it has but GitHub
    # no longer accepts comes back as a 401.
    if result.returncode == 4 or "(HTTP 401)" in result.stderr:
        raise NoIdsFromGh("gh is not logged in: run 'gh auth login' (or set GH_TOKEN) and run this again.")
    if "(HTTP 404)" in result.stderr:
        raise NoIdsFromGh(f"gh cannot see {owner}/{repo} with your login: check the name, or ask for access.")
    if result.returncode != 0:
        lines = [line for line in result.stderr.splitlines() if line.strip()]
        why = _clean(lines[-1]) if lines else f"exit code {result.returncode}"
        raise NoIdsFromGh(f"gh gave no IDs ({why}): {IDS_BY_HAND}.")
    unusable = NoIdsFromGh(f"gh gave no usable IDs for {owner}/{repo}: {IDS_BY_HAND}.")
    try:
        data = json.loads(result.stdout)
    except ValueError:
        raise unusable from None
    owner_data = data.get("owner") if isinstance(data, dict) else None
    if not isinstance(owner_data, dict):
        raise unusable
    ids = (data.get("id"), owner_data.get("id"))
    names = (owner_data.get("login"), data.get("name"))
    if not all(type(value) is int and 0 < value <= MAX_PROVIDER_ID for value in ids):
        raise unusable
    if not all(isinstance(value, str) and _repository_name(value) for value in names):
        raise unusable
    return names[0], names[1], ids[0], ids[1]


def cmd_site_link(args: argparse.Namespace) -> int:
    host = _setup(args)
    group, site = _split_site(args.site, "The site")
    if (args.repository_id is None) != (args.owner_id is None):
        raise UsageError("Pass --repository-id and --owner-id together, or neither")
    for value in (args.repository_id, args.owner_id):
        if value is not None and not 0 < value <= MAX_PROVIDER_ID:
            raise UsageError("--repository-id and --owner-id must be positive whole numbers")
    hostname, owner, repo = _parse_repository(args.repository or _git_origin())
    token = _get_member_token(host)

    body: dict[str, object] = {"owner": owner, "repo": repo, "liveBranch": args.live_branch}
    if hostname == GITHUB_HOSTNAME:
        body["provider"] = "github"
    else:
        body["provider"] = "forgejo"
        body["host"] = f"https://{hostname}"
    ids_from = None
    # Why no IDs went along: printed when Plak's own anonymous lookup fails.
    no_ids = None
    if args.repository_id is not None:
        body["repositoryId"], body["ownerId"] = args.repository_id, args.owner_id
        ids_from = "as given"
    elif hostname != GITHUB_HOSTNAME:
        no_ids = f"To link it without Plak's lookup, {IDS_BY_HAND}."
    elif not args.gh:
        no_ids = f"gh was not asked (--no-gh): run this again without --no-gh, or {IDS_BY_HAND}."
    else:
        try:
            body["owner"], body["repo"], body["repositoryId"], body["ownerId"] = _github_repository(owner, repo)
            ids_from = "from gh"
        except NoIdsFromGh as error:
            no_ids = str(error)

    response = _request(
        host,
        "PUT",
        f"{host}/-/api/v1/sites/{group}/{site}/repository",
        headers={"Authorization": f"Bearer {token}"},
        json=body,
        timeout=30.0,
    )
    if response is None:
        return 1
    if response.status_code != 200:
        code = _problem_data(response).get("code")
        if code == "REPOSITORY_NOT_FOUND":
            # The server's detail also tells the admin form what to fill in;
            # here the line below says that.
            print(f"Error: Repository {owner}/{repo} not found on {hostname}, or not public.", file=sys.stderr)
        else:
            _print_problem_detail(response)
        if code in ("REPOSITORY_NOT_FOUND", "CI_PROVIDER_RATE_LIMITED") and no_ids is not None:
            print(no_ids, file=sys.stderr)
        return 1

    data = _problem_data(response)
    linked = f"{hostname}/{body['owner']}/{body['repo']}"
    if isinstance(data.get("owner"), str) and isinstance(data.get("repo"), str):
        linked = f"{hostname}/{_clean(data['owner'])}/{_clean(data['repo'])}"
    print(f"Linked {linked} to {group}/{site}.")
    if args.live_branch is None:
        print("Live: from any branch, on a push, a manual run or a schedule. Previews: from any branch.")
    else:
        print(f"Live: only from '{args.live_branch}', on a push, a manual run or a schedule. "
              "Previews: from any branch.")
    if ids_from is not None:
        print(f"IDs {ids_from}: repository {body['repositoryId']}, owner {body['ownerId']}.")
    print(f"Set up the workflow: {host}/{group}/{site}/deploy")
    return 0


def _add_access_flags(parser: argparse.ArgumentParser, defaults: tuple[str, str, str]) -> None:
    """--access, --secret-links and --invitees, with what leaving each out means."""
    base_default, keys_default, invitees_default = defaults
    parser.add_argument(
        "--access",
        choices=ACCESS_BASES,
        default=None,
        help=(
            "Who may see the content: public (anyone), sso (anyone who signs in "
            "with SSO Rijk), site_team (members of the site and its group) or "
            f"nobody (only through the exceptions below). Left out: {base_default}"
        ),
    )
    parser.add_argument(
        "--secret-links",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=f"Whether a valid secret link lets anyone in, without signing in. Left out: {keys_default}",
    )
    parser.add_argument(
        "--invitees",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=f"Whether invitees get in after signing in with SSO Rijk. Left out: {invitees_default}",
    )
    parser.add_argument(
        "--host", default=None, help=HOST_HELP
    )


AUTH_ERROR_MESSAGES = {
    "EXPIRED_TOKEN": "The login attempt expired. Try again with 'plak login'.",
    "ACCESS_DENIED": "Login refused in the browser.",
    "INVALID_GRANT": "Invalid or revoked login attempt. Try again with 'plak login'.",
}


def cmd_login(args: argparse.Namespace) -> int:
    host = _setup(args)

    print(f"Logging in at {host}", file=sys.stderr)
    client_name = f"plak-cli {VERSION} on {platform.system()}"
    response = _request(
        host,
        "POST",
        f"{host}/-/api/v1/cli/device-authorizations",
        json={"clientName": client_name},
        timeout=30.0,
    )
    if response is None:
        return 1
    if response.status_code not in (200, 201):
        _print_problem_detail(response)
        return 1

    start = _problem_data(response)
    device_code = start.get("deviceCode")
    user_code = start.get("userCode")
    raw_complete = start.get("verificationUriComplete")
    raw_uri = start.get("verificationUri")
    if not device_code or not user_code or not (raw_complete or raw_uri):
        print("Error: unexpected answer while starting the login", file=sys.stderr)
        return 1

    # Only a sign-in URL pointing at the same host is shown or opened: a
    # server (or someone imitating it) must not get an arbitrary URL
    # (file://, another domain) clicked through here.
    verification_uri = next(
        (
            candidate
            for candidate in (raw_complete, raw_uri)
            if isinstance(candidate, str) and candidate and _same_origin(candidate, host)
        ),
        None,
    )
    if verification_uri is None:
        print(
            f"Error: the server returned a login URL that does not belong to {host}; "
            "login aborted",
            file=sys.stderr,
        )
        return 1
    verification_uri = _clean(verification_uri)
    user_code = _clean(str(user_code))

    try:
        interval = float(start.get("interval") or 5)
        expires_in = float(start.get("expiresIn") or 900)
    except (TypeError, ValueError):
        interval, expires_in = 5.0, 900.0

    print(f"\nOpen this URL to log in:\n  {verification_uri}\n", file=sys.stderr)
    print(f"Code: {user_code}\n", file=sys.stderr)
    if not args.no_open and sys.stderr.isatty():
        if webbrowser.open(verification_uri):
            print("Browser opened. Finish logging in there.\n", file=sys.stderr)
        else:
            print("Could not open a browser; use the URL above.\n", file=sys.stderr)

    deadline = time.monotonic() + expires_in
    while True:
        if time.monotonic() > deadline:
            print("Error: login attempt expired before it was approved", file=sys.stderr)
            return 1
        time.sleep(interval)
        response = _request(
            host,
            "POST",
            f"{host}/-/api/v1/cli/tokens",
            json={"grantType": "device_code", "deviceCode": device_code},
            timeout=30.0,
        )
        if response is None:
            return 1
        if response.status_code == 200:
            break
        data = _problem_data(response)
        code = data.get("code")
        if code == "AUTHORIZATION_PENDING":
            continue
        if code == "SLOW_DOWN":
            interval += 5
            continue
        message = AUTH_ERROR_MESSAGES.get(
            code, data.get("detail") or data.get("title") or f"HTTP {response.status_code}"
        )
        print(f"Error: {_clean(str(message))}", file=sys.stderr)
        return 1

    payload = _problem_data(response)
    access_token = payload.get("accessToken")
    if not isinstance(access_token, str) or not access_token:
        print("Error: unexpected answer while fetching the token", file=sys.stderr)
        return 1
    refresh_token = payload.get("refreshToken") or ""
    try:
        expires_in_seconds = float(payload.get("expiresIn") or 0)
    except (TypeError, ValueError):
        expires_in_seconds = 0.0
    member = payload.get("member") if isinstance(payload.get("member"), dict) else {}
    email = _clean(member.get("email")) if isinstance(member.get("email"), str) else ""

    fallback_reason = _save_session(
        host,
        access_token,
        refresh_token,
        time.time() + expires_in_seconds,
        insecure=args.insecure_storage,
        make_default=True,
    )
    print(f"Logged in as {email}." if email else "Logged in.")
    if fallback_reason is None:
        print("Session stored in the system keyring.", file=sys.stderr)
    elif args.insecure_storage:
        print(f"Session stored in plain text in {_hosts_path()}.", file=sys.stderr)
    else:
        _warn_plain_text(fallback_reason)
    _note_legacy_env_file()
    return 0


def cmd_logout(args: argparse.Namespace) -> int:
    host = _setup(args)

    entry = _host_entry(_read_hosts(), host)
    try:
        stored_access, refresh_token = _session_tokens(host, entry)
    except UsageError as error:
        # Logging out still has to drop what is stored locally.
        print(f"Warning: {error}", file=sys.stderr)
        stored_access, refresh_token = None, None
    access_token = os.environ.get("PLAK_ACCESS_TOKEN") or stored_access
    revoked = True
    incompatible = None
    if access_token or refresh_token:
        headers = {"Authorization": f"Bearer {access_token}"} if access_token else {}
        json_body = {"refreshToken": refresh_token} if refresh_token else None
        try:
            response = _http(
                "DELETE",
                f"{host}/-/api/v1/cli/session",
                headers=headers,
                json=json_body,
                timeout=30.0,
            )
            revoked = response.status_code in (200, 204)
        except httpx.HTTPError:
            revoked = False
        except IncompatibleServer as error:
            incompatible = error

    _forget_session(host)
    if incompatible:
        print("Logged out locally.")
        raise incompatible
    if (access_token or refresh_token) and not revoked:
        print(
            "Warning: could not revoke the session at the server; "
            "logged out locally.",
            file=sys.stderr,
        )
    print("Logged out.")
    return 0


def cmd_whoami(args: argparse.Namespace) -> int:
    host = _setup(args)
    token = _get_bearer_token(host)

    response = _request(
        host,
        "GET",
        f"{host}/-/api/v1/cli/whoami",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30.0,
    )
    if response is None:
        return 1

    if response.status_code == 200:
        data = _problem_data(response)
        member = data.get("member") if isinstance(data.get("member"), dict) else {}
        email = _clean(member["email"]) if isinstance(member.get("email"), str) else "unknown"
        expires_at = _clean(data["expiresAt"]) if isinstance(data.get("expiresAt"), str) else ""
        suffix = f" (expires {expires_at})" if expires_at else ""
        print(f"Logged in as {email} on {host}{suffix}.")
        return 0

    _print_problem_detail(response)
    return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="plak",
        description="Publish static sites to Plak and manage previews.",
    )
    parser.add_argument("--version", action="version", version=f"plak {VERSION}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    login = subparsers.add_parser(
        "login", help="Log in and store the session for your user account."
    )
    login.add_argument("--host", default=None, help=HOST_HELP)
    login.add_argument(
        "--no-open",
        action="store_true",
        help="Do not open the login URL in the browser automatically",
    )
    login.add_argument(
        "--insecure-storage",
        action="store_true",
        help="Store the session in plain text in the config directory instead of the system keyring",
    )
    login.set_defaults(func=cmd_login)

    logout = subparsers.add_parser("logout", help="Log out and wipe the session.")
    logout.add_argument("--host", default=None, help=HOST_HELP)
    logout.set_defaults(func=cmd_logout)

    whoami = subparsers.add_parser("whoami", help="Show who is logged in.")
    whoami.add_argument("--host", default=None, help=HOST_HELP)
    whoami.set_defaults(func=cmd_whoami)

    publish = subparsers.add_parser(
        "publish",
        help="Publish a dist folder or file as a live or preview version.",
    )
    publish.add_argument(
        "dist_path", help="Path to the dist folder or the file to publish"
    )
    publish.add_argument("--host", default=None, help=HOST_HELP)
    publish.add_argument("--site", required=True, help="group/site")
    publish.add_argument(
        "--preview",
        help="Preview ref, for instance pr-42; without this field it is a live deploy",
    )
    publish.add_argument(
        "--base-path",
        default=None,
        help=(
            "Folder inside the archive that becomes the root of the site, for "
            "instance dist. Use this when the bundle holds more than the site "
            "itself; without this flag Plak determines the root"
        ),
    )
    publish.add_argument(
        "--output-file",
        default=None,
        help=(
            "Write 'version-id=<id>' to this file (for instance $GITHUB_OUTPUT) "
            "instead of printing the id on stdout; used by the action"
        ),
    )
    publish.set_defaults(func=cmd_publish)

    remove = subparsers.add_parser(
        "preview-remove", help="Remove a preview (idempotent)."
    )
    remove.add_argument("ref", help="Preview ref to remove")
    remove.add_argument("--host", default=None, help=HOST_HELP)
    remove.add_argument("--site", required=True, help="group/site")
    remove.set_defaults(func=cmd_preview_remove)

    group = subparsers.add_parser("group", help="Manage groups.")
    group_commands = group.add_subparsers(dest="group_command", required=True)
    group_create = group_commands.add_parser(
        "create",
        help="Create a group; you become its admin.",
        description=(
            "Create a group and become its admin. The access flags set the "
            "default access that new sites in the group start with."
        ),
    )
    group_create.add_argument("group", help="Slug of the group, for instance team-aurora")
    group_create.add_argument("--name", required=True, help="Display name of the group")
    _add_access_flags(group_create, ("site_team", "off", "off"))
    group_create.set_defaults(func=cmd_group_create)

    site = subparsers.add_parser("site", help="Manage sites.")
    site_commands = site.add_subparsers(dest="site_command", required=True)
    site_create = site_commands.add_parser(
        "create",
        help="Create a site in a group; you become its admin.",
        description=(
            "Create a site in a group where you are editor or admin, and become "
            "admin of the site. Access flags you leave out follow the group's "
            "default access."
        ),
    )
    site_create.add_argument("site", help="group/site, for instance team-aurora/docs")
    site_create.add_argument("--title", required=True, help="Display name of the site")
    _add_access_flags(site_create, ("the group's default",) * 3)
    site_create.set_defaults(func=cmd_site_create)

    site_link = site_commands.add_parser(
        "link",
        help="Link the repository that may publish to a site from CI.",
        description=(
            "Link a GitHub or Forgejo repository to a site where you are admin, so "
            "its workflows may publish with an OIDC ID token, without a secret. "
            "Without a repository argument it takes the remote 'origin' of the "
            "git checkout you are in. For a GitHub repository the IDs come from "
            "your own 'gh' login, so a private repository links too."
        ),
    )
    site_link.add_argument("site", help="group/site, for instance team-aurora/docs")
    site_link.add_argument(
        "repository",
        nargs="?",
        default=None,
        help=(
            "owner/repo (on GitHub) or the repository URL, for instance "
            "https://code.overheid.nl/minbzk/website. Left out: the remote 'origin' here"
        ),
    )
    site_link.add_argument(
        "--live-branch",
        default=None,
        help=(
            "The only branch that may publish live, for instance main. Left out: every "
            "branch may, still only from a push, a manual run or a schedule"
        ),
    )
    site_link.add_argument(
        "--repository-id",
        type=int,
        default=None,
        help="Numeric ID of the repository, for one Plak and gh cannot look up (with --owner-id)",
    )
    site_link.add_argument(
        "--owner-id", type=int, default=None, help="Numeric ID of the repository's owner (with --repository-id)"
    )
    site_link.add_argument(
        "--no-gh",
        dest="gh",
        action="store_false",
        help="Do not ask gh for the IDs; Plak looks the repository up itself, which finds a public one only",
    )
    site_link.add_argument("--host", default=None, help=HOST_HELP)
    site_link.set_defaults(func=cmd_site_link)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as error:
        code = error.code if isinstance(error.code, int) else 2
        return code
    try:
        return args.func(args)
    except UsageError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2
    except IncompatibleServer as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
