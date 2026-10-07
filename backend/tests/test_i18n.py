"""Language negotiation for the pages the server renders itself (plak/i18n.py)."""

from __future__ import annotations

from plak import i18n


class TestNegotiate:
    def test_a_visitor_who_asks_for_nothing_gets_the_language_of_the_service(self) -> None:
        """No header is no signal, and Plak is a Dutch service. A lone `*` is
        not a request for a language either."""
        assert i18n.negotiate(None) == "nl"
        assert i18n.negotiate("") == "nl"
        assert i18n.negotiate("*") == "nl"
        assert i18n.negotiate(" ; q=0.5") == "nl"

    def test_the_weight_decides_which_language_wins(self) -> None:
        """Browsers write their list in preference order with descending
        weights, so this only shows up with a client that writes its own
        header. It was answering with Dutch there."""
        assert i18n.negotiate("nl;q=0.1, en;q=0.9") == "en"
        assert i18n.negotiate("en;q=0.1, nl;q=0.9") == "nl"
        # Equal weights keep the order they were written in.
        assert i18n.negotiate("en;q=0.5, nl;q=0.5") == "en"
        assert i18n.negotiate("nl, en") == "nl"

    def test_a_weight_of_zero_means_not_acceptable(self) -> None:
        """q=0 is a refusal, not a low preference. Dutch turned down and
        nothing else we have on the list leaves English."""
        assert i18n.negotiate("nl;q=0, fr") == "en"
        assert i18n.negotiate("nl;q=0") == "en"
        # English turned down leaves the language of the service.
        assert i18n.negotiate("en;q=0, fr") == "nl"
        # Both turned down: nothing is acceptable, so the service speaks its own.
        assert i18n.negotiate("nl;q=0, en;q=0") == "nl"
        # Refusing a language we do not have says nothing about ours.
        assert i18n.negotiate("fr;q=0") == "nl"

    def test_an_unreadable_header_falls_back_rather_than_fails(self) -> None:
        assert i18n.negotiate("nl;q=abc, en") == "en"
        assert i18n.negotiate(";;;") == "nl"
        assert i18n.negotiate("nl;q=9") == "nl"
        assert i18n.negotiate(",,nl,,") == "nl"

    def test_a_language_we_do_not_have_falls_back_to_english_not_dutch(self) -> None:
        """Someone asking for French has told us they do not read Dutch. Then
        the wider of the two languages we do have is the better guess than the
        one we already know they did not ask for."""
        assert i18n.negotiate("de-DE,de;q=0.9") == "en"
        assert i18n.negotiate("fr") == "en"
        assert i18n.negotiate("pl-PL,pl;q=0.9,ru;q=0.8") == "en"

        # But only when there is nothing we do have anywhere in the list.
        assert i18n.negotiate("fr-FR,fr;q=0.9,nl;q=0.5") == "nl"
        assert i18n.negotiate("de,en-GB;q=0.8") == "en"
