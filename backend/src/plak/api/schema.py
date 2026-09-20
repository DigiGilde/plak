"""Shared pydantic base for the wire contract (NL API Design Rules):
camelCase on the wire, snake_case internally. Thanks to `populate_by_name`
snake_case bodies from existing clients keep being accepted too.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


__all__ = ["ApiModel"]
