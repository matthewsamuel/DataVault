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

Optioneel voor de back-ups van de database (zie hieronder): `"backup_folder"` (standaard
`~/Library/Application Support/DataVault/backups`), `"backup_keep_daily"` (7) en
`"backup_keep_weekly"` (4).

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

### Back-ups van de database
- `backend/backup.py` kopieert de database naar de schijf van de Mac: bij het starten als de nieuwste
  back-up ouder is dan 24 uur, daarna elk uur een controle (dus ongeveer dagelijks), en met
  **Nu back-uppen** in Instellingen (`POST /api/backup/run`; `GET /api/backup/status` geeft de stand).
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
- Rotatie: de nieuwste van elk van de laatste 7 dagen en van de laatste 4 ISO-weken blijven, plus de
  5 nieuwste handmatige (`KEEP_MANUAL`; een tweede **Nu back-uppen** op dezelfde dag haalt de eerste
  niet weg) en de 5 nieuwste veiligheidskopieën van vóór een herstel. Alleen bestanden met precies die namen in de
  back-upmap worden ooit verwijderd.
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

### Shows: Welke is mooier? (Elo)
- Twee foto's naast elkaar (`frontend/elo.js`, knop in de kop van Shows); de keuze verandert beide
  scores in `file_ratings`, dezelfde schaal als de duimpjes (start 1000, duim ±50, nooit onder 100).
- Elo (`backend/elo.py`): verwachte score `E = 1 / (1 + 10^((Rb − Ra) / 400))`, nieuwe score
  `R + round(K · (S − E))` met S = 1 of 0. K is 32 voor de eerste 30 vergelijkingen van een foto en
  daarna 16, per foto. Bij gelijke K is de verandering symmetrisch; één keuze verandert een score
  nooit meer dan K.
- Beide scores en de geschiedenisregel worden in één transactie geschreven die eerst de
  schrijfvergrendeling neemt (`BEGIN IMMEDIATE`), dus twee keuzes tegelijk (twee tabbladen) gaan na
  elkaar. `elo_comparisons` (migratie 50) bewaart beide paden, de winnaar en beide scores en aantallen
  vóór en na: `POST /api/shows/compare/undo` zet ze precies terug, of weigert (409) als een van de
  foto's daarna nog veranderde. Het aantal vergelijkingen van een foto wordt uit die geschiedenis
  geteld. Paden in de geschiedenis gaan mee bij verplaatsen (`media_index._carry_user_data`).
- Paren (`elo.choose_pair`): de eerste foto met voorkeur voor weinig vergelijkingen (gewicht
  1/(1+n)²), de tegenstander uit de 8 foto's met de dichtstbijzijnde score. Bij gelijke scores (een
  nieuwe bibliotheek: bijna alles 1000) begint het zoeken op een willekeurige plek in dat blok en
  beslist bij gelijke afstand een muntworp, anders kwamen steeds de alfabetisch eerste foto's.
  Nooit het paar dat net in beeld was of net vergeleken is, nooit dezelfde grootte (een kopie) en
  nooit twee foto's uit één reeks (`elo.mark_series`: per map op EXIF- of video-tijd gesorteerd; een
  reeks loopt door zolang elke foto binnen 2 s na de vorige komt, dus ook frame 1 en 5 van een
  burst). Bijna-duplicaten op beeld meldt een latere stap met `elo.register_near_duplicate_check(fn)`.
  RAW-bestanden doen niet mee (de browser toont ze niet). De foto's komen uit de media-index als die
  klaar is (een map die de index nog niet kent: uit een wandeling, en de index wordt bijgewerkt),
  anders uit een wandeling (60 s bewaard). Uitgeschakelde Shows-mappen doen niet mee.
- Andere collecties dan mappen (albums) haken in met `elo.register_scope('album', resolve)`, waarbij
  `resolve(waarde)` `{title, paths}` geeft (of None). De routes nemen dan `scope=album:<waarde>` in
  plaats van `folder`, en de geschiedenis bewaart `album:<waarde>` als scope. In de keuzelijst van
  het venster komen ze met `registerEloScopes(label, () => [{value: 'album:7', text}])` (elo.js).
- Routes: `GET /api/shows/compare/pair?folder=|scope=&prev=&prev=`, `POST /api/shows/compare {a, b,
  winner, folder|scope}`, `POST /api/shows/compare/undo {id}` (antwoordt ook de voortgang `stats`
  van de collectie) en `GET /api/shows/leaderboard?folder=|scope=&limit=`.
- Toetsen in het venster: een ingedrukt gehouden toets telt één keer, en zolang het volgende paar
  laadt telt geen keuze. Na het opnieuw tekenen blijft de focus op dezelfde kant (`eloSetBody`).
  Na Tab of Enter krijgt het venster `.is-keyboard`, en dan heeft wat de focus heeft altijd een ring
  (WebKit geeft `:focus-visible` niet bij focus die met de hand verplaatst wordt).

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
thema's, Shows en Welke is mooier?, sneltoetsen, Herstelcentrum, Instellingen met een back-up,
contrast in beide thema's) en faalt bij elke JavaScript-fout, consolefout of mislukte API-aanroep. Hij
heeft een Python met Playwright nodig (`pip install playwright && playwright install webkit`)
en wordt niet door pytest meegenomen. Voor de server gebruikt hij `code/venv`, of de Python in
`DATAVAULT_PYTHON` (handig in een git worktree zonder eigen venv).

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
