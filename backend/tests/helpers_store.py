"""Writing a version straight into a ContentStore, for test setup."""

from __future__ import annotations

import uuid

from plak.ingest.store import ContentStore


def store_version(
    store: ContentStore, group: str, site: str, version_id: uuid.UUID, files: dict[str, bytes]
) -> str:
    """Writes `files` as a version through `write_version` and returns its storage ref."""
    with store.write_version(group, site, version_id) as writer:
        for rel_path, content in files.items():
            with writer.open_file(rel_path) as out:
                out.write(content)
    return writer.storage_ref
