# Changelog

Alle noemenswaardige wijzigingen aan DataVault Dashboard.

## Fase 2 – Stabiliseren

### Veiligheid van je bestanden
- Elk verplaatsen van een bestand (Verplaatsen in de viewer, Keurer weggooien en verplaatsen,
  duplicaten opruimen en terugzetten) gaat nu via één gedeelde, veilige verplaatsfunctie.
  Een bestaand bestand wordt nooit meer overschreven: bij een naambotsing krijgt het
  verplaatste bestand een vrije naam als `foto_DUP_001.jpg`. Ook drie bestanden met dezelfde
  naam die binnen één seconde naar dezelfde map gaan, blijven alle drie bestaan.
- Verplaatsen bewaart alle metadata (wijzigings- en aanmaakdatum, EXIF, extended attributes)
  en vergrendelde bestanden (Finder-slotje) blijven vergrendeld.
- Verplaatsen naar een andere schijf wordt geweigerd met een duidelijke melding, in plaats van
  stilletjes te kopiëren en het origineel te wissen.
- Mappen en snelkoppelingen (symlinks) worden nooit als bestand verplaatst.
- Lukt verplaatsen in de viewer niet, dan zie je nu waarom (bijv. "staat al in deze map")
  in plaats van alleen "Failed to move file".
- Ongeldige verzoeken (bijv. een doelmap "onder" een bestand, of een getal waar een pad
  hoort) geven nu een nette foutmelding in plaats van een serverfout. Er werd en wordt in die
  gevallen niets verplaatst.
- De beschermde mappen (cache met de database, prullenmand, RAW_DUPLICATES) zijn nu ook
  beschermd als een pad anders geschreven wordt, bijvoorbeeld `.DATAVAULT_CACHE` in plaats van
  `.datavault_cache`. Op een gewone (hoofdletterongevoelige) Mac-schijf is dat dezelfde map, en
  voorheen kon één verzoek zo de database van de app naar de prullenmand verplaatsen. De
  verplaatsfunctie zelf weigert nu ook alles in de cache-map of de database.
- Alle verplaatsingen in de app (viewer, Keurer, duplicaten en ongedaan maken) wachten nu op
  elkaar. Twee gelijktijdige verzoeken (bijv. twee tabbladen) kunnen daardoor niet allebei
  "de andere kopie" van een foto wegzetten: de controle dat het origineel er nog is en het
  verplaatsen gebeuren in één ononderbroken stap.

### Foto Keurer
- Ongedaan maken gebeurt nu volledig op de server: de app onthoudt per beslissing waar het
  bestand naartoe ging (prullenmand of doelmap). Undo stuurt alleen nog het nummer van de
  beslissing, controleert opnieuw beide paden en weigert als er inmiddels een ander bestand
  op de oorspronkelijke plek staat. Er wordt nooit iets overschreven.
- De undo-stapel (laatste 20 acties) overleeft het herladen van de pagina.
- Undo werkt ook na het wisselen van map: de beslissing wordt in haar eigen map teruggedraaid.
- Teller, dagdoel en streak komen na elke beslissing en undo rechtstreeks van de server en
  lopen dus niet meer uit de pas.
- Mislukt een beslissing of undo, dan zie je een foutmelding en blijft de kaart staan, in
  plaats van een valse "gelukt". Een bestand dat intussen verdwenen is, wordt overgeslagen
  met een melding.
- Verplaatsen in de Keurer: de mappenlijst in het verplaatsvenster verscheen niet meer; dat
  is opgelost. Verplaatsen telt (zoals voorheen) niet mee voor het dagdoel.
- Database-migratie 2 voegt de kolom voor het doelpad toe; bestaande beslissingen, streaks
  en ratings blijven behouden. Oude weggooi-beslissingen van vóór deze versie kunnen niet
  automatisch worden teruggezet (het pad werd toen niet bewaard).
- Undo controleert nu ook dat het bestand in de prullenmand of doelmap echt hetzelfde bestand
  is dat de Keurer daar neerzette (en niet een andere foto met dezelfde naam, bijvoorbeeld
  nadat je de prullenmand in Finder hebt opgeruimd). Is het een ander bestand, dan wordt er
  niets teruggezet en zie je een melding. Database-migratie 3 bewaart hiervoor per beslissing
  een kenmerk van het verplaatste bestand; bestaande beslissingen, streaks en ratings blijven
  behouden.
- Komt er later een nieuw bestand op de plek van een in de Keurer verplaatst bestand en beoordeel
  je dat, dan blijft het eerdere verplaatsen gewoon ongedaan te maken. Voorheen verdween die
  undo stilletjes. Database-migratie 4 herbouwt hiervoor de beslissingentabel; alle
  beslissingen blijven met hun eigen nummer bewaard en vooraf wordt een back-up gemaakt.

### Opgeruimd
- Verwijderd: de kapotte vault-brede duplicatenscan (`/api/raw_duplicates/scan` en
  `/api/raw_duplicates/delete`) met de bijbehorende scannercode, het ongebruikte
  `/api/delete-to-trash` (verplaatste zonder bescherming tegen overschrijven),
  `/api/main-folders` en de losse `dateFixer`-scripts. De duplicatenscan per map, de
  voortgang, annuleren en "laatste ongedaan maken" blijven gewoon werken.

### Duplicaten
- "Duplicaat verwijderen" en "Alles verwijderen" controleren vlak voor het verplaatsen of het
  bewaarde origineel nog bestaat en byte voor byte identiek is. Is het origineel intussen
  verplaatst of gewijzigd, dan wordt er niets verplaatst en krijg je een melding om opnieuw
  te scannen. Zo kan de laatste kopie van een foto nooit meer weggezet worden.
- Alleen gewone bestanden worden verplaatst: mappen, snelkoppelingen en bestanden in
  RAW_DUPLICATES, TRASH of de cache worden geweigerd. (Voorheen kon één verzoek de hele
  RAW-map verplaatsen.)
- Een geweigerd verzoek laat geen lege "verwijder-run" meer achter in de undo-geschiedenis.
- "Laatste ongedaan maken" voor duplicaten liep vast als de bestanden van de laatste actie niet
  meer in RAW_DUPLICATES stonden (bijv. handmatig opgeruimd): oudere acties waren dan nooit meer
  terug te zetten. Zo'n actie wordt nu overgeslagen (en blijft bewaard, voor als de bestanden
  terugkomen). Bestanden die niet teruggezet konden worden, blijven geregistreerd voor een
  volgende poging in plaats van uit de geschiedenis te verdwijnen.
