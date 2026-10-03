"""Shared pydantic base for the wire contract (NL API Design Rules):
camelCase on the wire, snake_case internally. Thanks to `populate_by_name`
snake_case bodies from existing clients keep being accepted too.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from plak.constants import AccessBase


class ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


ACCESS_BASE_HINT = (
    "`public` (iedereen), `sso` (elke gebruiker die inlogt met SSO Rijk), `site_team` (wie een rol "
    "heeft op de site of op haar groep) of `nobody` (niemand standaard: alleen via de "
    "uitzonderingen hieronder)."
)


class AccessOut(ApiModel):
    """Wie de content mag zien: een basis plus twee uitzonderingen."""

    base: AccessBase = Field(description=f"De basis, precies één van: {ACCESS_BASE_HINT}")
    keys: bool = Field(description="Of geheime links toegang geven, ook zonder inloggen.")
    invitees: bool = Field(description="Of genodigden toegang geven na inloggen met SSO Rijk.")


__all__ = ["ACCESS_BASE_HINT", "AccessOut", "ApiModel"]
