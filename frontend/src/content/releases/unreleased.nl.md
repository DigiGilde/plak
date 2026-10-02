## Wijzigingen per versie

Plak heeft nu een pagina "Wat is er nieuw", bereikbaar via de link onderaan elke pagina. Daar lees je per versie wat er veranderd is.

Onderaan de pagina staat ook de versie van Plak waar je nu mee werkt.

## Versie van de CLI

Met `plak --version` zie je welke versie van de Plak CLI je gebruikt. Spreekt de server een nieuwere versie van de API dan de CLI kent, dan stopt de CLI en zegt hij hoe je bijwerkt.

## Previews bij de pull request

De publish-action kan een preview nu op GitHub bij de pull request laten zien: als deployment met een knop "View deployment", als reactie met de link, of allebei. Pull requests van bots, zoals Dependabot, slaat hij standaard over. Voor de DigiGilde-omgeving hoef je `host` niet meer op te geven.

De action heet nu `actions/publish` in plaats van `actions/publiceer`. Werk je de vastgezette commit in je workflow bij, pas dan ook dat pad aan.
