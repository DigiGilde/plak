"""Registers every SQLAlchemy model on the shared metadata (Base)."""

from plak.models import audit, ci, cli, identity, publication, slugs  # noqa: F401
from plak.models.base import Base

__all__ = ["Base"]
