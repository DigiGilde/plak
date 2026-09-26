#!/usr/bin/env python3
"""Fills the dev database and the content volume with example content.

Run it with `just seed`, which pipes this file into the running app
container: that container already has the plak package installed, the
PLAK_* environment and the content volume mounted on PLAK_CONTENT_ROOT.

The seed is destructive and repeatable. It first drops every group and every
member (cascading to sites, versions, previews, invitees, access keys, linked
repositories and CLI sessions) plus the matching version directories on the content volume,
and then writes the whole example set again. The append-only audit log is
never touched: the runtime account has no DELETE on it, and an audit trail
that a script can rewrite is worth nothing.

What it puts down:

  - two groups with a different default access policy;
  - one group holding an admin, an editor and a reader, so the role model is
    visible in one screen;
  - a member with a site role only, without group membership;
  - sites on every access base, with the interesting extras combined: a public
    site with a secret link beside it, and a closed site on invitees alone;
  - real published versions plus two previews, unpacked through the normal
    ingest path from a bundle this script builds in memory.
"""

from __future__ import annotations

import asyncio
import io
import sys
import tarfile
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from plak.access import keys as access_keys
from plak.config import Settings, load_settings
from plak.constants import AccessBase, Role
from plak.db import make_engine, make_session_factory
from plak.ingest.service import Deployer, IngestService
from plak.ingest.store import ContentStore
from plak.models.identity import (
    Group,
    GroupMember,
    Member,
    MemberStatus,
    PlatformRole,
    SiteMember,
)
from plak.models.publication import Invitee, Preview, Site, Version

# The sub the mock OIDC server hands out for every dev login
# (dev/mock-oidc-config.json), and the sub dev/compose.yml bootstraps into a
# platform admin. Used as a fallback only: the configured value wins, so a
# changed PLAK_BOOTSTRAP_ADMIN_SUB still lands on the seeded member.
DEFAULT_ADMIN_SUB = "dev-beheerder"
ADMIN_EMAIL = "beheerder@plak.local"

BUNDLE_NAME = "site.tar.gz"

GROUP_PUBLIC = "rijksoverheid"
GROUP_INTERNAL = "inspectie"

PREVIEW_OPEN = "pr-42"
PREVIEW_RESTRICTED = "pr-77"


# -- The example site -------------------------------------------------------

_CSS = """:root {
  color-scheme: light dark;
  --ink: #1b1b1b;
  --paper: #fdfdfc;
  --line: #c8c8c4;
  --accent: #154273;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  padding: 0 1rem 4rem;
  font: 1rem/1.6 system-ui, -apple-system, "Segoe UI", sans-serif;
  color: var(--ink);
  background: var(--paper);
}
header, main, footer { max-width: 44rem; margin: 0 auto; }
header { padding: 3rem 0 2rem; border-bottom: 2px solid var(--accent); }
h1 { margin: 0.25rem 0; font-size: 2rem; line-height: 1.2; }
.group { margin: 0; text-transform: uppercase; letter-spacing: 0.08em;
  font-size: 0.75rem; font-weight: 700; color: var(--accent); }
.badge { display: inline-block; margin: 0.75rem 0 0; padding: 0.15rem 0.6rem;
  border: 1px solid var(--line); border-radius: 999px; font-size: 0.8rem; }
main { padding-block: 2rem; }
table { width: 100%; border-collapse: collapse; margin: 1.5rem 0; }
th, td { padding: 0.5rem 0.25rem; text-align: left; border-bottom: 1px solid var(--line); }
footer { padding-top: 2rem; border-top: 1px solid var(--line); font-size: 0.85rem; }
a { color: var(--accent); }
@media (prefers-color-scheme: dark) {
  :root { --ink: #f2f2f0; --paper: #16181c; --line: #3a3d44; --accent: #8fb8e8; }
}
"""

_PAGE = """<!doctype html>
<html lang="nl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<link rel="stylesheet" href="assets/plak.css">
</head>
<body>
<header>
<p class="group">{group}</p>
<h1>{title}</h1>
<p class="badge">{badge}</p>
</header>
<main>
{body}
</main>
<footer>
<p>Gezaaide dev-inhoud van Plak, gebouwd op {stamp}.</p>
</footer>
</body>
</html>
"""

_INDEX_BODY = """<p>{lead}</p>
<table>
<tr><th>Groep</th><td>{group}</td></tr>
<tr><th>Site</th><td>{title}</td></tr>
<tr><th>Uitgave</th><td>{badge}</td></tr>
</table>
<p><a href="verantwoording.html">Verantwoording en bronnen</a></p>
"""

_SUB_BODY = """<p>Deze pagina zit in dezelfde bundel als de startpagina en
bestaat om te laten zien dat een deeplink en een relatief pad het doen.</p>
<p><a href="index.html">Terug naar de startpagina</a></p>
"""


def _site_files(group_name: str, title: str, badge: str, lead: str) -> dict[str, bytes]:
    """The files of one example site, ready for the ContentStore."""
    stamp = datetime.now(tz=UTC).strftime("%d-%m-%Y %H:%M UTC")
    common = {"group": group_name, "title": title, "badge": badge, "stamp": stamp}
    return {
        "index.html": _PAGE.format(
            body=_INDEX_BODY.format(lead=lead, **common), **common
        ).encode(),
        "verantwoording.html": _PAGE.format(
            body=_SUB_BODY, **{**common, "title": f"Verantwoording - {title}"}
        ).encode(),
        "assets/plak.css": _CSS.encode(),
    }


def _bundle(files: dict[str, bytes]) -> bytes:
    """Packs the files into a .tar.gz, the same shape a real deploy uploads."""
    buffer = io.BytesIO()
    now = int(time.time())
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for rel_path, content in files.items():
            info = tarfile.TarInfo(rel_path)
            info.size = len(content)
            info.mtime = now
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


# -- Seeding ----------------------------------------------------------------


@dataclass(frozen=True)
class SeedResult:
    members: int
    groups: int
    sites: int
    versions: int
    previews: int
    secret_link_key: str


def _member(
    sub: str,
    email: str,
    name: str,
    *,
    admin: bool = False,
    deactivated: bool = False,
    age_days: int = 0,
    seen_days_ago: int | None = None,
) -> Member:
    """A seeded member. `age_days` and `seen_days_ago` are counted back from now,
    so the platform page has three genuinely different sort orders to show
    instead of six members created in the same second."""
    now = datetime.now(tz=UTC)
    return Member(
        id=uuid.uuid4(),
        sso_subject=sub,
        email=email,
        name=name,
        platform_role=PlatformRole.ADMIN if admin else PlatformRole.MEMBER,
        status=MemberStatus.DEACTIVATED if deactivated else MemberStatus.ACTIVE,
        created_at=now - timedelta(days=age_days),
        last_login_at=None if seen_days_ago is None else now - timedelta(days=seen_days_ago),
    )


def _site(
    group: Group,
    slug: str,
    title: str,
    base: AccessBase,
    creator: Member,
    *,
    keys: bool = False,
    invitees: bool = False,
) -> Site:
    return Site(
        id=uuid.uuid4(),
        group_id=group.id,
        slug=slug,
        title=title,
        access_base=base,
        access_keys=keys,
        access_invitees=invitees,
        created_by=creator.id,
    )


async def _wipe(session_factory: async_sessionmaker[AsyncSession], store: ContentStore) -> None:
    """Drops every group and member, and the version directories they owned."""
    async with session_factory() as db:
        storage_refs = list(await db.scalars(select(Version.storage_ref)))
        await db.execute(delete(Group))
        await db.execute(delete(Member))
        await db.commit()
    for storage_ref in storage_refs:
        store.delete_version(storage_ref)


async def _publish(
    ingest: IngestService,
    session_factory: async_sessionmaker[AsyncSession],
    store: ContentStore,
    group: Group,
    site: Site,
    files: dict[str, bytes],
    deployer: Deployer,
    ref: str | None = None,
) -> uuid.UUID:
    """Runs one bundle through the real ingest path; `ref` makes it a preview."""
    source = store.new_spool_file()
    source.write_bytes(_bundle(files))
    try:
        async with session_factory() as db:
            if ref is None:
                return await ingest.deploy(db, group, site, BUNDLE_NAME, source, deployer)
            return await ingest.preview_deploy(
                db, group, site, ref, BUNDLE_NAME, source, deployer
            )
    finally:
        source.unlink(missing_ok=True)


async def seed(settings: Settings) -> SeedResult:
    """Wipes and refills the dev database plus the content volume."""
    if settings.environment != "dev":
        raise RuntimeError(
            "seed weigert te draaien buiten PLAK_ENVIRONMENT=dev: hij wist alle groepen en leden"
        )

    engine = make_engine(settings)
    session_factory = make_session_factory(engine)
    store = ContentStore(settings.content_root)
    ingest = IngestService(store, settings)

    try:
        await _wipe(session_factory, store)

        admin_sub = settings.bootstrap_admin_sub or DEFAULT_ADMIN_SUB
        admin = _member(admin_sub, ADMIN_EMAIL, "Dev Beheerder", admin=True, age_days=240, seen_days_ago=0)
        editor = _member(
            "dev-redacteur", "s.jansen@plak.local", "Sanne Jansen", age_days=180, seen_days_ago=1
        )
        reader = _member("dev-lezer", "t.devries@plak.local", "Tim de Vries", age_days=95, seen_days_ago=12)
        site_only = _member(
            "dev-sitelid", "n.bakker@plak.local", "Noor Bakker", age_days=60, seen_days_ago=40
        )
        inspector = _member(
            "dev-inspecteur", "w.dekker@plak.local", "Wim Dekker", age_days=30, seen_days_ago=3
        )
        # Never logged in, so the platform page has a row where "last activity"
        # is empty and has to sort last in both date orders.
        deactivated_member = _member(
            "dev-ontslagen", "k.visser@plak.local", "Kim Visser", deactivated=True, age_days=2
        )
        members = [admin, editor, reader, site_only, inspector, deactivated_member]

        public_group = Group(
            id=uuid.uuid4(),
            slug=GROUP_PUBLIC,
            name="Rijksoverheid",
            default_access_base=AccessBase.PUBLIC,
        )
        internal_group = Group(
            id=uuid.uuid4(),
            slug=GROUP_INTERNAL,
            name="Inspectie Leefomgeving",
            default_access_base=AccessBase.SITE_TEAM,
        )

        # The public group carries all three roles at once, so one group page
        # shows the whole role model. The internal group has its own admin and
        # deliberately not the bootstrap member: that is what makes the
        # difference between "platform admin sees everything" and real group
        # membership visible in dev.
        group_members = [
            GroupMember(group_id=public_group.id, member_id=admin.id, role=Role.ADMIN),
            GroupMember(group_id=public_group.id, member_id=editor.id, role=Role.EDITOR),
            GroupMember(group_id=public_group.id, member_id=reader.id, role=Role.READER),
            GroupMember(group_id=internal_group.id, member_id=inspector.id, role=Role.ADMIN),
        ]

        # Every base occurs, and the extras occur both on a closed base (where
        # they are the only way in) and beside a wider one (where they add a
        # second road): that second case is the one the old single enum could
        # not express at all.
        annual = _site(public_group, "jaarverslag", "Jaarverslag 2025", AccessBase.PUBLIC, admin)
        figures = _site(
            public_group, "kerncijfers", "Kerncijfers open data", AccessBase.NOBODY, admin, keys=True
        )
        evaluation = _site(
            public_group,
            "evaluatie",
            "Evaluatie subsidieregeling",
            AccessBase.NOBODY,
            admin,
            invitees=True,
        )
        report = _site(
            internal_group,
            "toezichtrapport",
            "Rapportage toezicht 2025",
            AccessBase.SITE_TEAM,
            inspector,
            keys=True,
            invitees=True,
        )
        instructions = _site(
            internal_group, "werkinstructies", "Werkinstructies", AccessBase.SSO, inspector
        )
        sites = [annual, figures, evaluation, report, instructions]

        async with session_factory() as db:
            db.add_all(members)
            db.add_all([public_group, internal_group])
            await db.flush()
            db.add_all(group_members)
            db.add_all(sites)
            await db.flush()
            # A site role adds to the group role and never subtracts from
            # it; this member has no group role at all, so the site is the
            # only thing she can reach.
            db.add(
                SiteMember(
                    site_id=evaluation.id,
                    member_id=site_only.id,
                    role=Role.EDITOR,
                    added_by=admin.id,
                )
            )
            db.add_all(
                [
                    Invitee(site_id=evaluation.id, identifier=ADMIN_EMAIL, added_by=admin.id),
                    Invitee(
                        site_id=evaluation.id,
                        identifier="a.hendriks@example.org",
                        added_by=admin.id,
                    ),
                    # On toezichtrapport both extras sit beside a base that
                    # already lets the site team in: one reviewer from outside
                    # plus a link for whoever cannot log in at all.
                    Invitee(
                        site_id=report.id, identifier="r.dekker@example.org", added_by=inspector.id
                    ),
                ]
            )
            await db.commit()

        by_admin = Deployer(member_id=admin.id)
        by_inspector = Deployer(member_id=inspector.id)

        await _publish(
            ingest, session_factory, store, public_group, annual,
            _site_files("Rijksoverheid", "Jaarverslag 2025", "Live versie",
                  "Het jaarverslag over 2025, publiek toegankelijk zonder inloggen."),
            by_admin,
        )
        await _publish(
            ingest, session_factory, store, public_group, figures,
            _site_files("Rijksoverheid", "Kerncijfers open data", "Live versie",
                  "Deze cijfers staan achter een geheime link: alleen wie de link heeft, komt binnen."),
            by_admin,
        )
        await _publish(
            ingest, session_factory, store, public_group, evaluation,
            _site_files("Rijksoverheid", "Evaluatie subsidieregeling", "Live versie",
                  "Concept-evaluatie, alleen te lezen door de genodigden op de lijst."),
            by_admin,
        )
        await _publish(
            ingest, session_factory, store, internal_group, report,
            _site_files("Inspectie Leefomgeving", "Rapportage toezicht 2025", "Live versie",
                  "Interne rapportage, zichtbaar voor de leden van de groep."),
            by_inspector,
        )

        await _publish(
            ingest, session_factory, store, public_group, annual,
            _site_files("Rijksoverheid", "Jaarverslag 2025", f"Preview {PREVIEW_OPEN}",
                  "Voorvertoning van de tekstronde in pull request 42."),
            by_admin, ref=PREVIEW_OPEN,
        )
        await _publish(
            ingest, session_factory, store, public_group, annual,
            _site_files("Rijksoverheid", "Jaarverslag 2025", f"Preview {PREVIEW_RESTRICTED}",
                  "Voorvertoning met een eigen toegang, strenger dan de site zelf."),
            by_admin, ref=PREVIEW_RESTRICTED,
        )

        async with session_factory() as db:
            # A preview may be stricter than its site; this one shows that
            # override in the Previews tab without anyone having to set it.
            await db.execute(
                update(Preview)
                .where(Preview.site_id == annual.id, Preview.ref == PREVIEW_RESTRICTED)
                .values(
                    access_base_override=AccessBase.SITE_TEAM,
                    access_keys_override=False,
                    access_invitees_override=False,
                )
            )
            _, secret_link_key = await access_keys.create_key(
                db, figures.id, "Gedeeld met de redactieraad"
            )
            await access_keys.create_key(db, report.id, "Meelezer buiten de inspectie")
            await db.commit()

        async with session_factory() as db:
            return SeedResult(
                members=len(members),
                groups=2,
                sites=len(sites),
                versions=await db.scalar(select(func.count()).select_from(Version)),
                previews=await db.scalar(select(func.count()).select_from(Preview)),
                secret_link_key=secret_link_key,
            )
    finally:
        await engine.dispose()


# -- Command line -----------------------------------------------------------


def _report(settings: Settings, result: SeedResult) -> str:
    admin_base = (settings.base_url or "http://beheer.plak.localhost:8080").rstrip("/")
    content_base = (settings.content_base_url or admin_base).rstrip("/")
    annual = f"{content_base}/{GROUP_PUBLIC}/jaarverslag/"
    lines = [
        "",
        "Dev-inhoud gezaaid.",
        f"  {result.members} leden, {result.groups} groepen, {result.sites} sites, "
        f"{result.versions} versies, {result.previews} previews",
        "",
        "Inloggen en rondkijken:",
        f"  beheer     {admin_base}/",
        f"  live       {annual}",
        f"  preview    {annual}_preview/{PREVIEW_OPEN}/",
        f"  geheime link  {content_base}/{GROUP_PUBLIC}/kerncijfers/?key={result.secret_link_key}",
        "",
        "Publiceren vanaf je eigen machine, met de plak-CLI:",
        f"  plak login   (beheer-URL {admin_base})",
        f"  en keur de code in je terminal goed op {admin_base}/cli-link",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    settings = load_settings()
    result = asyncio.run(seed(settings))
    sys.stdout.write(_report(settings, result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
