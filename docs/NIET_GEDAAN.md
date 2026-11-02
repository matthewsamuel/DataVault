# Bewust niet gedaan

Een eerlijke lijst van wat er in fase 2 en 3 met opzet is blijven liggen, met de reden. Kleine
interne zaken (commitgeschiedenis, tijdelijke mappen, losse meetpunten) staan er niet in. Een
**volgende stap** is iets dat de moeite waard is om als eerste op te pakken.

Terug naar de [README](../README.md) of het [CHANGELOG](../CHANGELOG.md).

## Veiligheid en bestanden

- **Oude Keurer-beslissingen ("weg") van vóór de migratie zijn niet ongedaan te maken.** Er is nooit
  opgeslagen waar het bestand heen ging en de oude prullenmapnamen zijn dubbelzinnig. Raden zou het
  verkeerde bestand kunnen terugzetten, dus de app meldt het en doet niets.
- **Opgehaalde Spotify-gegevens worden niet gewist bij "Vergeet dit account".** Alleen de koppeling verdwijnt; de rijen
  worden gemarkeerd, omdat de regel is dat gegevens nooit worden gewist (de ontwikkelaarsvoorwaarden van Spotify
  vragen anders).
- **Weesbestanden na een crash** (ingelezen uploads, een half geschreven miniatuur) worden bij het starten
  niet opgeruimd. Ze staan alleen in de eigen cachemap van de app, nooit in het archief.
- **`/api/thumb` is nog een GET met een neveneffect** (het schrijft een miniatuur in de eigen cache). Een `<img>`
  kan niets anders doen dan GET.

## Zoeken, reeksen en kaart

- **Woorden die het zoekmodel zwak kent.** Opnieuw gemeten met het echte model (30 nieuwe zoekopdrachten, 87 tot
  93 procent gevonden, de grens bleef staan). Een woord als "avondeten" zet de goede foto's zelf laag: dat is het
  model, niet de grens. "Eten" of "dinner" vindt ze wel.
- **Gebatchte beeldherkenning.** De gebruikte ONNX-export heeft een vaste batchgrootte van 1; 10 tot 70 foto's per
  seconde is genoeg.
- **Koude eerste zoekopdracht onder 2 seconden.** Dat lukt met modelbestanden in het OS-geheugen (1,4 s); direct na
  opstarten of van een drukke externe schijf kan het 10 tot 20 s duren. Het model wordt voorgeladen zodra het zoekveld
  focus krijgt.
- **Video's naast foto's in Beste uit reeks.** Video's vormen alleen reeksen met video's, op één beeld per video (op
  1 s, of op 10 % of 50 % van de duur als dat beeld vlak is) en alleen bij vrijwel dezelfde lengte. Een ingekorte
  kopie wordt daardoor niet herkend, en een video die helemaal zwart is doet niet mee.
- **Een groot beeld van een mkv of avi in Groot bekijken.** Een video die de browser niet afspeelt, toont alleen
  zijn miniatuur (300 px) en blijft daarom passend op 100%; een groter beeld zou een extra ffmpeg-cache vragen.
  De miniatuur van een video die zwart begint, is ook nog zwart (de miniatuur blijft op 1 s).
- **Automatisch reeksen zoeken na elke index.** Dat zou veel processorgebruik geven zonder dat je erom vraagt.
- **Paren bij 100.000 sterk scheve hashes nog sneller.** Nu 6 tot 7 s (was 12 tot 24 s) in het slechtste geval
  van de benchmark (`scripts/bench_near_pairs.py`); verder vraagt het gecompileerde code.
- **De opnamedatum in camcorder-mts (AVCHD).** Datum, duur en afmetingen van avi, mkv en mts komen nu uit ffprobe;
  een mts zonder datum in de container houdt de bestandsdatum, want de MDPM-datum in de videostroom wordt niet
  gelezen (er was geen echt camerabestand om tegen te testen).
- **RAW-miniaturen met echte camerabestanden.** RAW via `sips` is getest met een nagebootste fout, niet met echte RAW.
- **Namen van zeeën bij een foto op het water.** Een foto op zee heet "Voor de kust van Zeeland, Nederland" of
  "Op zee, 80 km uit de kust van Portugal", niet "Noordzee" of "Middellandse Zee": de vlakken van de zeeën (Natural
  Earth marine polys) zitten niet in de offline kaartdata. *Kan later met een extra bestand van ongeveer 1 MB.*
- **Het land over de datumgrens heen.** De offline kaart tekent elk land één keer; wie voorbij 180 graden schuift
  (Japan naar de Stille Oceaan), ziet daar lege zee en geen namen, tot de kaart terugspringt.

## Assistent (Pasha)

- **Verplaatsen via voorstel.** Voorstellen leven tien minuten in het geheugen en overleven geen herstart; dat is
  veiliger dan ze bewaren.
- **Exact dubbele bestanden als apart voorstel.** De duplicatenscan heeft geen blijvend resultaat om uit voor te stellen;
  Pasha opent daarom de scan.
- **Het resultaat van een actie terug naar het model** (een tweede antwoord na de actie). Pasha toont het resultaat
  zelf als strook met een knop; een tweede ronde zou elke vraag twee keer zo lang laten duren. Zie
  [PASHA_TOOLS.md](PASHA_TOOLS.md).
- **Ollama's eigen gereedschapsaanroep is alleen gemeten met qwen2.5:3b.** Een ander model kiest zelf het formaat op
  wat Ollama over het model zegt; meten kan met `scripts/bench_pasha_tools.py --model`.

## DataGatherer

- **Gecodeerde iPhone-back-ups lezen binnen DataVault.** Dat vraagt versleuteling en een extra bibliotheek.
- **Bijlagen die alleen op de Mac staan (WhatsApp, iMessage)** tonen een bestandsvakje: lezen vraagt Volledige
  schijftoegang, die de server niet mag nodig hebben.
- **De tijdsmarge voor hetzelfde websitebezoek** (5 seconden tussen Chrome op deze Mac en Takeout) is gekozen,
  niet geijkt: je echte geschiedenis is tijdens de bouw nooit gelezen. Safari wordt niet samengevoegd (Safari staat
  niet in Takeout).
- **Een `.tgz` binnen een `.zip`** wordt niet gelezen; een `.tgz` als bestand of in een map wel.
- **De vorm van `message_summary_info`** (eerdere versies van een bewerkt iMessage) komt uit de openbare beschrijving
  en is niet aan een echte chat.db getoetst. Is hij anders, dan blijft alleen de tekst van nu zichtbaar, plus de
  tekst die DataVault al had.
- **Een eigen schema voor Chrome en Safari.** Schema's bestaan alleen voor bronnen met een online koppeling.
- **Ijking van de matchtijd voor Last.fm en de Spotify-API** vraagt je echte luisterdata, die tijdens de bouw nooit
  gelezen is.
- **Apple Music en Snapchat** staan niet in de Spotify-samenvoeging of de berichtentabel, omdat die tabellen daar niet
  voor gemaakt zijn.

## Uiterlijk en bediening

- **"Ongedaan maken" na een Pasha-voorstel of een Keurer-keuze "houden".** Pasha's verplaatsingen staan in het
  Herstelcentrum; "houden" verplaatst niets en heeft ↓ al, dus de melding zou alleen ruis geven in de snelle Keurer.
- **De interne namen van de Spotify-types** (`Early Bird`, `Broad Explorer`) blijven Engels in de API; het scherm
  vertaalt ze, zodat bestaande gegevens en tests niet veranderen.
- **Lange titels in het takenpaneel inkorten.** Ze worden netjes afgekapt met een beletselteken.
- **Een eigen `LICENSE`-bestand.** Er staat nog geen licentie in de repository. *Goede volgende stap.*

## Software en meten

- **Twee servers op hetzelfde archief.** De tweede ziet de lopende taken van de eerste als onderbroken en beide doen hun
  eigen indexeerronde. Dat bestond al; de schrijfacties zijn idempotent.
- **Alle metingen zijn op gegenereerde archieven** op de interne SSD van de ontwikkelmachine, niet op de externe schijf en
  nooit op het echte archief.
- **De rooktest draait niet in een gewone pytest-ronde.** Hij duurt vijf tot tien minuten en heeft Playwright met
  WebKit nodig; `pytest -m smoke` draait hem wel. Onder zware belasting wacht hij nu zelf langer en vraagt hij een
  verzoek dat door herladen werd afgebroken opnieuw, maar een machine die minutenlang volledig bezet is kan hem nog
  steeds laten mislukken.
- **Loskoppelen wordt nagebootst door de archiefmap te hernoemen** (in `tests/test_unplug.py` en de rooktest). Een echte
  USB-schijf die midden in een schrijfactie wegvalt geeft andere fouten (EIO in plaats van 'niet gevonden'); dat is
  niet automatisch te testen zonder de schijf echt los te trekken.
