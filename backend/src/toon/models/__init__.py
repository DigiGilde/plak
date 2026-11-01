"""Registers every SQLAlchemy model on the shared metadata (Base)."""

from toon.models import audit, ci, cli, identity, publication, slugs  # noqa: F401
from toon.models.base import Base

__all__ = ["Base"]
