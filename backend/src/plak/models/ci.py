"""CI trust model: the one repository a site accepts OIDC-authenticated
deploys from (trusted publishing)."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from plak.models.base import Base, IDMixin


class CiProvider(enum.StrEnum):
    GITHUB = "github"
    FORGEJO = "forgejo"


class SiteRepository(IDMixin, Base):
    """`repository_id` and `owner_id` are the provider's numeric ids, resolved
    through its REST API when the link is made: they survive a rename or a
    transfer, which is what makes them the thing to trust rather than the
    name.
    """

    __tablename__ = "site_repositories"

    site_id: Mapped[uuid.UUID] = mapped_column(
        postgresql.UUID(as_uuid=True), ForeignKey("sites.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    provider: Mapped[CiProvider] = mapped_column(
        SAEnum(CiProvider, name="ci_provider", values_callable=lambda c: [member.value for member in c]),
        nullable=False,
    )
    host: Mapped[str] = mapped_column(String, nullable=False)
    owner: Mapped[str] = mapped_column(String, nullable=False)
    repo: Mapped[str] = mapped_column(String, nullable=False)
    repository_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    owner_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    live_branch: Mapped[str | None] = mapped_column(String, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True), ForeignKey("members.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
