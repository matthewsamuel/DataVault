# Bewust niet gedaan

Een eerlijke lijst van wat er in fase 2 en 3 met opzet is blijven liggen, met de reden. Kleine
interne zaken (commitgeschiedenis, tijdelijke mappen, losse meetpunten) staan er niet in. Een
**volgende stap** is iets dat de moeite waard is om als eerste op te pakken.

Terug naar de [README](../README.md) of het [CHANGELOG](../CHANGELOG.md).

## Veiligheid en bestanden

- **Oude Keurer-beslissingen ("weg") van vóór de migratie zijn niet ongedaan te maken.** Er is nooit
  opgeslagen waar het bestand heen ging en de oude prullenmapnamen zijn dubbelzinnig. Raden zou het
  verkeerde bestand kunnen terugzetten, dus de app meldt het en doet niets.
- **Terugzetten van een duplicatenrun controleert niet op inode en grootte.** Het bestaande controleert
  wel of er niets overschreven wordt. Een extra controle vraagt een nieuwe migratie. *Goede volgende stap.*
- **Opgehaalde Spotify-gegevens worden niet gewist bij "Vergeet dit account".** Alleen de koppeling verdwijnt; de rijen
  worden gemarkeerd, omdat de regel is dat gegevens nooit worden gewist (de ontwikkelaarsvoorwaarden van Spotify
  vragen anders).
- **Meegekopieerde bestandsdatum (birthtime) bij exporteren is niet bewezen.** De code probeert hem gelijk
  te zetten, maar de tests controleren alleen de wijzigingsdatum.
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
- **Landnamen op de offline kaart.** Landen tonen een tooltip; labels vragen botsingsberekening, en middelpunten van
  samengestelde landen (Frankrijk) staan op de verkeerde plek. Provincies ontbreken ook.
- **Een plaatsnaam bij elke foto.** Een stad wordt alleen genoemd als er binnen 40 km een grotere stad in de
  offline gegevens ligt; op een kust of in de natuur blijft alleen het land over.
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

## Assistent (Pasha)

- **Verplaatsen via voorstel.** Voorstellen leven tien minuten in het geheugen en overleven geen herstart; dat is
  veiliger dan ze bewaren.
- **Exact dubbele bestanden als apart voorstel.** De duplicatenscan heeft geen blijvend resultaat om uit voor te stellen;
  Pasha opent daarom de scan.
- **Ollama's eigen gereedschapsaanroep** is niet vergeleken met het eigen JSON-formaat, dat bij qwen2.5:3b in 85 tot
  90 procent van de tests lukt.

## DataGatherer

- **Gecodeerde iPhone-back-ups lezen binnen DataVault.** Dat vraagt versleuteling en een extra bibliotheek.
- **Bijlagen die alleen op de Mac staan (WhatsApp, iMessage)** tonen een bestandsvakje: lezen vraagt Volledige
  schijftoegang, die de server niet mag nodig hebben.
- **Tapback-verwijderingen, bewerkte en ingetrokken iMessages** worden niet opnieuw bekeken.
- **`.tgz`-Takeouts** worden gekopieerd maar niet gelezen; alleen `.zip` en uitgepakte mappen.
- **Dezelfde websitebezoek uit twee bronnen** (Chrome lokaal en Takeout) kan twee keer in Activiteit staan.
- **Een eigen schema voor Chrome en Safari.** Schema's bestaan alleen voor bronnen met een online koppeling.
- **Ijking van de matchtijd voor Last.fm en de Spotify-API** vraagt je echte luisterdata, die tijdens de bouw nooit
  gelezen is.
- **Apple Music en Snapchat** staan niet in de Spotify-samenvoeging of de berichtentabel, omdat die tabellen daar niet
  voor gemaakt zijn.
- **Twee oudere duplicaat-opdrachten** (`delete_single`, `scan_folder`) nemen hun pad nog als URL-parameter. Ze worden
  niet meer door het scherm gebruikt en elke POST vraagt een token.

## Uiterlijk en bediening

- **Een eigen tekstonderdeel in het menu voor Berichten, DataGatherer en Reeksen en een overloopmenu "Meer".** Er is geen ruimte in het
  menu bij 1024 px; de onderdelen zitten daarom in andere schermen (Reeksen bij Duplicaten; DataGatherer is een icoon in de menubalk). *Goede volgende stap.*
- **Een album als bereik in Welke is mooier?** De haak (`elo.register_scope`) bestaat; een album-scope is nog niet
  geregistreerd.
- **Lange titels in het takenpaneel inkorten.** Ze worden netjes afgekapt met een beletselteken.
- **Een keuze voor de back-upmap in de interface.** Ze staat in `config.json`.
- **Een melding met "Ongedaan maken" na bepaalde acties.** De gedeelde melding heeft geen knop.
- **Kopregels en teksten die nog Engels zijn** in de lange uitleg bij Spotify DNA en inzichten.
- **Een eigen `LICENSE`-bestand.** Er staat nog geen licentie in de repository. *Goede volgende stap.*

## Software en meten

- **Twee servers op hetzelfde archief.** De tweede ziet de lopende taken van de eerste als onderbroken en beide doen hun
  eigen indexeerronde. Dat bestond al; de schrijfacties zijn idempotent.
- **Alle metingen zijn op gegenereerde archieven** op de interne SSD van de ontwikkelmachine, niet op de externe schijf en
  nooit op het echte archief.
- **De rooktest is niet meegenomen in pytest** en is gevoelig voor een zwaar belaste machine (een enkele keer een
  afgebroken `fetch` of een time-out); een herhaling slaagt.
- **Een blijvende test voor loskoppelen en terugplaatsen van de schijf** in de rooktest. De controle is eenmalig met de
  hand gedaan.
- **Een uniform foutantwoord voor een vreemde `Host`.** Starlette antwoordt daar met platte tekst; alleen
  DNS-rebinding-pogingen zien dat.
