"""Interface language for the pages the server renders itself.

The beheer SPA carries its own catalogue; this one covers what a browser gets
before any JavaScript runs: the public front page, and the screens a visitor
meets while logging in or while being refused.

There is no session to read a preference from on the content host, and the
front page is public, so the language comes from `Accept-Language` alone. A
member who set a language in the beheer environment keeps that setting there;
the two are deliberately separate, because a page without a session cannot
know about it.

Dutch is the reference: `EN` is typed against `NL`, so a key that is missing
from one of them is a type error rather than a raw key on screen. Which of the
two a visitor without a usable `Accept-Language` gets is a separate question;
see DEFAULT and FOREIGN below.
"""

from __future__ import annotations

from typing import Final

Locale = str  # "nl" or "en"

SUPPORTED: Final[tuple[str, ...]] = ("nl", "en")

# Two different fallbacks, because "no signal" and "a signal we cannot follow"
# are not the same question.
#
# Nothing asked for (a crawler, curl, a browser that sends no header): keep the
# language of the service. Plak is a Dutch government platform, the front page
# is what an index stores, and there is nothing here that says otherwise.
#
# Asked for a language we do not have (fr, de, pl): that visitor has told us
# they do not read Dutch. English is the wider of the two we do have, so it is
# the better guess than the one language we already know they did not ask for.
DEFAULT: Final[str] = "nl"
FOREIGN: Final[str] = "en"

# The API answers a client rather than a visitor: a CLI, a CI workflow, a
# script. "Nothing asked for" is then a caller that never considered language
# at all, and English is what such a caller is likeliest to read, so the API
# hands this to `negotiate` as its `default`. The negotiation itself is the
# same one: whoever asks for Dutch gets Dutch, on the front page and in
# problem+json alike.
API_DEFAULT: Final[str] = "en"

NL: Final[dict[str, str]] = {
    "page.title": "Plak",
    "front.intro": (
        "Snel en eenvoudig een HTML-pagina delen. Een rapport, analyse of "
        "overzicht, zelf gemaakt of met een AI-assistent: zet het op Plak en "
        "deel de link. Jij bepaalt wie het mag zien."
    ),
    "front.lead": "Log in met je Rijksoverheid-account om te beginnen.",
    "front.login": "Inloggen",
    "front.steps.heading": "Zo deel je een pagina",
    "front.steps.1": (
        "Zet je HTML-bestand op Plak door het te uploaden, of laat je AI-assistent "
        "dit doen."
    ),
    "front.steps.2": (
        "Kies wie het mag zien: iedereen, collega's die inloggen, alleen wie je "
        "uitnodigt, of wie de geheime link heeft."
    ),
    "front.steps.3": "Deel de link.",
    "front.steps.4": (
        "Wil je iets wijzigen, dan upload je een nieuwe versie. Je collega's zien "
        "altijd de laatste, en oude versies blijven beschikbaar."
    ),
    "front.site.heading": "Ook voor een hele site",
    "front.site.body": (
        "Werk je aan een site met meerdere pagina's in een repository? Publiceer "
        "dan vanuit GitHub of code.overheid.nl, met een preview per pull request."
    ),
    "front.name.heading": "Waar de naam vandaan komt",
    "front.name.story": (
        "De naam komt van plakkaat. Vroeger werden plakkaten opgehangen om iets "
        "met meer mensen te delen dan je zelf kon bereiken. Dat is wat Plak ook "
        "doet. En net als een plakkaat kan het in de openbare ruimte hangen, "
        "zichtbaar voor iedereen, of op een afgesloten plek waar alleen een "
        "kleinere groep komt."
    ),
    # One line, because it goes in a status bar that shows one line and cuts
    # the rest off with an ellipsis. The first word carries the message, so it
    # survives the truncation on a narrow screen.
    "beta.bar": "Bètaversie - Plak is in ontwikkeling en kan fouten bevatten",
    "footer.label": "Over deze dienst",
    "footer.whatsNew": "Wat is er nieuw",
    "footer.version": "Versie {version}",
    "footer.about": "Over Plak",
    "footer.accessibility": "Toegankelijkheid",
    "footer.privacy": "Privacy",
    "footer.api": "API-documentatie",
    "login.failed": "Inloggen is mislukt. Probeer het opnieuw.",
    # The page that asks for the code of a secret link shared without it
    # (serving/code_page.py). It names neither the site nor the group: it is
    # served on a secret address, and whoever lands there without the code
    # should learn nothing beyond that a code is needed.
    "code.title": "Code nodig",
    "code.heading": "Vul de code in",
    "code.intro": (
        "Deze pagina is gedeeld met een link zonder code. Vul de code in die je "
        "apart hebt gekregen."
    ),
    "code.label": "Code",
    "code.submit": "Bekijk de pagina",
    "code.wrong": "De code klopt niet. Controleer de code die je apart hebt gekregen.",
    "code.later": "Probeer het later opnieuw.",
}

EN: Final[dict[str, str]] = {
    "page.title": "Plak",
    "front.intro": (
        "Share an HTML page quickly and easily. A report, analysis or overview, "
        "made yourself or with an AI assistant: put it on Plak and share the "
        "link. You decide who gets to see it."
    ),
    "front.lead": "Sign in with your Dutch central government account to get started.",
    "front.login": "Sign in",
    "front.steps.heading": "How to share a page",
    "front.steps.1": (
        "Put your HTML file on Plak by uploading it, or have your AI assistant do "
        "it for you."
    ),
    "front.steps.2": (
        "Choose who gets to see it: everyone, colleagues who sign in, only "
        "people you invite, or whoever has the secret link."
    ),
    "front.steps.3": "Share the link.",
    "front.steps.4": (
        "To change something, upload a new version. Your colleagues always see the "
        "latest one, and older versions stay available."
    ),
    "front.site.heading": "A whole site too",
    "front.site.body": (
        "Working on a site with multiple pages in a repository? Publish it from "
        "GitHub or code.overheid.nl instead, with a preview for every pull "
        "request."
    ),
    "front.name.heading": "Where the name comes from",
    "front.name.story": (
        "The name comes from plakkaat, the Dutch word for a placard. Placards used "
        "to be put up to share something with more people than you could reach "
        "yourself. That is what Plak does. And like a placard it can hang in the "
        "open, visible to everyone, or in a closed room only a smaller group "
        "enters."
    ),
    "beta.bar": "Beta - Plak is under development and may contain errors",
    "footer.label": "About this service",
    "footer.whatsNew": "What's new",
    "footer.version": "Version {version}",
    "footer.about": "About Plak",
    "footer.accessibility": "Accessibility",
    "footer.privacy": "Privacy",
    "footer.api": "API documentation",
    "login.failed": "Signing in failed. Please try again.",
    "code.title": "Code required",
    "code.heading": "Enter the code",
    "code.intro": (
        "This page was shared with a link without a code. Enter the code you "
        "were given separately."
    ),
    "code.label": "Code",
    "code.submit": "View the page",
    "code.wrong": "That code is not right. Check the code you were given separately.",
    "code.later": "Please try again later.",
}

_CATALOGUES: Final[dict[str, dict[str, str]]] = {"nl": NL, "en": EN}

_MISSING = set(NL) ^ set(EN)
if _MISSING:  # pragma: no cover - a guard that only fires while editing
    raise RuntimeError(f"catalogues disagree on: {sorted(_MISSING)}")


def _weighted(accept_language: str) -> list[tuple[float, int, str]]:
    """Every element as (quality, position, base), unusable ones dropped.

    The position travels along so a sort on quality alone keeps the order the
    client wrote equal-weight tags in, which is what RFC 9110 means by their
    relative order.
    """
    elements: list[tuple[float, int, str]] = []
    for position, part in enumerate(accept_language.split(",")):
        tag, _, parameters = part.strip().partition(";")
        base = tag.strip().lower().split("-")[0]
        if not base:
            continue
        quality = 1.0
        for parameter in parameters.split(";"):
            name, _, value = parameter.partition("=")
            if name.strip().lower() != "q":
                continue
            try:
                quality = float(value.strip())
            except ValueError:
                # An unreadable weight is not a reason to drop the whole
                # header, but it is a reason not to trust this element.
                quality = 0.0
            break
        elements.append((min(1.0, max(0.0, quality)), position, base))
    return elements


def negotiate(accept_language: str | None, *, default: str = DEFAULT) -> str:
    """The language to render in, from an `Accept-Language` header.

    Weights are honoured: `nl;q=0.1, en;q=0.9` is a request for English, and
    `q=0` means a language is explicitly not acceptable rather than merely
    less wanted. Browsers write their list in preference order with descending
    weights, so this only shows up with a client that writes its own header.

    What it also weighs is whether anything was asked at all: see DEFAULT and
    FOREIGN above. A lone `*` is not a request for a language, it is "whatever
    you have", so it counts as nothing asked. `default` is what that case
    yields; the API passes API_DEFAULT there, the pages take DEFAULT.

    A refused language (q=0) is never returned while a supported, unrefused
    catalogue still exists, even when that refused language is `default`
    itself (the API's `default` is equal to FOREIGN). Only when every
    supported language is refused does this fall back to `default` regardless.

    A header that cannot be parsed has to fall back rather than fail.
    """
    if not accept_language:
        return default
    elements = _weighted(accept_language)
    elements.sort(key=lambda element: (-element[0], element[1]))

    refused = {base for quality, _, base in elements if quality == 0.0}
    asked = False
    for quality, _, base in elements:
        if quality == 0.0:
            continue
        if base in _CATALOGUES:
            return base
        if base != "*":
            asked = True

    # Turning down a language we have is itself a request for another one.
    if refused & set(_CATALOGUES):
        asked = True
    if not asked:
        return default
    if FOREIGN not in refused:
        return FOREIGN
    remaining = [language for language in SUPPORTED if language not in refused]
    return remaining[0] if remaining else default


def t(locale: str, key: str) -> str:
    """One message. An unknown locale falls back to Dutch rather than raising:
    a page that renders in the wrong language still renders."""
    return _CATALOGUES.get(locale, NL)[key]


__all__ = ["API_DEFAULT", "DEFAULT", "EN", "FOREIGN", "NL", "SUPPORTED", "negotiate", "t"]
