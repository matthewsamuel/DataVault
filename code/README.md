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

**Via terminal:**
```bash
cd /Volumes/T7/datavault_dashboard_dev/code
source venv/bin/activate
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Of gebruik het startscript:
```bash
cd /Volumes/T7/datavault_dashboard_dev/code
./start.sh
```

Open daarna: **http://127.0.0.1:8000**

## Configuratie

Bewerk `code/config.json` om de vault paden in te stellen:

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
- Python 3.9+
- Externe schijf T7 gemonteerd op `/Volumes/T7`
- ffmpeg (optioneel, voor video thumbnails): `brew install ffmpeg`

### Setup
```bash
cd /Volumes/T7/datavault_dashboard_dev/code
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Functies

### Media Viewer
- Mapboom bladeren (RAW_DUPLICATES en cache uitgesloten)
- Foto's en video's bekijken met thumbnails
- Volledig scherm met metadata

### RAW Duplicaten
- Scan RAW map op exacte duplicaten (SHA-256)
- Visueel raster met paden als tooltip
- Duplicaten verplaatsen naar RAW_DUPLICATES (structuur behouden)
- Volledig ongedaan maakbaar via database tracking

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
lsof -ti:8000 | xargs kill -9
```

**Geen video thumbnails** — Installeer ffmpeg: `brew install ffmpeg`, herstart server

**App opent niet ("unidentified developer")** — Right-click op `DataVault.app` → Open → Open
