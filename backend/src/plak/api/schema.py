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
    "`public` (everyone), `sso` (every user who signs in with SSO Rijk), `site_team` (anyone with a role "
    "on the site or on its group) or `nobody` (nobody by default: only through the "
    "exceptions below)."
)


class AccessOut(ApiModel):
    """Who may see the content: a base plus two exceptions."""

    base: AccessBase = Field(description=f"The base, exactly one of: {ACCESS_BASE_HINT}")
    keys: bool = Field(description="Whether secret links grant access, even without signing in.")
    invitees: bool = Field(description="Whether invitees get access after signing in with SSO Rijk.")


__all__ = ["ACCESS_BASE_HINT", "AccessOut", "ApiModel"]
