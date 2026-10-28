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
    │   ├── job_types.py   # Duplicatenscan, indexeren, miniaturen, foto's vergelijken
    │   ├── near_dups.py   # Bijna-duplicaten en bursts (Beste uit reeks)
    │   └── routes/        # API endpoints
    ├── frontend/          # HTML/CSS/JS interface
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
  4 threads). Paren en bursts voor 100.000 foto's: ~5 s bij willekeurige hashes, 7-10 s als veel
  foto's op elkaar lijkende hashes hebben (scans, screenshots; gemeten met 100.000 gemaakte hashes),
  alleen als er iets nieuw of veranderd is; een pagina reeksen daarna in enkele ms. Het geheugen
  blijft begrensd: de kandidaten worden per ~2 miljoen gecontroleerd (`CANDIDATE_BUDGET`), dubbele
  paren tussendoor weggegooid (piek ~0,5-0,8 GB in dat scheve geval, was 1,7-2,4 GB). Werkt ook met
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
- Duplicaten zoeken, vooraf indexeren, miniaturen maken, de bibliotheek bijwerken en foto's vergelijken (Beste uit reeks) lopen als taak op de achtergrond
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
