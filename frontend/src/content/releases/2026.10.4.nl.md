## Wijzigingen per versie

Plak heeft nu een pagina "Wat is er nieuw", bereikbaar via de link onderaan elke pagina. Daar lees je per versie wat er veranderd is. Onderaan de pagina staat ook de versie van Plak waar je nu mee werkt.

## De CLI

Met `plak --version` zie je welke versie van de Plak CLI je gebruikt. Spreekt de server een nieuwere versie van de API dan de CLI kent, dan stopt de CLI en zegt hij hoe je bijwerkt.

De CLI werkt nu ook op Windows; `plak login` bewaart je sessie in Windows Referentiebeheer. `plak publish` sluit af met de URL en de versie die je net publiceerde. Vindt `plak site link` je repository niet, dan zegt de CLI waarom en wat je kunt doen.

## Previews bij de pull request

De publish-action kan een preview nu op GitHub bij de pull request laten zien: als deployment met een knop "View deployment", als reactie met de link, of allebei. De reactie zegt wie de preview mag zien en of je daarvoor moet inloggen. Pull requests van bots, zoals Dependabot, slaat hij standaard over. Na het publiceren tonen de log en de samenvatting van de job de URL en de versie.

De action heet nu `actions/publish` in plaats van `actions/publiceer`, en voor de DigiGilde-omgeving hoef je `host` niet meer op te geven. Werk je de vastgezette commit in je workflow bij, pas dan ook het pad aan.

## Oudere versies worden opgeruimd

Standaard bewaart Plak de huidige live-versie van een site en de vijf versies daarvoor; een sitebeheerder kan per site een ander aantal instellen. Oudere live-versies worden elke nacht opgeruimd, zodat een site die vaak publiceert niet meer vastloopt op de beschikbare ruimte. Het tabblad "Versies" laat zien hoeveel ruimte de site gebruikt van wat hij mag gebruiken en hoeveel versies bewaard blijven; daar stelt een sitebeheerder ook het aantal in.

## Onbevestigde repository

Heb je een privérepository gekoppeld door het repository-id en het eigenaar-id zelf in te vullen, dan staat er op het tabblad "Deploy" "Nog niet bevestigd". De eerste publicatie vanuit die repository bevestigt de ids en zet de naam goed. Wordt een repository hernoemd, dan neemt Plak bij de volgende publicatie de nieuwe naam over.

## Contentvolume

Als platformbeheerder zie je op de pagina "Platformbeheer" nu hoe vol het contentvolume is: in gebruik, vrij en de reserve. Het wordt rood zodra een deploy van de maximale omvang er niet meer bij past.

## API-documentatie in twee talen

De API-documentatie, te vinden onderaan elke pagina, is er nu in het Engels en in het Nederlands. Ben je ingelogd, dan volgt ze de taal uit je profiel; met de wissel bovenaan de pagina kies je de andere taal.
