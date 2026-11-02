# DataVault Dashboard

Een lokale media viewer en RAW duplicaten opruimtool voor macOS. Draait op externe schijf T7, met absolute prioriteit voor data-veiligheid en metadata-behoud.

Dit is de Nederlandse gebruikershandleiding. Voor een overzicht van het project (architectuur, veiligheidsontwerp, gemeten snelheid) zie de [README in de hoofdmap](../README.md); wat bewust niet gedaan is staat in [docs/NIET_GEDAAN.md](../docs/NIET_GEDAAN.md).

## Mapstructuur

```
/Volumes/T7/datavault_dashboard_dev/
├── DataVault.app          # Dubbelklik om de app te starten
├── datavault_icon.png     # App icoon
└── code/                  # Alle broncode en configuratie
    ├── backend/           # Python FastAPI server
    │   ├── main.py        # Server entrypoint
    │   ├── config.py      # Configuratie
    │   ├── database.py    # SQLite (cache & undo)
    │   ├── file_ops.py    # Veilige bestandsoperaties
    │   ├── scanner.py     # Bestanden scannen & hashen
    │   ├── thumbnail.py   # Thumbnails genereren
    │   ├── jobs.py        # Achtergrondtaken (voortgang, pauzeren, hervatten)
    │   ├── job_types.py   # Duplicatenscan, indexeren, miniaturen, foto's vergelijken
    │   ├── near_dups.py   # Bijna-duplicaten en bursts (Beste uit reeks)
    │   ├── media_index.py # Bibliotheek: index van het hele archief (datum, camera, GPS)
    │   ├── semantic.py    # Slim zoeken: zoekmodel, herkennen, zoeken
    │   ├── assistant.py   # Pasha: gereedschappen en voorstellen met bevestiging
    │   ├── export.py, backup.py, recovery.py, elo.py, places.py
    │   ├── datagatherer/  # Spotify, Takeout, browsers, berichten
    │   ├── migrations.py  # Genummerde databasemigraties
    │   └── routes/        # API endpoints, één router per onderdeel
    ├── frontend/          # HTML/CSS/JS interface
    ├── models/            # Gedownloade zoekmodellen (niet in git; alleen na "installeren")
    ├── scripts/           # download_models.py, eval_semantic_search.py
    ├── venv/              # Python virtual environment
    ├── config.json        # Vault paden configuratie
    ├── requirements.txt   # Python dependencies
    ├── start.sh           # Server starten via terminal
    └── stop_datavault.sh  # Server stoppen via terminal
```

## Starten

**Gewone manier:** Dubbelklik op `DataVault.app`

**Via terminal (aanbevolen):** het startscript maakt bij de eerste keer de venv aan, stopt een
eerder gestarte DataVault-server op poort 8000 en opent de browser:
```bash
cd /Volumes/T7/datavault_dashboard_dev/code
./start.sh
```

Of met de hand (de app wordt door een *factory* gebouwd; een module-level `app` bestaat niet):
```bash
cd /Volumes/T7/datavault_dashboard_dev/code
venv/bin/python -m uvicorn --factory backend.main:create_app --host 127.0.0.1 --port 8000
```

Stoppen: `Ctrl+C` in de Terminal, of `./stop_datavault.sh` (nodig na een herstart vanuit de app,
zie RESTART.md).

Open daarna: **http://127.0.0.1:8000**

## Configuratie

Kopieer `code/config.example.json` naar `code/config.json` en stel de vault-paden in
(`config.json` staat niet in git):

```json
{
  "vault_root": "/Volumes/T7/vault",
  "raw_folder": "/Volumes/T7/vault/RAW",
  "duplicates_folder": "/Volumes/T7/vault/RAW_DUPLICATES",
  "trash_folder": "/Volumes/T7/vault/TRASH",
  "cache_folder": "/Volumes/T7/vault/.datavault_cache"
}
```

Optioneel voor de back-ups van de database (zie hieronder): `"backup_folder"` (standaard
`~/Library/Application Support/DataVault/backups`), `"backup_keep_daily"` (7) en
`"backup_keep_weekly"` (4). Een pad met `~/` begint in je thuismap. Een relatief pad (zonder `/` of
`~/` vooraan) gebruikt de app niet, omdat het zou afhangen van de map waarin de app start: dan blijft
de standaardmap in gebruik en zegt Instellingen > Back-ups waarom.

Optioneel: `"models_folder"` (waar het zoekmodel van Slim zoeken staat; standaard `code/models`).

## Installatie (eerste keer)

### Vereisten
- macOS (Apple Silicon of Intel)
- Python 3.12 (de vastgepinde versies in `requirements.txt`, zoals Pillow 13 en Starlette 1.10,
  werken niet op de Python 3.9 van macOS zelf): `brew install python@3.12`
- Externe schijf T7 gemonteerd op `/Volumes/T7`
- ffmpeg (optioneel, voor video thumbnails): `brew install ffmpeg`

### Setup
```bash
cd /Volumes/T7/datavault_dashboard_dev/code
python3.12 -m venv venv
venv/bin/pip install -r requirements.txt
cp config.example.json config.json   # en daarna de paden aanpassen
```
`./start.sh` doet de eerste twee stappen ook zelf als er nog geen `venv` is.

## Functies

### Media Viewer
- Mapboom bladeren (RAW_DUPLICATES en cache uitgesloten)
- Foto's en video's bekijken met thumbnails
- Volledig scherm met metadata (echte datum; Shows-waardering alleen als je erop gestemd hebt)
- Bladeren met de pijltjestoetsen of de zones links en rechts; Verplaatsen en Open in Finder
  werken altijd op het bestand dat je ziet (ook vanuit de tijdlijn en de chat)

### Duplicaten
- Scan een map op byte voor byte identieke bestanden (grootte, dan xxHash3; vlak voor elke verplaatsing een volledige vergelijking)
- Per groep kies je welke kopie blijft (regel of zelf aanklikken)
- Duplicaten verplaatsen naar RAW_DUPLICATES (structuur behouden), nooit wissen
- Elke opruimactie is in één keer terug te zetten (Herstellen, of in het Herstelcentrum)

### Beste uit reeks (bijna-duplicaten en bursts)
- Tweede tabblad op **Duplicaten**: foto's die op elkaar *lijken* (verkleinde of opnieuw opgeslagen
  kopieën, bewerkingen, bursts), niet byte voor byte gelijk. `backend/near_dups.py`, routes in
  `backend/routes/series.py`, scherm in `frontend/series.js`; migraties 30 en 31.
- **Vergelijken** is een achtergrondtaak (`phash`, na een snelle ronde van de bibliotheek). Per foto
  (alleen foto's; video's en RAW-bestanden die Pillow niet kan openen worden overgeslagen) een klein
  gedecodeerde versie (JPEG in draft-modus, HEIC via pillow_heif, rechtop volgens EXIF): dHash en
  pHash (64 bits), de gemiddelde kleur van de vier kwarten, contrast, scherpte (variantie van de
  Laplaciaan), en afgekapte schaduwen en hoge lichten. Alleen nieuwe of veranderde foto's worden
  gelezen; pauzeren, hervatten en annuleren verliezen hooguit één stapel van 200.
- Paren: multi-index hashing (4 delen van 16 bits, nooit alle paren); foto's die precies hetzelfde
  ogen vormen één groep. Bursts: dezelfde camera, EXIF-tijden hooguit 3 s na elkaar, dezelfde map.
- Strengheid (pHash / dHash / kleur): **Streng** 4/6/10, **Normaal** 8/10/16 (standaard), **Ruim**
  11/14/24; bursts 14/16/16, 20/22/22 en 26/28/30. Is één van de twee foto's bijna vlak (contrast
  < 10), dan mag de kleur hooguit 6 verschillen. Afgestemd op het testarchief (de verkleinde
  `strand klein.jpg` hoort bij het origineel, de effen testfoto's in andere kleuren niet) en op 132
  echte foto's met gemaakte varianten.
- Exacte kopieën blijven het werk van het eerste tabblad: hier tellen ze als één foto, met
  "+n exacte kopieën" erbij (ze gaan mee als die foto weggaat, alleen als ze dan nog byte voor byte
  gelijk zijn). Een groep van meer dan 48 foto's komt in delen, op tijdsvolgorde.
- Score (0-100, binnen de reeks, deterministisch): scherpte 40, resolutie 25, belichting 15, jouw
  keuze 20 (Keurer "houden" 12, Shows-waardering tot 8); gelijke stand: grootste bestand, dan pad.
  Scherpte wordt bij elke foto gemeten met de lange zijde op 512 px (een kleinere foto wordt eerst
  vergroot; `ALGO_VERSION` 2), en een foto telt nooit scherper dan een foto uit dezelfde reeks met
  meer pixels: een verkleinde kopie kan niet meer detail hebben dan zijn bron.
- Bevestigen verplaatst de rest met `move_no_overwrite` naar
  `RAW_DUPLICATES/near-duplicates/<pad onder RAW>`, als één actie per reeks in het Herstelcentrum
  (`recovery.ActionRecorder`, soort `near_dup`). De server controleert vooraf dat de foto's één
  reeks zijn, dat een foto die blijft er nog onveranderd staat, en per foto dat het nog het
  vergeleken bestand is. **Alles behouden** onthoudt de reeks (tot hij verandert); **Overslaan**
  geldt voor deze sessie.
- API: `GET /api/series/status`, `GET /api/series?strictness=&sort=size|recent&offset=&limit=`,
  `POST /api/series/keep {members, keep}`, `POST /api/series/keep-all {members}` (antwoordt `{key}`),
  `POST /api/series/reviews/undo {key}` (één keep-all terug), `POST /api/series/reviews/reset`.
- Exacte kopieën herkennen (`hash_twins`, vóór het zoeken van paren): tweelingen van dezelfde grootte
  krijgen een xxh3 (`media_phash.content`), in stapels van 100 weggeschreven, uit de cache van
  `file_hashes` als het bestand onveranderd is (inode, grootte, mtime, ctime), en nieuwe hashes gaan die
  cache in. `all_series` laat een tweeling van dezelfde grootte zonder hash weg, zodat een gestopte taak
  nooit kopieën als verschillende foto's toont.
- Scherm (`series.js`): een bevestigde of behouden reeks verdwijnt ook op de server, dus gaat
  `nextOffset` per besluit één omlaag (`seriesState.gone` corrigeert een verzoek dat al onderweg was);
  een overgeslagen reeks telt mee. **Groot bekijken** (`#modal-series-compare`, toets v) toont de reeks
  passend of op 100% (breedte van de grootste foto in CSS-pixels, `--compare-x/y` als gedeelde uitsnede).
- Snelheid (M3 Pro, 4 threads): 12 MP JPEG ~130 foto's/s; 4.000 foto's van 2 MP via de server in
  10 s (390/s; `/api/health` p95 12 ms tijdens de taak), een tweede ronde zonder nieuwe foto's
  0,2 s. HEIC wordt volledig gedecodeerd (36 MP: ~0,7 s per foto per thread, ~5 per seconde met
  4 threads). Paren en bursts voor 100.000 foto's: ~3 s bij willekeurige hashes. Foto's met dezelfde
  phash (scans, screenshots, effen afbeeldingen) worden als één waarde opgezocht en direct tegen de
  dhash- en kleurgrens gehouden, zodat alleen echte paren bewaard worden: 100.000 tweekleurige
  testbeelden (19.268 verschillende phashes, 89 miljoen kandidaten, 1,16 miljoen paren) in ~15 s
  (waarvan ~9 s wegschrijven), piek ~0,5 GB, langste GIL-pauze 30 ms (was 296 s, 10,5 GB en 16 s).
  Daarna zoekt een ronde alleen de groepen zonder anker op (`media_phash.paired = 2` markeert de
  foto waarvandaan de paren van een groep zijn opgezocht): 2.000 nieuwe foto's erbij in ~2 s plus
  het wegschrijven. Het geheugen blijft begrensd: de kandidaten worden per ~130.000 gecontroleerd
  (`CANDIDATE_BUDGET`), en boven 4 miljoen paren (`NEAR_PAIR_LIMIT`) blijven alleen de paren over
  die de reeksen maken (per strengheid een opspannend bos, `_forest`). Werkt ook met
  numpy ouder dan 2.0 (eigen bittelling als `np.bitwise_count` ontbreekt).

### Bibliotheek (media-index)
- De app houdt een index bij van elke foto, video en audiobestand in het archief (datum, afmetingen,
  camera, GPS, duur), in de database (`backend/media_index.py`, migratie 11). Tijdlijn, "op deze dag"
  en foto's per datum lezen daaruit in plaats van de schijf af te lopen.
- Bijwerken gaat vanzelf: bij het starten, als de schijf terug is, elk half uur, en als de tijdlijn
  ziet dat een map veranderd is. Alleen mappen waarvan de wijzigingsdatum veranderde worden opnieuw
  gelezen (100.000 ongewijzigde bestanden: ~0,03 s). 'Bibliotheek bijwerken' in het takenpaneel
  controleert alles, ook bestanden die op hun plek bewerkt zijn.
- Verplaatst de app een bestand, dan gaan de indexregel, de Shows-waardering en een Keurer-'houden'
  meteen mee. Verplaats je het in Finder, dan herkent de volgende ronde dat aan inode, grootte en
  aanmaakdatum (of aan de inhoud als die al bekend was), en gaat de waardering ook mee.
- Een bestand dat weg is, wordt eerst verborgen en pas aan het eind van een ronde uit de index
  gehaald. De index leest alleen; hij verplaatst of wist nooit een bestand.
- `DATAVAULT_AUTO_INDEX=0` zet het automatisch bijwerken uit (de tests doen dat).

### Albums: filters, slimme albums en op deze dag
- Tabblad **Albums** (sneltoets `g a`, `frontend/library.js`) toont de bibliotheek: alles, met filters
  op soort, datum (jaar, maand, periode of van/tot), map, camera, bestandstype, locatie,
  Shows-waardering, Keurer (gehouden of nog niet gekeurd), oriëntatie en opnamedatum, en sorteren op
  datum, naam, grootte of waardering. Het raster is gevirtualiseerd en haalt 200 bestanden per keer op
  als ze in beeld komen. Het adres volgt wat je ziet (`#albums?kind=photo&year=2019`,
  `#albums/album/3`, `#albums/op-deze-dag/07-14`), dus een weergave is te bewaren als bladwijzer.
- Eén filtervorm voor het adres, de server en de opgeslagen albums: `backend/library_filters.py`
  (`clean()` controleert en normaliseert, `to_query()` maakt er `media_index`-filters van). Datums
  zijn lokale dagen, net als de opnamedatums in de index en de tijdlijn. `folder` is één map,
  `folders` een lijst (een van deze mappen; zo opent 'op deze dag' met de mappen van Home), nooit
  allebei. De pagina laat waarden uit het adres die niet kunnen weg (`paramsToSpec` in library.js)
  en toont een filter dat de server weigert (400/403/422) als **Filter klopt niet**.
- Een slim album is een opgeslagen filter met naam, sortering en icoon (`backend/smart_albums.py`,
  migratie 20). Het is altijd actueel en verplaatst of kopieert nooit een bestand. De zes
  standaardalbums komen er één keer bij; verborgen of verwijderde komen terug met
  **Standaardalbums terugzetten** (is de naam intussen van een eigen album, dan met "(2)" erachter).
  Namen zijn uniek zonder op hoofdletters te letten (`casefold()`, ook buiten ASCII). Verandert een
  standaardalbum in een nieuwe versie (`UPGRADES`, `DEFAULTS_VERSION`), dan krijgt een ongewijzigd
  album het nieuwe filter één keer.
- **Op deze dag** toont per jaar (nieuwste eerst) de foto's en video's van een dag in eerdere jaren,
  met vorige/volgende dag en de mappen die op Home gekozen zijn, alleen met een echte opnamedatum
  (`capture_date: known`). 29 februari is altijd te kiezen; in een jaar zonder 29 februari laat
  28 februari die foto's ook zien. Elk jaar heeft `filters`: dezelfde selectie als browse-filter,
  zodat **Alle N bekijken** precies die N opent.
- API onder `/api/library`: `browse` (filters als queryparameters of `album=id`, met `offset`,
  `limit` tot 1000, `sort`; het antwoord heeft `version` zodat een lijst die doorbladert ziet dat de
  selectie veranderde), `facets`, `on-this-day?date=MM-DD`, en `albums` (GET/POST,
  `albums/{id}` GET/PATCH/DELETE met een id van 0 tot 2^62, `albums/defaults` POST). De tellingen in
  de zijbalk worden per album bewaard tot de index verandert (hooguit 30 s).
- `media_index.query()` sorteert eerst alleen de id's (en de sorteersleutel) en leest daarna de rijen
  van de pagina; gelijke waarden gaan op id, namen op pad. `selection_info()` geeft aantal en versie
  in één keer. Op 100.000 rijen kost een diepe pagina met aantal en versie hoogstens ~140 ms.
- `showToast(title, message, type, action)` kan een knop meekrijgen (`{label, onClick}`), bijvoorbeeld
  **Ongedaan maken** na het verbergen van een album.

### Slim zoeken (zoeken op wat er op een foto staat)
- Zoek foto's en video's op inhoud, in het Nederlands of Engels: "hond op het strand",
  "verjaardagstaart", "zonsondergang boven zee". Enter in het zoekveld bovenaan (of de toets `s`)
  opent het zoekvenster voor de hele bibliotheek, met de keuze **Slim zoeken** of **Bestandsnaam**,
  filters op soort en periode, en de resultaten op volgorde van hoe goed ze passen (pijltjes, Enter
  opent de viewer). In de viewer zoekt **Vergelijkbaar** (`v`) foto's die op de getoonde foto lijken.
- Alles draait op deze Mac (`backend/semantic.py`, `routes/search.py`): onnxruntime + tokenizers +
  numpy, geen torch, geen cloud. De app downloadt nooit zelf iets: het zoekmodel (ongeveer 926 MB)
  komt alleen binnen via de knop **Zoekmodel installeren** in Instellingen (die eerst vraagt en de bron
  en grootte toont) of met
  ```bash
  cd code
  venv/bin/python scripts/download_models.py          # --list toont alleen bestanden, bronnen en grootte
  ```
  Elk bestand is vastgepind op een revisie en wordt met SHA-256 gecontroleerd; een afgebroken download
  gaat verder waar hij was. De modellen staan in `code/models/` (op de T7, niet in git) of in de map
  die `"models_folder"` in `config.json` noemt.
- Na de installatie (en na elk bijwerken van de bibliotheek) bekijkt de taak **Foto's herkennen voor
  Slim zoeken** elke nieuwe of gewijzigde foto en video één keer (video: het beeld op 1 s). Hij leest
  de miniatuur (en maakt die als hij er nog niet is), schrijft per foto een vector van 768 getallen
  (float16) in de database (migratie 40, `semantic_embeddings`, op `media.id`: een verplaatste foto
  houdt zijn vector) en is te pauzeren, hervatten en annuleren; bij een losgekoppelde schijf wacht hij.
- Zonder model werkt de rest gewoon; Slim zoeken zegt dan hoe je het installeert, en zoeken op
  bestandsnaam werkt al.

**Het model.** SigLIP 2 base, patch 16, 224 px (Google, Apache-2.0), meertalig. Het beeldmodel is de
fp32-ONNX van immich-app (draait via Core ML op de grafische chip), het tekstmodel de fp16-ONNX van
onnx-community (draait op de processor); beide geven dezelfde vectoren (cosinus 0,99999). Gekozen na
een meting op een eigen gelabelde set (`scripts/semantic_eval/`: 179 foto's, 149 met open licentie van
Wikimedia Commons plus 30 macOS-achtergronden; 50 zoekopdrachten in het Nederlands en Engels, met het
juiste antwoord erbij). Opnieuw meten: `venv/bin/python scripts/eval_semantic_search.py --images <map>`.

| Kandidaat (op de processor) | NL R@1 | NL R@5 | NL MRR | NL mAP | EN R@1 | EN mAP | beelden/s | tekst |
|---|---|---|---|---|---|---|---|---|
| OpenAI CLIP ViT-B/32 (alleen Engels) | 0,66 | 0,78 | 0,72 | 0,63 | 0,94 | 0,92 | 37 | 17 ms |
| idem + vertalen met qwen2.5:3b (Ollama) | 0,74 | 0,88 | 0,80 | 0,74 | 0,94 | 0,93 | 37 | 175 ms |
| clip-ViT-B-32-multilingual-v1 + ViT-B/32 | 0,80 | 0,92 | 0,85 | 0,79 | 0,94 | 0,92 | 34 | 3 ms |
| XLM-RoBERTa base + ViT-B/32 (LAION-5B) | 0,98 | 1,00 | 0,99 | 0,94 | 0,96 | 0,93 | 35 | 33 ms |
| SigLIP 2 B/32, 256 px | 0,96 | 1,00 | 0,98 | 0,92 | 0,98 | 0,95 | 21 | 30 ms |
| **SigLIP 2 B/16, 224 px (gekozen)** | **0,98** | **1,00** | **0,99** | **0,93** | **0,98** | **0,96** | 10 (GPU 65-75) | 35 ms |

R@k: aandeel zoekopdrachten met een goede foto op plek 1 of in de top 5; MRR: gemiddelde 1/plek van de
eerste goede foto; mAP: hoe hoog álle goede foto's staan. Op onze set zijn XLM-R en SigLIP 2 B/16
gelijk; op de grote openbare test (Crossmodal-3600, 3600 foto's, door Immich gemeten) is SigLIP 2 B/16
duidelijk beter: Nederlands 75,3 tegen 70,8, Engels 84,9 tegen 76,9. Met de grafische chip is hij ook
snel genoeg. Varianten: fp16 geeft dezelfde volgorde als fp32 (NL mAP 0,931 tegen 0,930); int8 kost
het Nederlands het meest (tekstmodel int8: NL mAP 0,73) en is dus niet gebruikt. De miniatuur (300 px)
in plaats van het origineel geeft dezelfde R@1, R@5 en MRR (mAP 0,924 tegen 0,931) en is vele malen
sneller te lezen. Vertalen via Ollama hielp weinig ("hond" werd "honduras").

**Snelheid** (M3 Pro, 36 GB):

| Meting | Resultaat |
|---|---|
| Herkennen, miniaturen al aanwezig, grafische chip | 71 foto's/s (1.524 foto's in 21 s) |
| Herkennen, miniaturen nog te maken van 12 MP-JPEG's | 26 foto's/s |
| Herkennen op de processor (minder dan 1.000 wachtende foto's) | ~10 foto's/s |
| Core ML laden | eerste keer ~4 min (vertalen naar de grafische chip, daarna bewaard), daarna ~70 s |
| `/api/health` tijdens het herkennen | p50 1,6 ms, p95 2,4 ms |
| Zoeken, 100.000 foto's, nieuwe zoekopdracht | 66 ms (met filters ook ~65 ms) |
| Vergelijkbare foto's, 100.000 foto's | 31 ms |
| Volgende pagina of dezelfde zoekopdracht | 0,3 ms op de server |
| Eerste zoekopdracht na het starten (model en 100.000 vectoren laden) | 1,4 s; trager als de T7 druk is |
| Geheugen | tekstmodel ~1,7 GB (na 15 min niet zoeken weer vrij), vectoren 150 MB per 100.000 foto's |

Na een herstart van de Mac kan de allereerste zoekopdracht 10 tot 20 s duren (het model staat dan nog
niet in het geheugen van het besturingssysteem). Foto's worden een voor een herkend, omdat de gebruikte
ONNX-export een vaste batchgrootte van 1 heeft; de gemeten snelheid (hierboven) is ruim genoeg.

Het zoekveld laadt het model al zodra het focus krijgt, zodat de eerste zoekopdracht meestal direct is.
Een resultaat telt als "goed" als de score minstens 0,085 is, hooguit 0,06 onder de beste ligt en twee
standaardafwijkingen boven de mediaan van de hele index (gemeten op 179 foto's met 50 Nederlandse en
50 Engelse zoekopdrachten: 73% / 81% van de goede foto's bij 67% / 72% precisie, en geen enkele
zoekopdracht met goede foto's blijft leeg); de rest staat achter **Meer foto's die minder goed passen**.

**API** (filters zoals `/api/library/media`: `kind`, `date_from`, `date_to`, `folder`, `recursive`,
`camera`, `has_gps`, `min_rating`, `orientation`):
- `GET /api/search/semantic?q=…&offset=&limit=&include_weak=` → `{total, ranked, next_offset, items:
  [mediarij + score], took_ms, indexed, pending}` (409 zonder model)
- `GET /api/search/similar?path=…` (of `id=`), `GET /api/search/filename?q=…`, `GET /api/search/status`,
  `POST /api/search/warm`; installeren en herkennen zijn taken: `{type: 'model_download'}` en
  `{type: 'semantic'}`.

**Voor de assistent (en andere Python-code):**
```python
from backend import semantic
hits = semantic.search_photos(db, config, "hond op het strand", filters={"kind": "photo"}, limit=20)
# [{id, path, name, kind, taken, ..., score}, ...] beste eerst; semantic.NotReady als het model ontbreekt
```
`semantic.search_text(...)` en `semantic.search_similar(db, config, media_id, ...)` geven ook `total`
en paging.

### Kaart (foto's met GPS)

Het tabblad Kaart (`frontend/map.js`, `backend/routes/geo.py`) toont de media met een GPS-positie
uit de media-index (`media.lat/lon`, door `media_meta.py` gelezen; 0,0 en waarden buiten bereik
tellen als "geen locatie"). Leaflet en Leaflet.markercluster staan in `frontend/vendor/`.
De basiskaart is Natural Earth (`vendor/naturalearth/`, publiek domein) en werkt offline.
Tegels van OpenStreetMap zijn een keuze van de gebruiker (standaard uit, instelling
`map_osm_tiles` in app_settings); het enige internetadres in de frontend is het tegelsjabloon in
`map.js`, toegestaan in `tests/test_frontend_offline.py`.

- `GET /api/map/points?<filters van Albums>|album=&bbox=zuid,west,noord,oost&after=&limit=`:
  `{total, without_gps, bounds, version, next, points: [[id, lat, lon, taken, soort], ...]}`
  (stukken van hoogstens 50.000, op id; `next` is de `after` voor het volgende stuk).
- `GET /api/map/items?ids=1,2,3` (viewer en popups), `GET /api/map/locate?path=`,
  `GET|PUT /api/map/settings {osm_tiles}`.

### Herstelcentrum
- Eén lijst (tab **Herstel**, sneltoets 8) van alles wat de app in het archief verplaatste: Keurer
  (prullenmand, verplaatsen), duplicaten opruimen en Verplaatsen in de viewer, nieuwste eerst, met per
  actie wat waarheen ging en een knop **Terugzetten**. Kan iets niet terug (weg, een ander bestand op
  die plek, de oude plek bezet), dan staat de reden erbij. Er wordt nooit iets overschreven.
- `GET /api/recovery?offset=&limit=` geeft de lijst; elke regel noemt zelf het verzoek dat hem
  terugzet (`undo: {url, body}`): `/api/keurer/undo`, `/api/duplicates/undo` of `/api/recovery/undo`.
  Per regel worden de 20 getoonde bestanden gecontroleerd en daarna alleen tot er één terug kan;
  `checked` zegt hoeveel bestanden `restorable` telt (minder dan `file_count`: een ondergrens). Het
  terugzetten zelf controleert elk bestand opnieuw.
- Een nieuwe soort verplaatsing (bijv. bijna-duplicaten "beste bewaren", verplaatsingen door de
  assistent) heeft geen eigen tabel, route of scherm nodig: `recovery.register_kind("soort", "Label")`
  en per verplaatst bestand `ActionRecorder.add(src, dst)` direct na `move_no_overwrite` (zie de uitleg
  bovenin `backend/recovery.py`; migratie 12 bevat de gedeelde tabellen). Een eigen Provider met veel
  bestanden per actie bouwt zijn regel met `recovery.list_files` en `recovery.FolderChecks`.

### Toetsenbord, instellingen en thema's
- `1`–`9` of `g` + letter wisselt van tabblad (Albums heeft alleen `g a`), `/` gaat naar het zoekveld, `t` wisselt het thema, `?`
  toont alle sneltoetsen. Pijltjes, Home/End en PageUp/PageDown bewegen door het raster van Bestanden en
  de tijdlijn; Enter of spatie opent. Geen sneltoets werkt terwijl je typt.
- In Safari gaat Tab op de pagina zelf niet langs knoppen (zoals **Terugzetten** in het Herstelcentrum);
  gebruik dan ⌥ Tab, of zet het aan in Safari > Instellingen > Geavanceerd ("Druk op Tab om elk
  onderdeel op een webpagina te markeren"). In een venster gaat Tab altijd langs elke knop.
- Elk venster houdt de focus binnen zich, Escape sluit het bovenste, en de focus gaat daarna terug
  waar hij was (`frontend/ui.js`).
- **Instellingen** (tandwiel, sneltoets 9): thema, taalmodel, bibliotheek, Shows-bronnen. Een nieuw
  onderdeel voegt een stuk toe met `registerSettingsSection({id, title, icon, order, description, render})`
  (uitleg bovenin `frontend/settings.js`).
- Beide thema's halen WCAG AA-contrast voor alle zichtbare tekst; de rooktest controleert dat op elk
  scherm (`tests/ui/a11y_checks.js`).

### Uiterlijk (design system)
- Alle kleuren, letters, ruimtes, rondingen, schaduwen en bewegingen komen uit de tokens bovenin
  `frontend/style.css` (tussen `@tokens:start` en `@tokens:end`), voor licht, donker en Auto.
  Buiten dat blok staat geen enkele kleurcode: `tests/test_frontend_static.py` faalt daarop.
- Daaronder staan de gedeelde componenten (`.btn`, `.input`, `.select`, `.switch`, `.pill`,
  `.segmented`, `.card`, `.stat-tile`, `.modal-*`, `.menu`, `.toast`, `.empty-state`, `.skeleton`,
  `.progress`, `.badge`, `.thumb-tile`, ...). Een nieuw scherm gebruikt alleen die.
- Elke pagina staat in dezelfde kolom (`--page-max`, met `--page-gutter`) en begint met
  `.page-header` (een `h1.page-title` met icoon, een `.page-subtitle`, rechts eventueel knoppen).
  Zelf scrollende pagina's houden onderaan `--space-4xl` vrij voor het takenpaneel.
- Elke fototegel is `.thumb-tile` (Bestanden: `.media-card.thumb-tile`, Tijdlijn:
  `.tl-photo-card.thumb-tile`): afgerond, omhoog bij hover, glans tijdens laden, `.selected` geeft
  de groene ring met vinkje.
- Op zwevende vlakken (vensters, menu's, meldingen, takenpaneel) is de haarlijn
  `--border-overlay`; voeg een nieuw zwevend vlak toe aan die regel onder `@tokens:end`.
- Een venstertitel is `h2.modal-title` met één `<i>` vooraan; paden toon je met
  `archivePath()`/`pathNodes()` (app.js) vanaf het archief, met het volledige pad als `title`.
  Een kort padlabel (een chip, een regel in een lijst) is `displayPath(path, max)` (app.js): vanaf
  het archief, `Archief` voor de archiefmap zelf, lang ingekort in het midden, in een element met
  klasse `.path-label` (mono).
- Een eenheid achter een groot getal in een tegel is `<span class="stat-unit">` (gewone letter,
  rustiger kleur); `.stat-label` staat onderaan de tegel, zodat labels in een rij op één lijn staan.
- Het takenpaneel zet `--job-dock-space` (jobs.js); `.main-content` houdt zoveel onderaan vrij.
  Een eigen scrollvlak dat tot de onderkant van het venster loopt, gebruikt die variabele ook.
- Knoppen rechts in een paginakop staan bovenaan, op de hoogte van de titel (`align-items:
  flex-start`), in de gewone knopgrootte (geen `.btn-small`).
- Mono (`--font-mono`) alleen voor getallen en paden; staat een getal in een zin, zet dan alleen
  het getal in een eigen `<span>` in mono.
- Een smal venster (onder 900 px) krijgt `--page-gutter: 20px`; gebruik voor kantlijnen altijd
  `--page-gutter`, nooit een eigen waarde in een media query (de Tijdlijn rekent zijn tegels uit
  met dezelfde marge). Het menu bovenin schuift dan opzij (`.nav-tabs`).
- Meldingen (`showToast`) hebben een neutrale titel met een icoon per soort (`TOAST_ICONS`).
- In het takenpaneel telt een taak die `done` is met `summary.complete === false` (of
  `summary.errors > 0`) als onvolledig (`jobIncomplete()` in jobs.js, klasse `.job-incomplete`):
  die blijft open in beeld. Geef een nieuw taaktype dus `complete` en `errors` in zijn samenvatting.
- `http://127.0.0.1:<poort>/styleguide.html` toont ze allemaal, licht en donker naast elkaar.
- De rooktest bewaart van elk scherm een schermafbeelding in beide thema's (het pad staat in de
  uitvoer, of zet `DATAVAULT_SMOKE_SHOTS`).

### DataGatherer
- De tab **DataGatherer** (wolkje naast Instellingen, `g a`) brengt je eigen gegevens bij: Spotify
  (export, Web API, Last.fm) en Google Takeout. Elke bron is één module in
  `backend/datagatherer/connectors/` (zie `docs/DATAGATHERER_CONNECTORS.md` en `_template.py`); de
  tab bouwt zich op uit hun beschrijving (`GET /api/datagatherer/sources`).
- Geen wachtwoorden en geen browserautomatisering. Tokens en API-sleutels staan alleen in de
  macOS-Sleutelhanger (`keyring`). Netwerk alleen na een klik of een schema dat je zelf aanzette,
  alleen naar de hosts van die bron, en elk verzoek staat in `dg_network_log` (zichtbaar als Netwerklog).
- Exports worden gekopieerd naar `data/<eigenaar>/<Bron>/<datum soort>/` (nooit verplaatst of
  overschreven, sha256 tegen dubbels); bestaande exportmappen worden ter plekke geregistreerd. De
  Downloads-watcher kijkt alleen zolang een export aangevraagd is.
- Optionele configuratie in `config.json`: `"data_owner": "Matthew"` (de map onder `data/`, standaard
  de eerste) en `"datagatherer_watch_folders": ["/Users/…/Dropbox/Exports"]` (extra mappen naast `~/Downloads`).
- Stap 3.9c: Google Takeout (nl/en) en de Tijdlijn van de telefoon, Chrome- en Safari-geschiedenis
  (`local_import`, uit een kopie), Netflix, Snapchat, TikTok, Apple (privacy.apple.com) en Apple
  Gezondheid. Migratie 110: `activity_events`, `location_points`, `location_visits` en
  `media_location_inferred` (geschatte plek van foto's zonder GPS, alleen in de database). Migratie
  135 geeft elk item uit Mijn activiteit een sleutel op de seconde, zodat HTML en JSON van dezelfde
  Takeout één rij geven. API's:
  `/api/activity/{facets,days,events}`, `/api/location/{summary,days,track,settings,geotag}` en
  `/api/map/inferred`; de taak `datagatherer.geotag` schat opnieuw. Tijdzones offline via de Natural
  Earth-landen en `backend/data/country_zones.json` (uit de tz-database). Het scherm Activiteit
  (`activity.js`, `g c`) hangt onder DataGatherer.
- Migraties 60 (DataGatherer) en 61 (samengevoegde Spotify-plays). Het Spotify-scherm leest
  `spotify_plays_merged` pas als elk exportbestand onder `data/` in een ingelezen export ligt
  (`dg_raw_imports.status = 'imported'`, ingelezen na de laatste wijziging van het bestand);
  anders de exportbestanden zoals vroeger (`/api/spotify/coverage` zegt dan `waiting_for_import`).
- Bestand kiezen (`POST /sources/<id>/upload?name=`) zet het bestand onder zijn echte naam in een
  eigen map in `<cache>/datagatherer-upload/` en start een `datagatherer.watch`-taak
  (`{source, upload, name}`) die het naar het archief kopieert en inleest; daarna is de tijdelijke
  kopie weg. `raw_store.archive_file` slaat één export tegelijk op (sha256-controle, kopie en rij
  onder één slot).
- Tests: `DATAVAULT_GATHER_DAEMON=0` (de tests tikken de watcher zelf), een sleutelhanger in het
  geheugen, een netwerkbewaker die alles weigert (`net.set_transport_factory` voor een
  `httpx.MockTransport`), en een LaunchAgents-map in de tijdelijke map.

### Back-ups van de database
- `backend/backup.py` kopieert de database naar de schijf van de Mac: bij het starten als de nieuwste
  back-up ouder is dan 24 uur, daarna elk uur een controle (dus ongeveer dagelijks), en met
  **Nu back-uppen** in Instellingen (`POST /api/backup/run` start de taak `backup` en antwoordt meteen met 202;
  de stappen staan in het takenpaneel, de taak is te stoppen; `GET /api/backup/status` geeft de stand).
  Een database van 600 MB: ~75 s als taak, waar het verzoek eerst 115 s openstond.
  `DATAVAULT_AUTO_BACKUP=0` zet het automatische deel uit (de tests doen dat). De volledige kopie
  vóór elke migratie (`db_backups/` naast de database) blijft daarnaast bestaan.
- Zo wordt hij gemaakt: de SQLite backup API maakt één consistente momentopname (ook wat nog in het
  WAL-bestand staat), de tabellen die de app zelf opnieuw opbouwt worden in de kopie leeggemaakt
  (`file_hashes`, `media_dates`, `media_folders`, `job_results`, en elke tabel met `phash` of
  `embedding` in de naam; een nieuwe cache meldt zich met `backup.register_rebuildable(naam)`), daarna
  VACUUM, `PRAGMA integrity_check`, gzip (en teruglezen), en pas dan krijgt hij zijn naam
  (`datavault-JJJJMMDD-UUMMSS[-handmatig].db.gz`), met een harde link zodat er nooit iets wordt
  overschreven. Op een schijf zonder harde links (exFAT/FAT, een USB-stick) wordt het een nieuw
  bestand dat alleen gemaakt kan worden als de naam vrij is (`O_EXCL`), daarna weggeschreven
  (`fsync`) en met sha256 vergeleken (`backup._copy_new`). De `media`-tabel zelf blijft erin: albums
  en andere gegevens wijzen naar `media.id`. `gunzip -k` geeft een gewone SQLite-database.
- Fouten zijn Nederlandse zinnen (`backup._reason`): een back-upmap zonder schrijfrechten wordt vóór
  het kopiëren gemeld, met de map en de sleutel `backup_folder`.
- Waarom compact: met een index van 100.000 foto's is de database 109 MB; de back-up is 62 MB zonder
  de caches en 6,7 MB gecomprimeerd (gemeten: 1 s om te maken, 0,25 s om uit te pakken en te
  controleren). Elf back-ups passen zo ruim op een volle interne schijf.
- Rotatie: van elk van de laatste 7 dagen en van de laatste 4 ISO-weken blijft de nieuwste
  automatische back-up (een dag of week zonder automatische houdt zijn nieuwste handmatige), plus de
  5 nieuwste handmatige (`KEEP_MANUAL`; een tweede **Nu back-uppen** op dezelfde dag haalt de eerste
  niet weg, en een handmatige duwt de automatische van die dag er nooit uit) en de 5 nieuwste
  veiligheidskopieën van vóór een herstel. Alleen bestanden met precies die namen in de back-upmap
  worden ooit verwijderd.
- **Terugzetten** (met de app gestopt):
  ```bash
  cd code
  venv/bin/python restore_backup.py                 # lijst van back-ups, nieuwste eerst
  venv/bin/python restore_backup.py <naam of pad>   # vraagt 'ja'; --ja slaat dat over
  ```
  Draait de app met een andere config dan `code/config.json` (`DATAVAULT_CONFIG`), zet er dan
  `--config <pad>` achter; Instellingen toont de opdracht al zo (`backup.restore_command(config)`),
  zodat hij nooit bij een andere database uitkomt.
  Het script controleert de back-up (integrity_check, bekend schema), weigert zolang er een
  DataVault-server draait of de app de database open heeft (een gedeelde vergrendeling naast de
  database), bewaart de huidige database helemaal als `datavault-voor-herstel-….db.gz` en zet pas
  dan de back-up terug met de backup API (geen bestand wordt vervangen of weggegooid). Bij het
  volgende starten vult de app de lege caches vanzelf weer.

### Shows: Welke is mooier? (Glicko-2)
Hoe het werkt en waarom staat voor een buitenstaander in [docs/ELO.md](../docs/ELO.md), met de
metingen uit de simulatie. Kort:
- Twee foto's naast elkaar (`frontend/elo.js`, knop in de kop van Shows); de keuze ('a', 'b' of
  'draw') verandert beide scores in `file_ratings`, dezelfde schaal als de duimpjes (start 1000,
  duim ±50, nooit onder 100).
- Model (`backend/elo.py`, sinds migratie 160): **Glicko-2**. Per foto `mu` (score), `rd`
  (onzekerheid, 350 voor een nieuwe foto, nooit onder 30) en `vol` (volatiliteit), in `elo_ratings`.
  Na een vergelijking past elk van beide foto's Glicko-2 toe voor één partij tegen de stand van de
  ander vóór die partij (gelijk = 0,5). `file_ratings.rating` is `round(mu)`; een duim verandert
  alleen `file_ratings` en het model ziet hem als verschuiving (`mu + (rating_nu − public)`).
- Alles van één vergelijking (beide foto's, `elo_ratings`, de geschiedenisregel) gaat in één
  transactie met `BEGIN IMMEDIATE`. `elo_comparisons` bewaart per kant de score en de modelstand
  vóór en na, dus `POST /api/shows/compare/undo` zet alles precies terug (meerdere keren achter
  elkaar: nieuwste eerst) of weigert met 409 als een foto daarna nog veranderde. Een `client_token`
  (uniek) laat een dubbele POST één keer tellen. Paden in `elo_comparisons`, `elo_ratings` en
  `elo_skips` gaan mee bij verplaatsen (`media_index._carry_user_data`).
- Migratie 160 bouwt `elo_comparisons` opnieuw (CHECK met 'draw', modelkolommen, `client_token`; ids
  en de AUTOINCREMENT-teller blijven) en speelt de hele geschiedenis opnieuw af met Glicko-2
  (`elo.replay_history`). Duimen tussen twee vergelijkingen blijven precies waar ze waren. Bij een
  volgende modelwijziging: een nieuwe migratie die `replay_history` aanroept.
- Paren (`elo.choose_pairs`): de verwachte informatie van een paar is hoeveel de onzekerheid van
  beide foto's krimpt (`elo.information`); eerste foto's worden getrokken naar `rd²` (en wat extra
  voor foto's die tot de top kunnen horen), tegenstanders zijn de 14 dichtstbijzijnde in score plus
  3 willekeurige, en één paar wordt getrokken met kans ∝ informatie³. Foto's uit de laatste 8 paren
  en eerder vergeleken paren tellen veel minder; nooit het paar in beeld, een overgeslagen paar
  (30 dagen, `elo_skips`), een kopie, een reeks (`elo.mark_series`) of een bijna-duplicaat uit
  `near_dup_pairs` (stap 3.4). Slimme albums zijn een scope (`album:<id>`), naast mappen en shows.
- Routes:
  - `GET /api/shows/compare/pair?folder=|scope=&count=1..4&prev=&prev=&exclude=...` geeft `pair`,
    `pairs` (zonder overlap), `stats {photos, compared, comparisons, settled}` en `progress
    {session, today, streak, best_streak, goal, total}`.
  - `POST /api/shows/compare {a, b, winner: a|b|draw, folder|scope, client_token}`,
    `POST /api/shows/compare/skip {a, b, folder|scope}`, `POST /api/shows/compare/undo {id}`,
    `PUT /api/shows/compare/goal {goal: 10|20|50|100}`.
  - `GET /api/shows/leaderboard?folder=|scope=&year=&limit=` met per foto `rd`, `low`/`high` (95%),
    `wins/draws/losses`, `movement` (plaatsen sinds het begin van de laatste sessie; een sessie
    eindigt na 30 minuten zonder vergelijking) en `new`; plus `settled`, `years` en `session_start`.
  - `POST /api/shows/leaderboard/album {n, folder|scope, year?}` maakt een slim album 'Top N · …'
    (de filter van de collectie plus `min_rating` = de score van nummer N).
- Frontend: een wachtrij van 2 vooraf geladen (en gedecodeerde) paren; een keuze wisselt direct en
  het opslaan loopt erachteraan in een vaste keten (`elo.chain`), zodat z wacht op wat nog
  opgeslagen wordt. Een keuze binnen 250 ms na het verschijnen van een paar en een herhaalde toets
  tellen niet. Toetsen: ←/1, →/2, spatie (gelijk), s (overslaan), z (terugdraaien), Esc.
- Beweging: Web Animations API in elo.js, toestanden in de CSS-sectie `WELKE IS MOOIER?`. Het oude paar
  vertrekt als `.elo-stage.is-leaving` (absoluut, niet klikbaar) terwijl het nieuwe binnenkomt; bij
  `prefers-reduced-motion` of `html[data-motion="reduce"]` wisselt alles zonder animatie.
- Na Tab of Enter krijgt het venster `.is-keyboard` en heeft wat de focus heeft altijd een ring
  (WebKit geeft `:focus-visible` niet bij focus die met de hand verplaatst wordt). De inhoud wordt
  alleen via `eloSetBody`/`eloShowPair` vervangen, die de focus op dezelfde kant houden.
- Simulatie: `venv/bin/python tests/elo_simulation.py` (tabellen voor docs/ELO.md);
  `tests/test_elo_simulation.py` bewijst de besparing in pytest.


### DataGatherer: berichten en sociaal
- Bronnen: **WhatsApp** (WhatsApp voor Mac, een uitgepakte iPhone-`ChatStorage.sqlite`, en
  'Exporteer chat' als .txt/.zip van iOS en Android, NL en EN), **iMessage en sms** (`chat.db`),
  **Messenger** (de download uit de beveiligde opslag, `messages.zip`), **Facebook** en **Instagram**
  ('Je gegevens exporteren' in JSON). Alles komt in één winkel: `msg_conversations`,
  `msg_messages` (met FTS5-index `msg_fts`) en `social_items` (migraties 90 en 91).
- Twee keer inlezen voegt niets toe: elk bericht heeft een vaste sleutel per bron (iMessage-guid,
  WhatsApp-stanza-id, of gesprek + tijd + afzender + tekst). Een chat-export van WhatsApp wordt
  samengevoegd met de chat uit de database; berichten die de database ook heeft krijgen
  `superseded_by` en worden nooit gewist. Facebook-export en Messenger-download komen samen in één
  gesprek. Een Meta-chat heeft als sleutel het id van zijn map in de export, zodat een groep waaruit
  iemand vertrok of die een nieuwe naam kreeg één gesprek blijft; de Messenger-download wordt op
  deelnemers aan die chat gekoppeld. Meta's mojibake wordt hersteld; Nederlandse en Engelse mapnamen werken.
- **Volledige schijftoegang**: de server heeft ze niet nodig. Hij leest de database als hij mag;
  anders kopieert de kleine hulp `backend/datagatherer/fda_helper.py` (alleen standaardbibliotheek,
  start je zelf in een Terminal met Volledige schijftoegang) de database met `-wal` en `-shm` naar
  `<cache>/datagatherer-local/<bron>/`. De kaart toont 'Toegang nodig' met het pad in
  Systeeminstellingen en de exacte opdracht. DataVault leest altijd een kopie en ruimt die daarna op.
- Een gesynchroniseerde map (Dropbox, Google Drive, Koofr) voor Meta's terugkerende export stel je per
  bron in (`watch_setting`); de watcher kijkt daar altijd, ook zonder openstaande aanvraag.
- Het scherm **Berichten** (`g e`, of de knop Berichten op de DataGatherer-pagina) toont alle
  gesprekken, zoekt in alle bronnen (`/api/messages/...`) en toont bijlagen uit het archief.
- **Pasha** leest berichten alleen van bronnen waarvoor je 'Pasha mag deze berichten lezen' aanzette
  (standaard uit), en dan alleen de paar treffers die bij je vraag passen.
- Tests gebruiken alleen verzonnen databases en exports (`tests/fake_messages.py`); de
  'thuismap' met de chatdatabases is `DATAVAULT_GATHER_HOME` (in testmodus nooit je echte thuismap).
### Achtergrondtaken
- Duplicaten zoeken, vooraf indexeren, miniaturen maken, de bibliotheek bijwerken en foto's vergelijken (Beste uit reeks) lopen als taak op de achtergrond
- Het takenpaneel rechtsonder toont voortgang en resterende tijd, met Pauzeren, Hervatten en Annuleren
- Blijft bewaard na herladen of herstarten ("onderbroken", hervatbaar); bij een losgekoppelde schijf wacht een taak tot de schijf terug is

## Testen

```bash
cd code
venv/bin/python -m pytest                      # alle tests (tijdelijke testkluis, nooit je archief)
python3 tests/ui/smoke_webkit.py               # browser-rooktest in WebKit (Safari-engine)
python3 tests/ui/smoke_webkit.py chromium      # of in Chromium; --headed om mee te kijken
venv/bin/python -m pytest -m smoke             # de rooktest via pytest (in een gewone ronde overgeslagen)
```

De rooktest bouwt zelf een wegwerp-testkluis, start een eigen server op een vrije poort
(`DATAVAULT_TESTING=1`), klikt door alle tabbladen (en viewer, verplaatsvenster, chat,
thema's, Shows en Welke is mooier?, sneltoetsen, Herstelcentrum, Instellingen met een back-up,
DataGatherer, contrast in beide thema's, de schijf loskoppelen en terugplaatsen terwijl een taak loopt) en faalt bij
elke JavaScript-fout, consolefout of mislukte API-aanroep. Hij heeft een Python met Playwright nodig
(`pip install playwright && playwright install webkit`); pytest draait hem alleen met `-m smoke`
(`tests/test_smoke_browser.py`, die zoekt een Python met Playwright: `DATAVAULT_SMOKE_PYTHON`, deze Python of
`python3`). Voor de server gebruikt hij `code/venv`, of de Python in `DATAVAULT_PYTHON` (handig in een git
worktree zonder eigen venv); `DATAVAULT_TEST_PORTS=10120-10129` houdt hem binnen een poortbereik.

Op een druk bezette machine: na elke stap wacht hij zolang er nog verzoeken naar de app onderweg zijn, alle
time-outs van Playwright rekken mee met de belasting (of met `DATAVAULT_SMOKE_SLOW`, bijvoorbeeld `2`), en een
`fetch` die door herladen werd afgebroken wordt opnieuw gevraagd (alleen GET): een antwoord onder 500 staat als
'transient' in de uitvoer, al het andere blijft een fout. Elke stap toont zijn duur; de traagste staan aan het einde.

Voor schermafbeeldingen en ontwerpreviews is er een voller voorbeeldarchief:
`tests/fixtures/showcase_vault.py DIR` maakt ongeveer 190 gegenereerde foto's over 2015-2025 en een
dozijn landen (met GPS, bursts, kopieën, video, audio, Spotify en DataGatherer-bronnen), en
`--populate URL` vult het via de API van een testserver (index, reeksen, albums, stemmen, Keurer,
terug te zetten acties, een export). Start die server met `DATAVAULT_TESTING=1`,
`DATAVAULT_CONFIG=DIR_config.json`, `DATAVAULT_GATHER_HOME=DIR/../home` en
`DATAVAULT_CHROME_DIR=DIR/../Chrome`, met DIR in de tijdelijke map.

Geen enkele pytest-test praat met een echte Ollama: `tests/conftest.py` zet de chat voor elke
test op een nep-Ollama (`FakeOllama` in `tests/helpers.py`), die je per test kunt laten
uitvallen, een model kunt laten missen of een fout kunt laten sturen. De testkluis bevat ook
een echte HEIC-foto (als pillow_heif hem kan schrijven) en een gedraaide JPEG (EXIF-oriëntatie 6).

## Veiligheidsgaranties

| | |
|---|---|
| Alle metadata bewaard (datum, EXIF, xattrs) | Bestanden kopiëren dan verwijderen: NOOIT |
| Alles ongedaan te maken | Archiefbestanden buiten de kluis verplaatsen: NOOIT (exporteren kopieert alleen) |
| Pad-validatie op alle operaties | Automatisch verwijderen zonder bevestiging: NOOIT |
| Blokkering bij cross-device verplaatsingen | Media-inhoud aanpassen: NOOIT |

## Problemen oplossen

**"Vault not available"** — Controleer of T7 gemonteerd is: `ls /Volumes/T7`

**Poort 8000 al in gebruik:**
```bash
./stop_datavault.sh
```
Dit stopt alleen een DataVault-server die op poort 8000 luistert. Gebruik geen
`lsof -ti:8000 | xargs kill -9`: dat stopt ook elk programma dat *verbonden* is met poort 8000,
zoals het Safari-tabblad met de app, en `kill -9` kan de server midden in een taak afbreken.
Draait er een ander programma op poort 8000, dan stoppen `start.sh` en `stop_datavault.sh` het
niet; ze melden welk programma het is.

**Geen video thumbnails** — Installeer ffmpeg: `brew install ffmpeg`, herstart server

**App opent niet ("unidentified developer")** — Right-click op `DataVault.app` → Open → Open
