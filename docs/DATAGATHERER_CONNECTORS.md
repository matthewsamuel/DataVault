# Bron toevoegen aan DataGatherer

Een nieuwe gegevensbron is **één Python-bestand** in `code/backend/datagatherer/connectors/`.
De tab DataGatherer, de knoppen, de hulp, de instellingen, de taken en het netwerklabel bouwen
zich op uit wat dat bestand van zichzelf zegt. Er komt geen frontendcode per bron bij
(`tests/test_frontend_static.py` controleert dat `datagatherer.js` geen bron bij naam kent).

Begin met een kopie van het sjabloon:

```bash
cp code/backend/datagatherer/connectors/_template.py code/backend/datagatherer/connectors/mijnbron.py
```

Een bestand dat met `_` begint, laadt de registry niet; je kopie wel. Bij het starten controleert
`registry.validate()` elke bron. Een fout in de beschrijving stopt de server meteen, met de reden
erbij: liever luid bij de start dan een halve kaart.

## De harde regels

- **Nooit wachtwoorden.** DataVault typt en bewaart geen wachtwoord, en automatiseert geen browser.
  Een export vraagt de gebruiker zelf aan in zijn eigen browser; DataVault opent alleen de juiste
  pagina en leest het bestand daarna in.
- **Sleutels alleen in de Sleutelhanger.** Tokens en API-sleutels gaan via `ctx.secrets`
  (`keychain.py`, de bibliotheek `keyring`). Nooit in `config.json`, de database, het archief, een
  log of een API-antwoord. In testmodus is dat een sleutelhanger in het geheugen.
- **Alleen online na een klik of een schema.** Gebruik voor elk verzoek `ctx.http()`. Die client komt
  alleen bij je eigen `allowed_hosts`, via https, en alleen als de actie van de gebruiker kwam
  (trigger `user`) of het schema van de bron aan staat (trigger `schedule`). Elk verzoek, ook een
  geweigerd verzoek, staat in het netwerklog (`dg_network_log`), zonder querystring.
- **Ruwe exports blijven onaangeroerd.** `raw_store.archive_file` kopieert een export naar
  `data/<eigenaar>/<raw_folder>/<JJJJ-MM-DD> <soort>/`. Het origineel wordt nooit verplaatst,
  gewijzigd, overschreven of gewist. Dezelfde bytes (sha256) worden nooit twee keer opgeslagen.
  Bestaande mappen (`existing_folders`) worden ter plekke geregistreerd. Lees een export via
  `raw_store.iter_export_files`: die pakt niets uit in het archief en weigert een zipbom.
- **Twee keer inlezen voegt niets toe.** Geef elk record een vaste sleutel en schrijf met
  `INSERT OR IGNORE`. Een rijkere bron vervangt een armere door een markering (`superseded`),
  nooit door te wissen.
- **Een eigen tabel betekent een nieuwe migratie** in `backend/migrations.py`, met een test die
  laat zien dat alle bestaande gegevens blijven (zie `test_migrations_60_61_...`).
- **Tests maken nooit echte verbindingen** en lezen nooit echte gegevens van deze Mac. Gebruik
  `net.set_transport_factory(lambda: httpx.MockTransport(...))` en verzonnen exports met het
  echte formaat (zie `tests/fake_services.py`).

## De beschrijving

| Veld | Betekenis |
|---|---|
| `id` | `^[a-z][a-z0-9_]{1,31}$`; staat in de namen van taken (`datagatherer.sync.<id>`) en in de URL's |
| `name`, `icon`, `description` | De kaart: naam, Font Awesome-icoon (`fas fa-…` of `fab fa-…`), één Nederlandse zin |
| `category`, `order` | Groep op de tab (Muziek, Berichten, Sociaal, Activiteit, Locatie) en plaats daarin |
| `capabilities` | Een deel van `api_sync`, `local_import`, `export_request`, `export_watch` en `import` |
| `allowed_hosts` | Alleen bij `api_sync`: de exacte hostnamen die de bron mag bereiken |
| `exports` | `ExportKind`: de exportpagina (`request_url`), bestandspatronen voor Downloads, de mapnaam, hoe lang het duurt en de stappen |
| `raw_folder`, `existing_folders`, `folder_patterns` | Waar kopieën van exports komen, welke bestaande mappen in `data/<eigenaar>/` ter plekke meetellen, en naar welke bestanden hun vingerafdruk kijkt |
| `help` | `HelpOption`s, van makkelijk naar moeilijk (`effort`: `makkelijk`, `gemiddeld`, `moeilijk`) |
| `settings` | `SettingField`s. `kind='secret'` gaat naar de Sleutelhanger, de rest naar `dg_connectors.settings` |
| `setup` | Alleen bij `api_sync`: het venster Verbinden met stappen (`{redirect_uri}` wordt ingevuld), velden, uitleg en links. `oauth=True` opent daarna de toestemmingspagina (PKCE, zie `oauth.py`) |
| `record_label`, `stale_days`, `schedule_minutes`, `launchd` | Hoe een record heet, wanneer de gegevens "achter" lopen, het standaardinterval van het schema (staat standaard uit), en of de launchd-agent hem ook mag bijwerken |

Het netwerklabel volgt vanzelf: "Verbindt met … wanneer jij klikt", of "Alleen lokaal".

## Het gedrag

Implementeer wat je `capabilities` beloven:

- `detect(path, names)`: de `ExportKind`-id als het bestand een export van jouw bron is.
  `names` is de lijst bestanden in een zip. Kijk naar de inhoud: in Downloads staan veel zips.
- `find_existing(ctx)`: exportmappen die al in het archief staan. Standaard zijn dat de
  `existing_folders` onder `data/<eigenaar>/`.
- `import_raw(ctx, raw)`: lees één ruwe export (`raw['path']`: een bestand, of een geregistreerde
  map als `raw['is_folder']`). Geef `{added, seen, covers_from, covers_until}` terug. Roep tussendoor
  `ctx.check()` aan (annuleren en pauzeren) en `ctx.progress(message=...)`.
- `after_import(ctx)` (optioneel): na het inlezen, bijvoorbeeld om opnieuw te bepalen wat vervangen is.
- `sync(ctx)`: `api_sync` of `local_import`. Haal op wat nieuw is. Bewaar een cursor in dezelfde
  transactie als de records (`state.set_cursor(conn, ...)`), zodat hij nooit voorloopt.
- `is_configured(ctx)`, `authorize_url(...)`, `finish_authorization(...)`: voor een API-bron.
- `coverage(ctx)`: wat de opgeslagen gegevens dekken (`Coverage`: aantal, eerste, laatste, vanaf
  wanneer het een schatting is, `segments` (van, tot, geschat) en `holes` (van, tot) waar niets is).
  Dat wordt de datum, de achterstand en de dekkingsbalk op de kaart; een gat geeft de status
  "Gat van N dagen". Een gat is een stuk tussen eerste en laatste zonder enig gegeven.
- `detect(path, names)`: herken je export aan de inhoud (`names`: de bestanden in een zip, of `None`).
  `path.name` is altijd de echte bestandsnaam, ook bij Bestand kiezen. De watcher onthoudt het
  antwoord per bron.
- `notes(ctx)`: extra regels op de kaart, bijvoorbeeld "de koppeling verloopt over 7 dagen".
- `forget(ctx)`: "Vergeet dit account". Standaard verwijdert dit elke geheime instelling (plus
  `extra_secret_keys`) uit de Sleutelhanger. Gegevens en exports blijven in het archief.

Een fout die de gebruiker kan oplossen is een `ConnectorError` met een Nederlandse zin.
`reconnect=True` toont "Opnieuw verbinden"; `retry_after=<epoch>` laat het schema zo lang wachten.

## Wat je gratis krijgt

- Een kaart met status, gegevens tot een datum, achterstand, dekkingsbalk, de volgende stap,
  één hoofdknop (Verbinden, Bijwerken, Export aanvragen of Inlezen) en de rest onder "Meer".
- Taken in het takenpaneel: `datagatherer.sync.<id>` en `datagatherer.import.<id>`, met voortgang,
  annuleren, pauzeren, hervatten en wachten op het archief. Kopiëren uit Downloads is
  `datagatherer.watch`.
- De Downloads-watcher. Zolang er een export aangevraagd is, kijkt hij elke 30 s in `~/Downloads`
  (of in `datagatherer_watch_folders` uit de config). Hij slaat downloads over die nog bezig zijn,
  herkent een export met `detect`, kopieert hem en leest hem in. Het origineel blijft staan; de
  kaart zegt dat het weg mag.
- Een schema per bron (standaard uit) en, voor `launchd = True`, de achtergrondtaak in Instellingen.
- Instellingen per bron (velden, sleutels in de Sleutelhanger, schema, netwerklog, exports).

## Gedeelde bouwstenen (stap 3.9c)

- `activity_store`: één tabel `activity_events` voor alles wat je deed (`kind` met een Nederlands
  label in `KINDS`). Bouw records met `activity_store.event(kind, ts_ms, key(...), title, url,
  details)` en schrijf ze met `activity_store.insert(db, bron, raw_id, events)`; dezelfde sleutel
  twee keer voegt niets toe. Het scherm Activiteit toont elke bron vanzelf.
- `location_store`: `parse_bytes` herkent de vier Google-formaten aan de inhoud, `insert` schrijft
  `location_points` en `location_visits`. Na een import met nieuwe punten roept je bron
  `geotag.run(...)` aan (zie `google_timeline.after_import`).
- `export_kit`: `files(raw_path, wanted)` leest ook zips in een map of in een zip, zonder uit te
  pakken; `PASHA` is de opt-in voor de assistent (standaard uit); `watch_setting(...)` geeft je bron
  een eigen map om op te volgen (een gesynchroniseerde cloudmap), die altijd bekeken wordt, ook
  zonder openstaande aanvraag.
- Een `local_import`-bron (Chrome, Safari) leest een database op deze Mac: `is_configured` zegt of
  hij gevonden is, `sync` leest een kopie via `browser_history.snapshot` (nooit het origineel), en in
  testmodus nooit de echte mappen.
- Apple Foto's: DataVault leest de Foto's-bibliotheek niet. Wie wil, exporteert zelf met het
  open-source programma osxphotos (`osxphotos export <map in het archief> --update`, nooit
  `--cleanup`); de media-index pakt die map op zoals elke andere.

## Testen

Voeg je bron toe aan de tests: `test_every_connector_has_valid_dutch_metadata` loopt al over elke
bron. Schrijf daarnaast minstens:
- twee keer dezelfde export inlezen geeft 0 nieuwe records;
- `detect` herkent de echte export en weigert een ander bestand met een vergelijkbare naam;
- voor een API-bron: de verzoeken via een `httpx.MockTransport`, ook de foutgevallen (verlopen
  token, 429, een host buiten `allowed_hosts`).
