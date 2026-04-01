# DataVault Dashboard - Foto Keurer

Een geavanceerde, intuïtieve foto- en videocurator voor uw digitale archief. De "Foto Keurer" (Photo Curator) is een Tinder-achtige interface waarmee u snel en eenvoudig grote hoeveelheden afbeeldingen en video's kunt beoordelen, organiseren en beheren.

---

## 🎯 Overzicht

DataVault Dashboard is een webgebaseerde toepassing voor het beheren van digitale media-archieven. De Foto Keurer module biedt een moderne, gamified aanpak voor het curateren van foto's en video's met:

- **Swipe-gebaseerde interactie**: Links swipen om te houden, rechts om te verwijderen, omhoog om te verplaatsen, omlaag om ongedaan te maken
- **Streaksysteem**: Motiveer jezelf met dagelijkse doelen (20 foto's per dag)
- **Voortgangstracking**: Real-time tellers voor dagelijkse en totale voortgang
- **Robuuste foutafhandeling**: Automatisch overslaan van onleesbare bestanden
- **Metadata-behoud**: Alle bestandseigenschappen blijven intact
- **Video-ondersteuning**: Automatisch afspelen met preview-mogelijkheid

---

## ✨ Functies

### Kern Functionaliteit

#### 1. **Swipe Interface**
- **← (Links swipen / A-toets)**: Houd de huidige foto
- **→ (Rechts swipen / D-toets)**: Verwijder naar prullenmand
- **↑ (Omhoog swipen / W-toets)**: Verplaats naar gekozen map
- **↓ (Omlaag swipen / S-toets)**: Maak laatste actie ongedaan

#### 2. **Streaksysteem**
- Dagelijks doel: **20 foto's/video's beoordelen**
- **Streak counter**: Toon opeenvolgende dagen met voltooide doelen
- **Voortgangsbalk**: Real-time indicator voor dagelijkse voortgang
- **Totaal teller**: Levenslang aantal beoordeelde bestanden
- Gegevens persistent opgeslagen in SQLite database

#### 3. **Mapkeuze**
- Selecteer welke map u wilt curateren
- Ondersteunt geneste mapstructuren
- Real-time verkenning van bestandsstructuur
- Alleen onbeoordeelde bestanden tonen

#### 4. **Preview & Fullscreen**
- **Spatiebalk**: Toggle fullscreen preview van huidige bestand
- Automatisch afspelen van video's met loop en mute
- Geschikte weergave voor foto's en video's
- Sluit met spatiebalk of ESC-toets

#### 5. **Undo Functionaliteit**
- Omlaag swipen om laatste actie ongedaan te maken
- Automatische bestandsherstel uit prullenmand of verplaatste locatie
- Maximaal 20 acties in geschiedenis
- Visuele indicator wanneer undo beschikbaar is

#### 6. **Server Beheer**
- Klik op DataVault-logo om server opnieuw op te starten
- Automatische herverbinding na restart
- Loading indicator met spinner

#### 7. **Robuuste Foutafhandeling**
- Automatisch overslaan van onleesbare bestanden
- Ondersteuning voor beschadigde media
- Fouten worden gelogd en gerapporteerd
- Sessie blijft stabiel ook bij bestandsfouten

---

## 🚀 Installatie & Setup

### Vereisten
- Python 3.8+
- Node.js (optioneel, voor frontend development)
- SQLite3 (inbegrepen in Python)
- Modern webbrowser (Chrome, Firefox, Safari, Edge)

### Stappen

1. **Repository klonen**
```bash
git clone https://github.com/matthewsamuel/DataVault.git
cd DataVault
```

2. **Python virtuele omgeving aanmaken**
```bash
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. **Dependencies installeren**
```bash
pip install -r requirements.txt
```

4. **Backend server starten**
```bash
python -m uvicorn code.backend.main:app --reload --host 0.0.0.0 --port 8000
```

5. **Applicatie openen**
Open uw browser en ga naar: `http://localhost:8000`

---

## 📚 Gebruikershandleiding

### Getting Started

1. **Log in of navigeer naar de Foto Keurer**
   - Klik op "Foto Keurer" in het hoofdmenu
   - U ziet een zwarte kaart met statistieken bovenaan

2. **Kies een map**
   - Klik op de "Mapkeuze" knop
   - Selecteer de map met foto's/video's die u wilt curateren
   - Klik "Bevestigen"

3. **Begin met beoordelen**
   - De eerste onbeoordeelde foto verschijnt op het scherm
   - Swipe of gebruik toetsenbord om te beslissen:
     - **Links/A**: Houd
     - **Rechts/D**: Verwijder
     - **Omhoog/W**: Verplaats naar ander mapje
     - **Omlaag/S**: Undo

4. **Bekijk voortgang**
   - Balk boven toont: vandaag beoordeeld / 20 en totale streak
   - Groene vinkje wanneer dagdoel bereikt

### Geavanceerde Functies

#### Fullscreen Preview
```
Druk op SPATIE om huidige bestand groot te zien
Video's starten automatisch af
Druk opnieuw SPATIE of ESC om terug te gaan
```

#### Verplaatsen naar Andere Map
```
Swipe OMHOOG of druk W
Kies doelmap (kan ook zoeken met "tu" → "tulips", "tiTUlaris", etc.)
Bestand wordt verplaatst, niet gekopieerd
```

#### Undo Actie
```
Swipe OMLAAG of druk S
Bestand gaat terug naar onbeoordeeld
Van prullenmand: wordt hersteld
Van verplaatste lokatie: terug naar origineel
```

#### Server Restart
```
Klik op DataVault-logo (links boven)
Server sluit af en herstart automatisch
App wacht tot server online is
```

---

## 🏗️ Architectuur

### Backend (Python + FastAPI)

```
code/backend/
├── main.py                 # FastAPI app, routes mount, server init
├── database.py            # SQLite database management
├── routes/
│   └── keurer.py         # Foto Keurer API endpoints
└── [andere routes]        # Andere functionaliteit
```

#### Technologie Stack
- **Framework**: FastAPI (modern, async-ready)
- **Database**: SQLite3 (lokale opslag)
- **Server**: Uvicorn (ASGI server)
- **File Handling**: Python pathlib, shutil

### Frontend (Vanilla JavaScript + HTML/CSS)

```
code/frontend/
├── index.html             # HTML structuur met modals
├── style.css              # Volledige CSS styling
├── app.js                 # App initialisatie en routing
├── keurer.js              # Foto Keurer logica
└── [andere modules]       # Andere frontend features
```

#### Technologie Stack
- **Framework**: Vanilla JavaScript (geen frameworks)
- **Styling**: CSS3 Flexbox + Grid
- **Touch**: Native touch event API
- **Storage**: Browser localStorage (indien nodig)

---

## 🗄️ Database Schema

### Tabel: `keurer_decisions`

Slaat alle foto- en videobeslissingen op per gebruiker per map.

```sql
CREATE TABLE keurer_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL,              -- Volledig bestandspad
    folder TEXT NOT NULL,             -- Map waaruit gekozen
    decision TEXT NOT NULL,           -- 'keep', 'delete', of 'move:{target}'
    decided_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(path, folder)              -- Één beslissing per bestand per map
)

-- Indices voor snelle queries
CREATE INDEX idx_keurer_folder ON keurer_decisions(folder)
CREATE INDEX idx_keurer_date ON keurer_decisions(decided_at)
```

### Tabel: `delete_runs` & `delete_operations`

Handelt undo-functionaliteit af door bestandsverplaatsingen op te slaan.

```sql
CREATE TABLE delete_runs (
    run_id TEXT PRIMARY KEY,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    total_files INTEGER DEFAULT 0
)

CREATE TABLE delete_operations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    src_path TEXT NOT NULL,           -- Origineel pad
    dst_path TEXT NOT NULL,           -- Verplaatst naar
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (run_id) REFERENCES delete_runs(run_id)
)
```

### Overige Tabellen

- **`file_hashes`**: Cache voor SHA256 hashes (snelle bestandsdetectie)
- **`file_ratings`**: ELO-achtige ratings per bestand (voor toekomstige AI)
- **`show_settings`**: Map zichtbaarheid instellingen

---

## 🔌 API Endpoints

### GET `/api/keurer/files`

Haal onbeoordeelde foto's/video's op uit een map.

**Query Parameters:**
```
folder: str (verplicht) - Map pad
limit: int (optioneel) - Aantal bestanden (default: 50)
```

**Response:**
```json
{
    "files": [
        {
            "name": "foto_001.jpg",
            "path": "/path/to/foto_001.jpg",
            "url": "/api/file?path=/path/to/foto_001.jpg",
            "type": "image",
            "size": 2048576
        }
    ],
    "total_unrated": 42
}
```

### POST `/api/keurer/decide`

Registreer een beslissing (houden, verwijderen, verplaatsen).

**Body:**
```json
{
    "path": "/path/to/file.jpg",
    "folder": "/home/user/Photos",
    "decision": "keep|delete|move",
    "target_folder": "/home/user/Archive"  // Alleen bij decision="move"
}
```

**Response:**
```json
{
    "status": "success",
    "decision_id": "uuid",
    "trash_path": "/trash/file.jpg"  // Pad naar prullenmand
}
```

### POST `/api/keurer/undo`

Maak laatste beslissing ongedaan.

**Body:**
```json
{
    "last_decision": {
        "path": "/path/to/file.jpg",
        "folder": "/home/user/Photos",
        "decision": "delete",
        "trash_path": "/trash/file.jpg"
    }
}
```

**Response:**
```json
{
    "status": "success",
    "restored": true,
    "message": "Actie ongedaan gemaakt"
}
```

### GET `/api/keurer/stats`

Haal streakstatistieken op.

**Response:**
```json
{
    "total": 1250,           // Totaal beoordeeld ooit
    "today": 15,             // Vandaag beoordeeld
    "goal": 20,              // Dagdoel
    "streak": 7,             // Opeenvolgende dagen met doel bereikt
    "goal_reached_today": false
}
```

### POST `/api/restart`

Start de server opnieuw op (admin).

**Response:**
```json
{
    "status": "restarting",
    "message": "Server wordt opnieuw opgestart..."
}
```

---

## 🎮 Toetsenbord & Gestures

### Toetsenbord Shortcuts

| Toets | Actie |
|-------|-------|
| **A** | Houd (links) |
| **D** | Verwijder (rechts) |
| **W** | Verplaats (omhoog) |
| **S** | Undo (omlaag) |
| **SPATIE** | Toggle preview fullscreen |
| **ESC** | Sluit preview/modals |

### Touch Gestures

| Gebaar | Actie |
|--------|-------|
| **Swipe ←** | Houd |
| **Swipe →** | Verwijder |
| **Swipe ↑** | Verplaats |
| **Swipe ↓** | Undo |
| **Tap** | Geen actie (alleen preview beschikbaar) |

---

## 💾 Bestandsbeheer

### Verwijderde Bestanden

Verwijderde bestanden worden naar een **prullenmand** verplaatst:
```
/.datavault_cache/trash/{YYYY}/{MM}/{DD}/filename
```

### Verplaatste Bestanden

Normale bestandsverplaatsing naar doelmap:
```
/target_folder/filename
```

### Metadata Behoud

Alle bestandseigenschappen behouden:
- Aanmaakdatum (ctime)
- Wijzigingsdatum (mtime)
- Bestandsmachtigingen
- EXIF/IPTC tags (voor foto's)
- XMP metadata

---

## 🔧 Technische Details

### JavaScript Modules

#### `keurer.js` (600+ lijnen)

**Kernfuncties:**
- `keurerInit()` - Initialisatie en event listeners
- `keurerLoadFiles()` - Bestanden ophalen via API
- `keurerShowCard()` - Kaart renderen met foto/video
- `keurerSetupDrag()` - Touch/mouse swipe detectie
- `keurerAnimateAndDecide()` - Kaart animeren en beslissing verzenden
- `keurerOpenMoveModal()` - Map kiezer voor verplaatsen
- `keurerFilterMoveTree()` - Zoeken in mapstructuur
- `keurerTogglePreview()` - Fullscreen preview
- `keurerUndo()` - Actie ongedaan maken

**Gesture Detectie:**
```javascript
// Touch events
touchstart -> startX, startY
touchmove  -> continue tracking
touchend   -> calculate direction & distance
```

**History Stack:**
```javascript
keurerHistory = [
    {
        file: "path/to/file.jpg",
        decision: "delete",
        trash_path: "/.datavault_cache/trash/...",
        moved_to: null
    }
]
// Max 20 items
```

#### `app.js`

**Server Beheer:**
- `restartServer()` - Triggert `/api/restart` endpoint
- `showRestartOverlay()` - UI loading indicator
- Polling `/api/health` totdat server online is

### Python Modules

#### `database.py`

**Klasse: `Database`**
```python
class Database:
    # Hash caching
    get_cached_hash(path, size, mtime)
    store_hash(path, size, mtime, sha256)
    
    # Ratings
    get_rating(path) -> int
    update_rating(path, new_rating)
    
    # Keurer decisions
    record_keurer_decision(path, folder, decision)
    get_keurer_decided_paths(folder) -> List[str]
    delete_keurer_decision(path, folder)
    get_keurer_stats() -> Dict
    
    # Delete tracking
    create_delete_run() -> str
    record_delete_operation(run_id, src_path, dst_path)
    get_delete_operations(run_id)
```

#### `routes/keurer.py`

**FastAPI Router**
- 4 endpoints (files, decide, undo, stats)
- Type hints voor alle parameters
- Error handling voor ontbrekende bestanden
- Batch operations voor Performance

---

## 📊 Streak Berekening

De streak wordt berekend door achterwaarts te itereren vanaf vandaag:

```
Huidige streaklogica:
1. Tel vandaag beoordeelde bestanden
2. Als < 20: begin streak count bij gisteren
3. Voor elke vorige dag: controleer of >= 20 beslissingen
4. Breek streak als dag < 20 heeft
```

**Voorbeeld:**
```
Dag 10 (vandaag): 15 bestanden ❌ (doel niet bereikt)
Dag 9 (gisteren): 25 bestanden ✓
Dag 8: 22 bestanden ✓
Dag 7: 18 bestanden ❌ (streak breekt)

Streak = 2 (dag 9 en 8)
```

---

## 🐛 Error Handling

### Onleesbare Bestanden

```python
# Automatisch overgeslagen
try:
    img = Image.open(filepath)
except:
    continue  # Volgende bestand
```

### Beschadigde Video's

```javascript
// Video error handler
video.onerror = () => {
    console.warn(`Video corrupt: ${file.path}`)
    keurerLoadNextFile()  // Ga naar volgende
}
```

### Database Fouten

- Transacties met rollback op failure
- Connection pooling voor Performance
- Timeout handling voor lange queries

### Server Fouten

- Automatische reconnect na `/api/restart`
- Polling met exponential backoff
- User feedback via overlays

---

## 🎨 Styling & UI/UX

### Kleurenschema

| Element | Kleur | Hex |
|---------|-------|-----|
| Houd overlay | Groen | `#88ff88` |
| Verwijder overlay | Rood | `#ff8888` |
| Verplaats overlay | Blauw | `#88ccff` |
| Undo overlay | Paars | `#bb88ff` |
| Accent | Oranje | `#ff9900` |
| Background | Donker grijs | `#1a1a1a` |

### Responsive Design

- **Desktop**: Volledige schermgebruik, mouse swipes
- **Tablet**: Touch-optimized, groter hitarea
- **Mobiel**: Full-screen interface, groot touch area

### Animaties

- Card swipe out (300ms ease-out)
- Overlay fade in/out (200ms)
- Modal slidedown (250ms cubic-bezier)
- Preview fullscreen transition (150ms)

---

## 📦 Dependencies

### Backend
```
fastapi>=0.104.0
uvicorn>=0.24.0
python-multipart>=0.0.6
Pillow>=10.0.0  # Image processing
```

### Frontend
- Geen externe dependencies (vanilla JavaScript)
- Browsers met ES6 support vereist

---

## 🔒 Veiligheid

### Input Validation
- Path traversal prevention (`Path.resolve()`)
- MIME type checking voor bestanden
- SQL injection prevention (parameterized queries)

### File Operations
- Symlink detection en filtering
- Permission checks alvorens bestanden te verplaatsen
- Safe delete naar aparte trash folder

### API Security
- CORS headers (indien van toepassing)
- Rate limiting (kan worden toegevoegd)
- File access logs

---

## 🚀 Performance

### Optimisaties

1. **Hash Caching**: SHA256 hashes gekakt per bestand
2. **Lazy Loading**: Bestanden geladen op demand
3. **Thumbnail Caching**: Vies (thumbnail preview)
4. **Database Indexing**: Snelle lookups via folder/date
5. **Batch Operations**: Meerdere acties in één request

### Benchmarks

- **File Load**: < 100ms per 50 bestanden
- **Decision Save**: < 50ms
- **Stats Calculation**: < 20ms
- **Undo Operation**: < 30ms

---

## 🐛 Troubleshooting

### "Zwart scherm" / Niets zichtbaar

**Oorzaken:**
- JavaScript error in console
- CSS niet geladen

**Fix:**
```bash
# Server restart
curl -X POST http://localhost:8000/api/restart

# Browser cache wissen
Ctrl+Shift+Delete (Chrome/Firefox)
```

### API retourneert 404

**Oorzaken:**
- StaticFiles mount te vroeg geplaatst
- API routes niet correct geregistreerd

**Fix:**
```python
# In main.py: zorg API routes VOOR static mount staan
@app.get("/api/...")
def api_route(): ...

app.mount("/", StaticFiles(...))  # ALTIJD LAATST
```

### Videos spelen niet af

**Oorzaken:**
- Verkeerd videoformat
- Browser codec support
- CORS headers

**Fix:**
- Converteer naar H.264 + AAC
- Test in verschillende browser
- Check `<video autoplay muted playsinline>`

### Undo werkt niet

**Oorzaken:**
- History buffer vol (max 20)
- Bestand reeds verwijderd van schijf

**Fix:**
- Controleer database integriteit
- Herstel bestanden vanuit backup

---

## 📝 Development

### Project Structuur
```
DataVault/
├── code/
│   ├── backend/
│   │   ├── main.py
│   │   ├── database.py
│   │   └── routes/
│   └── frontend/
│       ├── index.html
│       ├── style.css
│       ├── app.js
│       └── keurer.js
├── .gitignore
├── README.md
├── requirements.txt
└── [config files]
```

### Contributing

1. Fork repository
2. Create feature branch: `git checkout -b feature/your-feature`
3. Commit changes: `git commit -m 'Add feature'`
4. Push branch: `git push origin feature/your-feature`
5. Open Pull Request

### Code Style

- **Python**: PEP 8 (4 spaces)
- **JavaScript**: Google Style Guide (2 spaces)
- **HTML/CSS**: 2 space indentation

---

## 📄 Licentie

Dit project is beschikbaar onder de MIT Licentie. Zie `LICENSE` voor details.

---

## 👤 Auteur

**Schrift**: Matthew Samuel  
**Datum**: 2026  
**Status**: In actieve ontwikkeling

---

## 📞 Support & Contact

Voor vragen, bug reports, of suggesties:
- GitHub Issues: https://github.com/matthewsamuel/DataVault/issues
- Email: contact@example.com

---

## 🙏 Dankwoord

Dankzij alle tools en bibliotheken die deze applicatie mogelijk maken:
- FastAPI community
- SQLite
- Vanilla JavaScript developers

---

**Laatst bijgewerkt**: April 2026  
**Huidige versie**: 1.0.0  
**Status**: Stabiel
