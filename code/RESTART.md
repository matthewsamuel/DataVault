# DataVault Dashboard - Clean Restart Guide

## Volledige Clean Restart (gebruik deze!)

Kopieer en plak dit HELE blok in je terminal:

```bash
cd /Volumes/T7/datavault_dashboard_dev/code

# Stop ALLE oude servers
killall python3 python 2>/dev/null
lsof -ti:8000 | xargs kill -9 2>/dev/null
sleep 2

# Start server
source venv/bin/activate
python -m backend.main
```

> **Let op:** De server start nu in de foreground. Je ziet logs verschijnen.

---

## Controleer of het werkt

Je zou dit moeten zien:
```
INFO:     Started server process [XXXXX]
INFO:     Waiting for application startup.
Vault root found: /Volumes/T7/vault
Database initialized: /Volumes/T7/vault/.datavault_cache/datavault.db
DataVault Dashboard started successfully
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
```

**Als je dit ziet:** Open browser → http://localhost:8000

**Als je "Address already in use" ziet:** Er draait nog een server. Doe:
```bash
lsof -ti:8000 | xargs kill -9
```
Dan probeer opnieuw.

---

## In Browser

1. Open: **http://localhost:8000**
2. Hard refresh: **`Cmd + Shift + R`** (Mac) of `Ctrl + Shift + F5` (Windows)
3. Test:
   - Viewer tab → klik folders → check thumbnails
   - RAW Duplicates → scan library → filter Videos Only → check thumbnails

---

## Server Stoppen

Druk `Ctrl + C` in de terminal waar de server draait.

---

## Problemen?

### "Address already in use"
```bash
lsof -ti:8000 | xargs kill -9
sleep 2
# Probeer opnieuw
```

### Server start niet
```bash
cd /Volumes/T7/datavault_dashboard_dev/code
source venv/bin/activate
python -m backend.main
# Kijk naar error messages
```

### Video thumbnails zijn nog placeholders
```bash
# Check ffmpeg
which ffmpeg
ffmpeg -version

# Clear cache
rm -rf /Volumes/T7/vault/.datavault_cache/thumbs/*
# Herstart server
```

---

## Snelle Commands

```bash
# Server starten (foreground)
cd /Volumes/T7/datavault_dashboard_dev/code && source venv/bin/activate && python -m backend.main

# Stop alle servers
killall python3; lsof -ti:8000 | xargs kill -9

# Check welke poort 8000 gebruikt
lsof -i:8000

# Check ffmpeg
which ffmpeg && ffmpeg -version | head -1
```
