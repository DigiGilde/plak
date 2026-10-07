"""May this verified CI token deploy to this site?

The site names one repository (site_repositories). A token matches it when
its issuer is that repository's provider and host, and:

- the token carries `repository_id` (GitHub, Forgejo 16 and later): it must
  equal the stored id, and `repository_owner_id`, when present, the stored
  owner id. Ids survive a rename; names do not. A transfer to another owner
  changes the owner id, so the token is refused until the site is relinked.
- the token carries no ids (Forgejo 15, e.g. code.overheid.nl): `repository`
  must equal the stored `owner/repo` (case-insensitive), and Plak asks the
  Forgejo REST API whether `owner/repo` still has the stored ids, so a
  repository deleted and recreated under the same name does not inherit the
  trust. GitHub always sends ids; a GitHub token without them is refused.

A token bound to a site (its audience names the site id, ci/tokens.py)
matches that site only. A token without a site id, its audience the instance
itself, matches only a link that still accepts one (`site_id_required`
false: a link from before migration 0004 that got no other repository and
that no site admin closed since); elsewhere it is refused with
CI_SITE_ID_REQUIRED, but only once the repository itself matched, so a
stranger still gets CI_REPOSITORY_NOT_TRUSTED.

The repository's name on the trusted result, and so the origin a version
records, comes from the token's signed `repository` claim: the stored name
is whatever an admin typed for a private repository and goes stale after a
rename. A missing or malformed claim falls back to the stored name.
follow_token then brings the stored name in line, so the Deploy tab shows
it too, and marks ids an admin entered as confirmed once a token has matched
both of them.

A live deploy additionally needs an event from LIVE_EVENTS (fail closed: a
missing or any other event_name is refused) and, when one is set, the live
branch; previews and preview teardown accept any ref and any event.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from plak.audit import vocabulary
from plak.ci.providers import ProviderClient, ProviderUnavailableError, host_label, valid_name
from plak.ci.tokens import CiTokenError, VerifiedCiToken
from plak.models.ci import CiProvider, SiteRepository
from plak.models.publication import Site

# The only events that may go live: each runs on a ref someone with write
# access to the repository chose. pull_request*, issue_comment, workflow_run
# and friends can be triggered by outsiders or run on outside code.
LIVE_EVENTS = frozenset({"push", "workflow_dispatch", "schedule"})

# Claims copied into the audit record of a CI deploy. None of them is secret;
# each is capped so a hostile issuer cannot bloat the log.
AUDIT_CLAIMS = ("repository", "ref", "sha", "run_id", "workflow", "event_name")
MAX_CLAIM_LENGTH = 200


def ci_actor_identifier(provider: CiProvider | str, host: str, repository: str | int) -> str:
    """What a CI actor is pseudonymised from: provider, host and the numeric
    repository id (or, for a refused token without ids, `owner/repo` in
    lowercase). api/admin.py recomputes it to resolve a pseudonym."""
    return f"{provider}:{host}:{repository}"


def refused_actor_identifier(token: VerifiedCiToken) -> str:
    repository = token.claim("repository_id") or (token.claim("repository") or "").lower()
    return ci_actor_identifier(token.issuer.provider, token.issuer.host, repository)


def audit_refs(token: VerifiedCiToken) -> dict[str, str | bool]:
    refs: dict[str, str | bool] = {"provider": str(token.issuer.provider)}
    for name in AUDIT_CLAIMS:
        value = token.claim(name)
        if value is not None:
            refs[name] = value[:MAX_CLAIM_LENGTH]
    refs["site_bound"] = token.bound_site_id is not None
    if token.bound_site_id is not None:
        refs["bound_site_id"] = str(token.bound_site_id)
    return refs


@dataclass(frozen=True)
class TrustedRepository:
    provider: CiProvider
    host: str
    owner: str
    repo: str
    repository_id: int
    owner_id: int
    live_branch: str | None
    # Whether the token vouched for both stored ids: a GitHub or Forgejo 16
    # token matches the owner id only when it carries one.
    confirms_ids: bool = False

    @property
    def actor_identifier(self) -> str:
        return ci_actor_identifier(self.provider, self.host, self.repository_id)

    @property
    def origin(self) -> str:
        """How a CI deploy is recorded on its version: `github.com/owner/repo`."""
        return f"{host_label(self.host)}/{self.owner}/{self.repo}"


def _claimed_name(token: VerifiedCiToken) -> tuple[str, str] | None:
    """The token's `repository` claim as (owner, repo), or None when it is
    missing or not a valid `owner/repo`."""
    parts = (token.claim("repository") or "").split("/")
    if len(parts) != 2 or not all(valid_name(part) for part in parts):
        return None
    return parts[0], parts[1]


def _not_trusted() -> CiTokenError:
    return CiTokenError(vocabulary.CI_REPOSITORY_NOT_TRUSTED, status=403)


async def trusted_repository(
    db: AsyncSession, token: VerifiedCiToken, site: Site, providers: ProviderClient
) -> TrustedRepository:
    # api/deploys.py only asks about the site a bound token names; this keeps
    # any other caller from trusting it elsewhere.
    if token.bound_site_id is not None and token.bound_site_id != site.id:
        raise CiTokenError(f"{vocabulary.CI_AUDIENCE_MISMATCH}.site")
    row = await db.scalar(select(SiteRepository).where(SiteRepository.site_id == site.id))
    if row is None or row.provider != token.issuer.provider or row.host != token.issuer.host:
        raise _not_trusted()

    repository_id = token.claim("repository_id")
    if repository_id is not None:
        if repository_id != str(row.repository_id):
            raise _not_trusted()
        owner_id = token.claim("repository_owner_id")
        if owner_id is not None and owner_id != str(row.owner_id):
            raise _not_trusted()
        confirms_ids = owner_id is not None
    else:
        if row.provider != CiProvider.FORGEJO:
            raise _not_trusted()
        repository = token.claim("repository")
        if repository is None or repository.lower() != f"{row.owner}/{row.repo}".lower():
            raise _not_trusted()
        try:
            still = await providers.still_has_ids(
                row.provider, row.host, row.owner, row.repo, row.repository_id, row.owner_id
            )
        except ProviderUnavailableError as error:
            raise CiTokenError(
                f"{vocabulary.CI_PROVIDER_UNREACHABLE}.repository", status=503
            ) from error
        if not still:
            raise _not_trusted()
        confirms_ids = True

    if row.site_id_required and token.bound_site_id is None:
        raise CiTokenError(vocabulary.CI_SITE_ID_REQUIRED, status=403)

    owner, repo = _claimed_name(token) or (row.owner, row.repo)
    return TrustedRepository(
        provider=row.provider,
        host=row.host,
        owner=owner,
        repo=repo,
        repository_id=row.repository_id,
        owner_id=row.owner_id,
        live_branch=row.live_branch,
        confirms_ids=confirms_ids,
    )


async def follow_token(db: AsyncSession, site: Site, repository: TrustedRepository) -> str | None:
    """Stores the name a trusted token gave, when it differs from the stored
    one, and marks the ids confirmed when the token vouched for both; returns
    the previous `owner/repo` on a rename, else None. Matched on the ids too,
    so a link replaced in the meantime is left alone; the row lock keeps an
    admin relinking from slipping in before the commit."""
    row = await db.scalar(
        select(SiteRepository)
        .where(
            SiteRepository.site_id == site.id,
            SiteRepository.provider == repository.provider,
            SiteRepository.host == repository.host,
            SiteRepository.repository_id == repository.repository_id,
            SiteRepository.owner_id == repository.owner_id,
        )
        .with_for_update()
    )
    if row is None:
        return None
    previous = None
    if (row.owner, row.repo) != (repository.owner, repository.repo):
        previous = f"{row.owner}/{row.repo}"
        row.owner, row.repo = repository.owner, repository.repo
    if repository.confirms_ids and not row.ids_confirmed:
        row.ids_confirmed = True
    await db.commit()
    return previous


def check_live_deploy(repository: TrustedRepository, token: VerifiedCiToken) -> None:
    """Only a push, a manual run or a schedule may go live, and with a live
    branch set only on exactly that branch."""
    if token.claim("event_name") not in LIVE_EVENTS:
        raise CiTokenError(f"{vocabulary.CI_BRANCH_NOT_ALLOWED}.event", status=403)
    if repository.live_branch is None:
        return
    if token.claim("ref") != f"refs/heads/{repository.live_branch}":
        raise CiTokenError(
            f"{vocabulary.CI_BRANCH_NOT_ALLOWED}.branch",
            params={"branch": repository.live_branch},
            status=403,
        )
