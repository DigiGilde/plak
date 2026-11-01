"""Who runs this installation: `GET /-/api/v1/operator`.

Public, for the privacy and accessibility pages of the admin SPA that anyone
may read: no session, no CSRF header and no cookie. The values come from
TOON_OPERATOR_NAME and TOON_OPERATOR_EMAIL (config.py), which validates the
address at startup.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import ConfigDict, Field

from toon.api.docs import TAG_INSTALLATION
from toon.api.errors import error_responses
from toon.api.schema import ApiModel

router = APIRouter(prefix="/-/api/v1")

_NO_AUTH = {"security": [{}]}


class OperatorOut(ApiModel):
    """Who runs this installation of Plak, and how to reach them."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"name": "Digi Gilde", "email": "digigilde@rijksoverheid.nl"},
                {"name": None, "email": None},
            ]
        }
    )

    name: str | None = Field(
        description=(
            "Name of the organisation that runs this installation, as people should read it "
            "(`TOON_OPERATOR_NAME`), or `null` if it is not configured."
        ),
        examples=["Digi Gilde"],
    )
    email: str | None = Field(
        description=(
            "E-mail address at which people reach that organisation about privacy, accessibility and other "
            "questions (`TOON_OPERATOR_EMAIL`), or `null` if it is not configured. Always a plain address "
            "without a display name, fit for a `mailto:` link."
        ),
        examples=["digigilde@rijksoverheid.nl"],
    )


@router.get(
    "/operator",
    tags=[TAG_INSTALLATION],
    openapi_extra=_NO_AUTH,
    summary="Who runs this installation",
    response_description="The name and e-mail address of the organisation, each `null` if not configured.",
    description=(
        "The organisation that runs this installation of Plak and the e-mail address at which people reach "
        "it about privacy, accessibility and other questions, as set in `TOON_OPERATOR_NAME` and "
        "`TOON_OPERATOR_EMAIL`. The public privacy and accessibility pages of the admin interface show them. "
        "A setting that is empty comes back as `null`.\n\n"
        "**Who can call this:** anyone, without authentication. It needs no session and no CSRF header, and "
        "sets no cookie."
    ),
    responses=error_responses(
        {
            403: "The request comes from an origin other than the admin host (`ORIGIN_REFUSED`).",
            429: "Too many requests from this address (`TOO_MANY_REQUESTS`). Try again later.",
        }
    ),
)
async def operator_contact(request: Request) -> OperatorOut:
    settings = request.app.state.settings
    return OperatorOut(name=settings.operator_name or None, email=settings.operator_email or None)


__all__ = ["OperatorOut", "router"]
