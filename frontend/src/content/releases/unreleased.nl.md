## Eén startpagina

Wie het adres van Plak zelf intypt, komt nu op dezelfde startpagina als in het beheer. Daar staat ook waar de naam vandaan komt, en zonder account kies je onderaan tussen Nederlands en English.

## Service workers en de parameter `key`

Een gepubliceerde site kan geen service worker meer registreren, dus een PWA-plugin maakt een site niet meer offline bruikbaar. Staat er in het adres van een pagina een `key` die op een geheime link lijkt, dan haalt Plak die weg voordat de pagina laadt. Gebruikt je site zelf een parameter `key`? Geef die dan een andere naam.

## Een workflow noemt het site-ID

Een workflow die vanuit GitHub of Forgejo publiceert, noemt nu het ID van zijn site: `site-id` bij de action, `--site-id` bij de CLI. Zo komt de inhoud nooit bij een andere site terecht, ook niet door een tikfout in het adres of een site die is verwijderd en opnieuw aangemaakt. Het ID staat op het tabblad "Deploy", met een knop om het te kopiëren, en in de workflow die daar klaar staat. Gebruikt je workflow een oudere versie van de action of de CLI? Werk die dan eerst bij, want die kent `site-id` nog niet.

Een nieuwe koppeling vraagt het site-ID meteen. Bestaande koppelingen blijven werken zonder site-ID, behalve als dezelfde repository aan meer dan één site gekoppeld is: dan vraagt elke koppeling van die repository meteen het site-ID. Een sitebeheerder kan op het tabblad "Deploy" kiezen voor "Alleen met site-ID publiceren". Dat kan niet worden teruggedraaid.

## Toegang zonder herhaling

Onder de keuze voor toegang staat niet meer nog eens wat je net hebt gekozen. Kan niemand de site bekijken, dan zegt een waarschuwing dat. Bij de standaardtoegang van een groep staan geheime links en genodigden nu onder "Uitzonderingen", net als op het tabblad "Toegang" van een site.
