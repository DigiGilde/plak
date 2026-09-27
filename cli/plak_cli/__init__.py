"""Plak CLI: publish static sites and clean up previews.

Install with `uv tool install`, or run it from a checkout without
installing with `uv run --project cli plak ...`; see README.md.

Usage:
    plak login [--host <host>] [--no-open]
    plak logout [--host <host>]
    plak whoami [--host <host>]
    plak publish <dist-dir-or-file> --host <host> \
        --site <group/site> [--preview <ref>] [--base-path <dir>] \
        [--output-file <path>]
    plak preview-remove <ref> --host <host> --site <group/site>

Signing in happens with 'plak login': the session
(accessToken/refreshToken) is kept in .env.plak in the current directory.
In CI an OIDC token is used automatically (GitHub Actions with
'id-token: write', Forgejo Actions with 'enable-openid-connect: true'), or
supply a token yourself through the environment variable
PLAK_ACCESS_TOKEN.

Exit codes:
    0 - success
    1 - error from the server (a network or API error, say); detail on
        stderr
    2 - wrong usage (a missing or invalid argument, an unknown path, no
        session)
"""

from __future__ import annotations

import argparse
import io
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
from pathlib import Path

import httpx

VERSION = "0.1.0"

ENV_FILENAME = ".env.plak"

ALLOWED_ARCHIVE_EXTENSIONS = {".html", ".zip", ".tar.gz", ".tgz"}

# Slug for group, site and preview ref: identical to the server contract
# regex, so a ref or site part can never leak into the URL as an extra path
# segment.
SLUG_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
VERSION_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)

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


class UsageError(Exception):
    """An error in the call itself: a wrong argument, a missing path, an invalid file type."""


def _valid_slug(value: str, what: str) -> str:
    if not SLUG_RE.match(value):
        raise UsageError(
            f"{what} must be a valid slug (a-z, 0-9, hyphen), got: {value!r}"
        )
    return value


def _split_site(site: str) -> tuple[str, str]:
    parts = site.split("/")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise UsageError(
            f"--site must have the form 'group/site', got: {site!r}"
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
    if host.startswith("https://"):
        return
    if _LOOPBACK_RE.match(host):
        return
    raise UsageError(f"Use https:// for {host} (http only for loopback)")


def _resolve_host(args: argparse.Namespace) -> str:
    """Host from --host, PLAK_HOST in the environment, or PLAK_HOST in
    .env.plak (only if that file can be trusted, see
    _trusted_stored_host)."""
    host = getattr(args, "host", None) or os.environ.get("PLAK_HOST") or _trusted_stored_host()
    if not host:
        raise UsageError(
            "No host: pass --host or log in first with 'plak login --host <host>'"
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


def _env_path() -> Path:
    return Path.cwd() / ENV_FILENAME


def _read_env_file() -> dict[str, str]:
    path = _env_path()
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("\"'")
    return values


def _write_env_file(updates: dict[str, str | None]) -> Path:
    """Sets or removes keys in .env.plak, the rest stays. Mode 0600."""
    path = _env_path()
    lines = path.read_text().splitlines() if path.exists() else []
    remaining = dict(updates)

    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        key = (
            stripped.partition("=")[0].strip()
            if "=" in stripped and not stripped.startswith("#")
            else None
        )
        if key in remaining:
            value = remaining.pop(key)
            if value is not None:
                out.append(f"{key}={value}")
            continue
        out.append(line)

    for key, value in remaining.items():
        if value is not None:
            out.append(f"{key}={value}")

    # Holds tokens: first a 0600 file next to it (mkstemp), then an atomic
    # replace. Writing and chmodding afterwards leaves the file readable to
    # others for a moment, and an existing 0644 file even with fresh tokens.
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".env.plak.")
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write("\n".join(out) + "\n" if out else "")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return path


def _warn_if_not_gitignored() -> None:
    path = _env_path()
    try:
        result = subprocess.run(
            ["git", "check-ignore", "-q", str(path)],
            capture_output=True,
            cwd=str(Path.cwd()),
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return
    if result.returncode == 1:
        print(
            f"Warning: {ENV_FILENAME} is not in .gitignore. Add it there before you "
            "commit, or your session ends up in git.",
            file=sys.stderr,
        )


def _is_git_tracked(path: Path) -> bool:
    try:
        result = subprocess.run(
            ["git", "ls-files", "--error-unmatch", str(path)],
            capture_output=True,
            cwd=str(path.parent),
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _trusted_stored_host() -> str | None:
    """PLAK_HOST from .env.plak, but only if nothing other than this CLI can
    have changed the file: owned by the current user, mode 0600, and not
    taken along by git. A PLAK_HOST from a file that does not meet those
    demands is ignored: otherwise a tampered file (or one committed by
    accident) could send a session or OIDC token to someone else's
    server."""
    path = _env_path()
    try:
        info = path.stat()
    except OSError:
        return None
    trustworthy = (
        info.st_uid == os.getuid()
        and stat.S_IMODE(info.st_mode) == 0o600
        and not _is_git_tracked(path)
    )
    if not trustworthy:
        print(
            f"Warning: {ENV_FILENAME} is not trusted as a source for the host "
            "(owner, file permissions or git tracking is off); pass --host "
            "explicitly.",
            file=sys.stderr,
        )
        return None
    return _read_env_file().get("PLAK_HOST")


def _problem_data(response: httpx.Response) -> dict:
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _refresh_token(host: str, refresh_token: str) -> str:
    try:
        response = httpx.post(
            f"{host}/-/api/v1/cli/tokens",
            json={"grantType": "refresh_token", "refreshToken": refresh_token},
            timeout=30.0,
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
    _write_env_file(
        {
            "PLAK_ACCESS_TOKEN": access_token,
            "PLAK_REFRESH_TOKEN": refresh_out,
            "PLAK_ACCESS_EXPIRES_AT": str(expires_at),
        }
    )
    return access_token


def _stored_token(host: str) -> str | None:
    """Stored session for this host, refreshed if need be. None if there is none."""
    stored_host = _trusted_stored_host()
    if not stored_host or stored_host.rstrip("/") != host.rstrip("/"):
        return None
    data = _read_env_file()
    token = data.get("PLAK_ACCESS_TOKEN")
    if not token:
        return None
    expires_at = data.get("PLAK_ACCESS_EXPIRES_AT")
    if expires_at:
        try:
            # 60 seconds of slack: a token that expires while the request is
            # on its way is as useless as one that has already expired.
            expired = float(expires_at) - 60 <= time.time()
        except ValueError:
            expired = False
        if expired:
            refresh = data.get("PLAK_REFRESH_TOKEN")
            if not refresh:
                raise UsageError("Session expired: log in again with 'plak login'")
            token = _refresh_token(host, refresh)
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
        response = httpx.get(
            url, headers={"Authorization": f"bearer {request_token}"}, timeout=30.0
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
    # GitHub Actions and Forgejo Actions both read these workflow commands
    # from stdout: this masks the token in the rest of the log.
    print(f"::add-mask::{token}")
    return token


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
    raise UsageError(
        "No token: log in with 'plak login --host <host>', or set "
        "PLAK_ACCESS_TOKEN (for instance an OIDC token in CI, see README)"
    )


def _file_extension(path: Path) -> str:
    name = path.name.lower()
    for ext in (".tar.gz", ".tgz", ".zip", ".html"):
        if name.endswith(ext):
            return ext
    return path.suffix.lower()


def _pack_folder(dist_folder: Path) -> bytes:
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
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for file_path, relative_path in files:
            tar.add(file_path, arcname=str(relative_path))
    return buffer.getvalue()


def _determine_upload(dist_path: Path) -> tuple[bytes, str, str]:
    """Returns (content, filename, content type) for the upload.

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
    content = dist_path.read_bytes()
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
    try:
        host = args.host.rstrip("/")
        _require_https(host)
        token = _get_bearer_token(host)
        group, site = _split_site(args.site)
        if args.preview:
            _valid_slug(args.preview, "preview-ref")
        base_path = (
            _valid_base_path(args.base_path) if args.base_path is not None else None
        )
        content, file_name, content_type = _determine_upload(dist_path)
    except UsageError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2

    url = f"{host}/-/api/v1/sites/{group}/{site}/deploys"

    form_fields: dict[str, str] = {}
    if args.preview:
        form_fields["preview"] = args.preview
    if base_path is not None:
        form_fields["basePath"] = base_path

    try:
        response = httpx.post(
            url,
            headers={"Authorization": f"Bearer {token}"},
            files={"file": (file_name, content, content_type)},
            data=form_fields,
            timeout=120.0,
        )
    except httpx.HTTPError as error:
        print(f"Error: could not connect to {host}: {error}", file=sys.stderr)
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
        if args.output_file:
            # Straight to the output file ($GITHUB_OUTPUT, say): in CI stdout
            # also carries the '::add-mask::' command, and a shell capturing
            # stdout for the version id would take that command along.
            with open(args.output_file, "a", encoding="utf-8") as handle:
                handle.write(f"version-id={version_id}\n")
        else:
            print(version_id)
        return 0

    _print_problem_detail(response)
    return 1


def cmd_preview_remove(args: argparse.Namespace) -> int:
    try:
        host = args.host.rstrip("/")
        _require_https(host)
        token = _get_bearer_token(host)
        group, site = _split_site(args.site)
        _valid_slug(args.ref, "preview-ref")
    except UsageError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2

    url = f"{host}/-/api/v1/sites/{group}/{site}/previews/{args.ref}"

    try:
        response = httpx.delete(
            url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=60.0,
        )
    except httpx.HTTPError as error:
        print(f"Error: could not connect to {host}: {error}", file=sys.stderr)
        return 1

    if response.status_code in (200, 204):
        print(f"Preview '{args.ref}' removed (or was already gone).")
        return 0

    _print_problem_detail(response)
    return 1


AUTH_ERROR_MESSAGES = {
    "EXPIRED_TOKEN": "The login attempt expired. Try again with 'plak login'.",
    "ACCESS_DENIED": "Login refused in the browser.",
    "INVALID_GRANT": "Invalid or revoked login attempt. Try again with 'plak login'.",
}


def cmd_login(args: argparse.Namespace) -> int:
    try:
        host = _resolve_host(args)
        _require_https(host)
    except UsageError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2

    print(f"Logging in at {host}", file=sys.stderr)
    client_name = f"plak-cli {VERSION} on {platform.system()}"
    try:
        response = httpx.post(
            f"{host}/-/api/v1/cli/device-authorizations",
            json={"clientName": client_name},
            timeout=30.0,
        )
    except httpx.HTTPError as error:
        print(f"Error: could not connect to {host}: {error}", file=sys.stderr)
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
        try:
            response = httpx.post(
                f"{host}/-/api/v1/cli/tokens",
                json={"grantType": "device_code", "deviceCode": device_code},
                timeout=30.0,
            )
        except httpx.HTTPError as error:
            print(f"Error: could not connect to {host}: {error}", file=sys.stderr)
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

    _write_env_file(
        {
            "PLAK_HOST": host,
            "PLAK_ACCESS_TOKEN": access_token,
            "PLAK_REFRESH_TOKEN": refresh_token,
            "PLAK_ACCESS_EXPIRES_AT": str(time.time() + expires_in_seconds),
        }
    )
    _warn_if_not_gitignored()
    print(f"Logged in as {email}." if email else "Logged in.")
    return 0


def cmd_logout(args: argparse.Namespace) -> int:
    try:
        host = _resolve_host(args)
        _require_https(host)
    except UsageError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2

    data = _read_env_file()
    access_token = os.environ.get("PLAK_ACCESS_TOKEN") or data.get("PLAK_ACCESS_TOKEN")
    refresh_token = data.get("PLAK_REFRESH_TOKEN")
    revoked = True
    if access_token or refresh_token:
        headers = {"Authorization": f"Bearer {access_token}"} if access_token else {}
        json_body = {"refreshToken": refresh_token} if refresh_token else None
        try:
            # httpx.delete() itself refuses a body (the old
            # DELETE-has-no-body convention); httpx.request() does not.
            response = httpx.request(
                "DELETE",
                f"{host}/-/api/v1/cli/session",
                headers=headers,
                json=json_body,
                timeout=30.0,
            )
            revoked = response.status_code in (200, 204)
        except httpx.HTTPError:
            revoked = False

    _write_env_file(
        {
            "PLAK_ACCESS_TOKEN": None,
            "PLAK_REFRESH_TOKEN": None,
            "PLAK_ACCESS_EXPIRES_AT": None,
        }
    )
    if (access_token or refresh_token) and not revoked:
        print(
            "Warning: could not revoke the session at the server; "
            "logged out locally.",
            file=sys.stderr,
        )
    print("Logged out.")
    return 0


def cmd_whoami(args: argparse.Namespace) -> int:
    try:
        host = _resolve_host(args)
        _require_https(host)
        token = _get_bearer_token(host)
    except UsageError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2

    try:
        response = httpx.get(
            f"{host}/-/api/v1/cli/whoami",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30.0,
        )
    except httpx.HTTPError as error:
        print(f"Error: could not connect to {host}: {error}", file=sys.stderr)
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
    subparsers = parser.add_subparsers(dest="command", required=True)

    login = subparsers.add_parser(
        "login", help="Log in and store the session in .env.plak."
    )
    login.add_argument(
        "--host",
        default=None,
        help="Admin origin of Plak, for instance https://beheer.plak.example.nl",
    )
    login.add_argument(
        "--no-open",
        action="store_true",
        help="Do not open the login URL in the browser automatically",
    )
    login.set_defaults(func=cmd_login)

    logout = subparsers.add_parser("logout", help="Log out and wipe the session.")
    logout.add_argument("--host", default=None)
    logout.set_defaults(func=cmd_logout)

    whoami = subparsers.add_parser("whoami", help="Show who is logged in.")
    whoami.add_argument("--host", default=None)
    whoami.set_defaults(func=cmd_whoami)

    publish = subparsers.add_parser(
        "publish",
        help="Publish a dist folder or file as a live or preview version.",
    )
    publish.add_argument(
        "dist_path", help="Path to the dist folder or the file to publish"
    )
    publish.add_argument(
        "--host", required=True, help="Admin origin of Plak, for instance https://beheer.plak.example.nl"
    )
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
    remove.add_argument("--host", required=True)
    remove.add_argument("--site", required=True, help="group/site")
    remove.set_defaults(func=cmd_preview_remove)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as error:
        code = error.code if isinstance(error.code, int) else 2
        return code
    return args.func(args)
