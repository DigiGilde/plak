"""0003_storage_by_site_id: version directories keyed by site id.

Moves every version directory on the content volume from
{group}/{site}/{version_id} to {site_id}/{version_id} and rewrites
versions.storage_ref to match, so no slug is part of a path. It runs where the
volume is mounted (the entrypoint on ZAD, the migrate service in dev/) and
needs PLAK_CONTENT_ROOT only when there are versions to move.

The renames are not part of the transaction. A run that stops halfway leaves
the rows as they were and some directories at their new place; the next run
counts a directory it finds there as moved. Run it again rather than going
back to the previous image, which knows only the old places.

What is left under a group's directory afterwards has no row (a publish or a
cleanup that stopped halfway) and goes to `_reclaimed/`, as the nightly
cleanup would; a top-level directory that is no known group stays, with a
warning, since it may not be Plak's.

Revision ID: 0003_storage_by_site_id
Revises: 0002_retention_and_repo_ids
Create Date: 2026-10-03
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa

from alembic import op

revision = "0003_storage_by_site_id"
down_revision = "0002_retention_and_repo_ids"
branch_labels = None
depends_on = None

CONTENT_ROOT_VAR = "PLAK_CONTENT_ROOT"
OWN_DIRECTORIES = frozenset({"_tmp", "_reclaimed"})

_logger = logging.getLogger("alembic.plak")


def _remove_empty_parents(directory: Path, root: Path) -> None:
    while directory != root:
        try:
            directory.rmdir()
        except OSError:
            return
        directory = directory.parent


def _is_id(name: str) -> bool:
    try:
        return str(uuid.UUID(name)) == name
    except ValueError:
        return False


def _reclaim_group_leftovers(root: Path) -> None:
    groups = set(op.get_bind().scalars(sa.text("SELECT slug FROM groups")))
    stamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%S")
    for entry in sorted(root.iterdir()):
        if entry.name in OWN_DIRECTORIES or _is_id(entry.name):
            continue
        if entry.name in groups and entry.is_dir() and not entry.is_symlink():
            target = root / "_reclaimed" / f"{stamp}-{entry.name}"
            target.parent.mkdir(exist_ok=True)
            os.rename(entry, target)
            os.utime(target)
            _logger.warning("Restanten zonder rij van groep %s naar %s verplaatst", entry.name, target)
        else:
            _logger.warning("%s is geen groep of site van Plak en blijft staan", entry)


def _move(moves: list[tuple[object, str, str]]) -> Path | None:
    """Moves each (version id, old ref, new ref) and rewrites its row; returns
    the content root when there was something to move."""
    moves = [move for move in moves if move[1] != move[2]]
    if not moves:
        return None
    value = os.environ.get(CONTENT_ROOT_VAR)
    if not value:
        raise RuntimeError(f"{CONTENT_ROOT_VAR} is niet gezet; de versiemappen kunnen niet mee verhuizen.")
    root = Path(value)
    # A volume that is not mounted has none of them: refuse before anything
    # moves, or every row would point at a path the real volume lacks.
    if not any((root / old).is_dir() or (root / new).is_dir() for _, old, new in moves):
        raise RuntimeError(f"Geen enkele versiemap gevonden onder {root}: is het contentvolume aangekoppeld?")
    bind = op.get_bind()
    for version_id, old, new in moves:
        source, target = root / old, root / new
        if source.is_dir():
            target.parent.mkdir(parents=True, exist_ok=True)
            os.rename(source, target)
            _remove_empty_parents(source.parent, root)
        elif not target.is_dir():
            _logger.warning("Versiemap %s ontbreekt; storage_ref wordt toch %s", old, new)
        bind.execute(
            sa.text("UPDATE versions SET storage_ref = :ref WHERE id = :id"), {"ref": new, "id": version_id}
        )
    return root


def upgrade() -> None:
    rows = op.get_bind().execute(sa.text("SELECT id, site_id, storage_ref FROM versions")).all()
    root = _move([(row.id, row.storage_ref, f"{row.site_id}/{row.id}") for row in rows])
    if root is not None:
        _reclaim_group_leftovers(root)


def downgrade() -> None:
    rows = op.get_bind().execute(
        sa.text(
            "SELECT v.id, v.storage_ref, g.slug AS group_slug, s.slug AS site_slug FROM versions v "
            "JOIN sites s ON s.id = v.site_id JOIN groups g ON g.id = s.group_id"
        )
    ).all()
    _move([(row.id, row.storage_ref, f"{row.group_slug}/{row.site_slug}/{row.id}") for row in rows])
