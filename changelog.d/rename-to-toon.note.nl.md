## Plak heet nu Toon

Plak heeft een nieuwe naam: Toon. De naam komt van tonen: laten zien. Toon laat je pagina of site zien aan wie die mag zien. Dat kan iedereen zijn, of alleen een kleine groep.

- De adressen zijn `toon.rijks.app` en `beheer.toon.rijks.app`. De oude sturen door, dus links blijven werken. Kwam een bezoeker binnen met een geheime link? Die opent de volledige link één keer opnieuw.
- Log één keer opnieuw in.
- Staat het oude adres in je site, bijvoorbeeld in `canonical`, `og:url` of een vast pad? Publiceer de site dan opnieuw met `https://toon.rijks.app`. Stijlen en scripts laden niet via het oude adres.
- Publiceer je met de CLI? Het commando is nu `toon`. Verwijder het oude met `uv tool uninstall plak` en installeer het nieuwe met `uv tool install "git+https://github.com/DigiGilde/toon@beta#subdirectory=cli"`. "Gekoppelde CLI-sessies" is leeg, dus voer daarna `toon login` uit op elke computer waarop je de CLI gebruikt.
- Publiceer je vanuit een workflow? Wijzig dan twee regels in elke stap die de action gebruikt. `uses:` wordt `DigiGilde/toon/actions/publish@<commit-sha>`, met de commit van een release onder de naam Toon. `host:` wordt `https://beheer.toon.rijks.app`. Het tabblad "Deploy" van een site laat de nieuwe workflow zien.
- Gebruik je de plugin in Claude Code? Voer dan `/plugin marketplace remove plak`, `/plugin marketplace add DigiGilde/toon` en `/plugin install toon@toon` uit.

Meer uitleg staat (in het Engels) in de sectie [Moving over from Plak](https://github.com/DigiGilde/toon/blob/beta/docs/publishing.md#9-moving-over-from-plak).
