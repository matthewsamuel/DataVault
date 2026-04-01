# DataVault Dashboard - macOS App Bundle

## Wat is dit?

Een macOS applicatie bundle (`.app`) die je kunt dubbelklikken om de DataVault Dashboard te starten.

## Gebruik

### Starten
1. Dubbelklik op `DataVault.app` in de hoofdmap
2. De server start automatisch
3. Je browser opent met de dashboard

### Stoppen
Gebruik één van deze methoden:
- Sluit het browser venster en stop het proces via Activity Monitor
- Run `./stop_datavault.sh` vanuit de `code/` map in de terminal
- Gebruik `kill` met het PID dat wordt getoond in de terminal

## Structuur

```
datavault_dashboard_dev/
├── DataVault.app              # De app — dubbelklik hier om te starten
│   └── Contents/
│       ├── Info.plist         # App metadata
│       ├── MacOS/applet       # Launcher (gecompileerde AppleScript)
│       └── Resources/
│           ├── applet.icns    # App icoon
│           └── Scripts/
│               └── main.scpt  # Gecompileerd script
├── datavault_icon.png         # Icoon bronbestand
└── code/                      # Alle broncode
    ├── main.applescript       # AppleScript broncode (bewerkbaar)
    ├── backend/               # Python server
    ├── frontend/              # Web interface
    ├── venv/                  # Python omgeving
    └── config.json            # Vault configuratie
```

## Wat gebeurt er bij starten?

1. AppleScript controleert of server al draait op poort 8000
2. Activeert virtual environment vanuit `code/venv/`
3. Start FastAPI server in background
4. Wacht tot server klaar is (max 15 seconden)
5. Opent http://127.0.0.1:8000 in Safari

## App aanpassen

Bewerk `code/main.applescript` en hercompileer:
```bash
osacompile -o DataVault.app/Contents/Resources/Scripts/main.scpt code/main.applescript
```

## Troubleshooting

**"DataVault can't be opened because it is from an unidentified developer"**
- Right-click op `DataVault.app`
- Kies "Open"
- Klik "Open" in de dialoog
- Of: Systeem Preferences > Security & Privacy > General > "Open Anyway"

**Server start niet**
- Controleer of poort 8000 vrij is: `lsof -i :8000`
- Check of Python dependencies geïnstalleerd zijn: `cd code && source venv/bin/activate && pip install -r requirements.txt`

**Meerdere servers draaien**
- Run `cd code && ./stop_datavault.sh`
- Of handmatig: `kill $(lsof -ti :8000)`
