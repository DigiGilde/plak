"""Application factory: create_app(), lifespan, router registration.

Middleware from the inside out: Bearer guard, rate limit, SPA (outside the
rate limit: SPA assets are unlimited), host separation (outside rate
limit and SPA, but inside the security headers so its neutral 404 carries the
same header set as the router's neutral 404), security headers, TrustedHost,
API-Version. Three of those know the content host, and they all derive it from
the same PLAK_CONTENT_BASE_URL: the SPA answers on the admin host only, the
host separation decides which world a path belongs to, and the security headers
put the admin regime on the admin host alone. The app serves content, SPA and
platform routes itself and enforces the origin separation itself; any proxy in
front of it is dumb.

Routers in order: deploy API (bearer or session, with origin checking through
`require_admin_origin`: it lets CI through without Origin/Sec-Fetch-Site but
turns away a same-site content origin), the CLI login API (the
same origin check), session API for the SPA
(origin checking registered in the router itself), API docs, platform and
auth routes (`platform.pages`), and the serving router last because it is a
content catch-all (`/{group}/{site}/{rest:path}`) that would otherwise
swallow every later route; its own last route catches whatever no route at all
claimed and answers the neutral 404. The problem+json handlers from
api/errors.py cover the whole app but reshape `/-/api/` paths only.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

import httpx
from fastapi import Depends, FastAPI
from starlette.datastructures import MutableHeaders
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from plak.api import docs
from plak.api.admin import make_admin_router
from plak.api.cli import router as cli_router
from plak.api.deploys import BearerOutsideDeploysMiddleware
from plak.api.deploys import router as deploys_router
from plak.api.errors import register_error_handlers
from plak.api.origin_guard import normalise_origin, require_admin_origin
from plak.audit.log import AuditLog
from plak.auth.oidc import OidcClient
from plak.auth.revalidation import revalidate_sessions
from plak.auth.sessions import (
    SessionStore,
    content_anchor_session_from_request,
    content_session_from_request,
    session_from_request,
)
from plak.ci.providers import ProviderClient
from plak.ci.tokens import CiTokenVerifier
from plak.config import Settings, load_settings
from plak.constants import PATH_HEALTHZ
from plak.db import make_engine, make_session_factory
from plak.host_separation import HostSeparationMiddleware
from plak.ingest.storage_health import content_root_complaint, storage_check_job, storage_watch
from plak.ingest.store import ContentStore
from plak.platform import pages
from plak.platform.backchannel import router as backchannel_router
from plak.platform.health import health_response
from plak.platform.spa import SpaMiddleware, admin_csp, spa_available
from plak.previews.cleanup_job import cleanup_job
from plak.ratelimit import InMemoryCounter, make_rate_limit_middleware
from plak.security_headers import SecurityHeadersMiddleware, is_https
from plak.serving.code_page import router as code_router
from plak.serving.router import router as serving_router

API_VERSION = "1.0.0"

_logger = logging.getLogger(__name__)


class ApiVersionHeaderMiddleware:
    """Sets the ADR header `API-Version` on every response."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_version(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["API-Version"] = API_VERSION
            await send(message)

        await self.app(scope, receive, send_with_version)


class SessionRecheckMiddleware:
    """Re-validates a session against the IdP when it is due, before any route
    reads it (auth/revalidation.py). A session dropped here is gone from the
    store, so the request continues as one without a session."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            # Cookies only; the body stays untouched for the route itself.
            await revalidate_sessions(Request(scope, receive))
        await self.app(scope, receive, send)


async def _session_key(request: Request) -> str | None:
    """Rate-limit key for authenticated requests: per session sub, not per IP.
    Management and content session both count: a logged-in viewer on the
    content host has only the content cookie."""
    session = (
        session_from_request(request)
        or content_session_from_request(request)
        or content_anchor_session_from_request(request)
    )
    return session.sub if session is not None else None


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()

    content_host = settings.content_host

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Names the IdP coupling in the log, so a wrong issuer or client id is
        # visible in the first lines instead of only in a failing login. Never
        # the client secret or the private key.
        _logger.info(
            "OIDC-koppeling: issuer %s, client_id %s",
            settings.oidc_issuer,
            settings.oidc_client_id,
        )
        if not spa_available(settings.spa_path):
            _logger.warning(
                "Beheer-SPA niet gevonden in %s (PLAK_SPA_PATH): de beheer-host antwoordt 503 "
                "tot de gebouwde frontend daar staat",
                settings.spa_path,
            )
        engine = make_engine(settings)
        session_factory = make_session_factory(engine)
        oidc_http = httpx.AsyncClient()
        # Its own client: CI providers get no redirects and a short timeout on
        # every call (ci/providers.py), whatever the IdP client does.
        ci_http = httpx.AsyncClient(follow_redirects=False)
        content_store = ContentStore(settings.content_root)
        if settings.environment == "productie":
            root_complaint = content_root_complaint(content_store.root)
            storage_watch(app).content_root_message = root_complaint
            if root_complaint:
                _logger.error("%s Check PLAK_CONTENT_ROOT and the volume mount.", root_complaint)

        app.state.engine = engine
        app.state.settings = settings
        app.state.session_factory = session_factory
        app.state.session_store = SessionStore()
        app.state.content_store = content_store
        app.state.audit_log = AuditLog(session_factory, settings.audit_pepper, settings.audit_ip_key_bytes)
        # Counts code attempts per selector, next to the per-IP counting the
        # rate-limit middleware does (serving/code_page.py).
        app.state.code_attempts = InMemoryCounter()
        app.state.oidc_client = OidcClient(settings, oidc_http)
        app.state.ci_verifier = CiTokenVerifier(settings, ci_http)
        app.state.ci_providers = ProviderClient(ci_http)
        try:
            async with (
                cleanup_job(
                    session_factory, content_store, live_versions_kept=settings.live_versions_kept
                ) as cleanup_task,
                storage_check_job(app, content_store, settings) as storage_task,
            ):
                app.state.cleanup_task = cleanup_task
                app.state.storage_task = storage_task
                yield
        finally:
            await oidc_http.aclose()
            await ci_http.aclose()
            await engine.dispose()

    app = FastAPI(
        title="Plak API",
        summary="Statische sites publiceren en delen, met previews per pull request.",
        description=docs.API_DESCRIPTION,
        version=API_VERSION,
        contact={
            "name": "Plak",
            "url": "https://github.com/DigiGilde/plak/issues",
            "email": "digigilde@rijksoverheid.nl",
        },
        servers=[{"url": "/-/api/v1"}],
        openapi_tags=docs.OPENAPI_TAGS,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    register_error_handlers(app)
    docs.register_openapi(app)

    # Innermost middleware: a Bearer header outside the two deploy endpoints is
    # refused, but only after ratelimit and TrustedHost have done their work.
    app.add_middleware(BearerOutsideDeploysMiddleware)

    # Inside the SPA and the rate limit: static assets and refused requests
    # never cost a call to the IdP.
    app.add_middleware(SessionRecheckMiddleware)

    ratelimit_class, ratelimit_kwargs = make_rate_limit_middleware(
        settings, get_authenticated_key=_session_key
    )
    app.add_middleware(ratelimit_class, **ratelimit_kwargs)

    # Outside the ratelimit (SPA assets are unlimited) and inside the host
    # separation: the SPA is the fallback for the whole admin host, so it has
    # to know which host it is on itself.
    # The logout form passes the content host, and with RP-initiated logout on
    # the IdP; Chrome checks each redirect of a form navigation against
    # form-action. Taken from the issuer: an OP whose end_session_endpoint sits
    # on another origin needs that origin here instead.
    form_targets = [normalise_origin(settings.content_base_url)]
    if settings.oidc_rp_logout:
        form_targets.append(normalise_origin(settings.oidc_issuer))
    csp = admin_csp(*(target for target in form_targets if target))
    app.add_middleware(SpaMiddleware, spa_path=settings.spa_path, content_host=content_host, csp=csp)

    # Outside SPA and ratelimit: a path on the wrong host is a routing miss
    # that costs no budget and serves nothing. Inside the security headers:
    # otherwise its neutral 404 lacks Permissions-Policy and HSTS and can be
    # told apart from the router's 404.
    app.add_middleware(HostSeparationMiddleware, content_host=content_host)

    app.add_middleware(
        SecurityHeadersMiddleware, hsts=is_https(settings.base_url), content_host=content_host, admin_csp=csp
    )

    if settings.base_url:
        # Added after ratelimit, so further out: a forged Host is refused
        # before it counts against any rate-limit budget.
        # Two origins share this one app: besides the admin host,
        # the content host is on the allowlist too.
        allowed_hosts = {urlsplit(settings.base_url).hostname, content_host}
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=sorted(allowed_hosts))

    # Outermost middleware: 400/429 responses from TrustedHost and ratelimit
    # carry the API-Version header as well.
    app.add_middleware(ApiVersionHeaderMiddleware)

    # No dependencies and no session: reachable by anyone on the admin host,
    # and refused as a neutral 404 on the content host (host_separation.py).
    @app.get(PATH_HEALTHZ, include_in_schema=False)
    async def health() -> JSONResponse:
        return await health_response(app)

    # Deploy API before the session API. The origin check sits here too: it
    # lets bearer CI through (no Origin/Sec-Fetch-Site) but turns away a
    # same-site content origin that would send the Strict session cookie along
    # to the session branch of these mutating endpoints.
    app.include_router(deploys_router, dependencies=[Depends(require_admin_origin)])
    app.include_router(cli_router, dependencies=[Depends(require_admin_origin)])
    app.include_router(make_admin_router())
    app.include_router(docs.router)

    # Platform/auth routes before the serving catch-all, otherwise
    # /{group}/{site} would already capture paths like /-/login.
    app.include_router(pages.router)
    # Admin host only (host_separation.py); no origin check, because the OP
    # calls it server to server and authenticates with the logout token itself.
    app.include_router(backchannel_router)
    # The only POST on the content host; before the serving catch-all for the
    # same reason.
    app.include_router(code_router)
    app.include_router(serving_router)

    return app
