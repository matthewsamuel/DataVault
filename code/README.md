# DataVault Dashboard

Een lokale media viewer en RAW duplicaten opruimtool voor macOS. Draait op externe schijf T7, met absolute prioriteit voor data-veiligheid en metadata-behoud.

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
    │   ├── job_types.py   # Duplicatenscan, indexeren, miniaturen
    │   ├── semantic.py    # Slim zoeken: zoekmodel, herkennen, zoeken
    │   └── routes/        # API endpoints
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
- `1`–`9` of `g` + letter wisselt van tabblad, `/` gaat naar het zoekveld, `t` wisselt het thema, `?`
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

### Achtergrondtaken
- Duplicaten zoeken, vooraf indexeren, miniaturen maken en de bibliotheek bijwerken lopen als taak op de achtergrond
- Het takenpaneel rechtsonder toont voortgang en resterende tijd, met Pauzeren, Hervatten en Annuleren
- Blijft bewaard na herladen of herstarten ("onderbroken", hervatbaar); bij een losgekoppelde schijf wacht een taak tot de schijf terug is

## Testen

```bash
cd code
venv/bin/python -m pytest                      # alle tests (tijdelijke testkluis, nooit je archief)
python3 tests/ui/smoke_webkit.py               # browser-rooktest in WebKit (Safari-engine)
python3 tests/ui/smoke_webkit.py chromium      # of in Chromium; --headed om mee te kijken
```

De rooktest bouwt zelf een wegwerp-testkluis, start een eigen server op een vrije poort
(`DATAVAULT_TESTING=1`), klikt door alle tabbladen (en viewer, verplaatsvenster, chat,
thema's, Shows, sneltoetsen, Herstelcentrum, Instellingen, contrast in beide thema's) en faalt bij elke JavaScript-fout, consolefout of mislukte API-aanroep. Hij
heeft een Python met Playwright nodig (`pip install playwright && playwright install webkit`)
en wordt niet door pytest meegenomen.

Geen enkele pytest-test praat met een echte Ollama: `tests/conftest.py` zet de chat voor elke
test op een nep-Ollama (`FakeOllama` in `tests/helpers.py`), die je per test kunt laten
uitvallen, een model kunt laten missen of een fout kunt laten sturen. De testkluis bevat ook
een echte HEIC-foto (als pillow_heif hem kan schrijven) en een gedraaide JPEG (EXIF-oriëntatie 6).

## Veiligheidsgaranties

| | |
|---|---|
| Alle metadata bewaard (datum, EXIF, xattrs) | Bestanden kopiëren dan verwijderen: NOOIT |
| Alles ongedaan te maken | Bestanden buiten vault benaderen: NOOIT |
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
