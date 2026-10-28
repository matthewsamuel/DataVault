# DataVault Dashboard - Herstarten

## Normale herstart

```bash
cd /Volumes/T7/datavault_dashboard_dev/code
./start.sh
```

`start.sh` stopt alleen het proces dat op poort 8000 *luistert* (de oude DataVault-server),
start de server opnieuw met `venv/bin/python` en opent de browser uit `config.json`
(`"browser"`, standaard Safari).

## Herstarten vanuit de app

Klik op het **DataVault**-logo linksboven en bevestig. Alleen deze server wordt herstart:
een hulpproces wacht tot de server gestopt is en start hem daarna opnieuw met dezelfde Python,
dezelfde argumenten (ook de poort), dezelfde werkmap en dezelfde omgevingsvariabelen. Er wordt
niets op poort 8000 (of elders) afgeschoten. De pagina wacht tot de nieuwe server antwoordt en
toont na 30 seconden een melding als hij niet terugkomt; start hem dan met `./start.sh`.
Draait de server met `uvicorn --reload`, dan weigert de app te herstarten (de reloader doet dat
al bij elke wijziging).

Na een herstart heeft de server een nieuw sessietoken; de pagina haalt dat zelf op.

Loopt er tijdens de herstart nog een lange opdracht (bijv. een tijdlijnscan), dan wacht de oude
server daar niet eindeloos op: na hoogstens 15 seconden stopt hij toch (nooit midden in het
verplaatsen van een bestand) en komt de nieuwe server op.

Na een herstart vanuit de app draait de server los van het Terminal-venster van `start.sh`:
- **Uitvoer (log):** die staat niet meer in dat Terminal-venster maar in
  `~/Library/Logs/DataVault/server.log` (te openen met de app Console, of
  `tail -f ~/Library/Logs/DataVault/server.log`). Een ander bestand kies je met de
  omgevingsvariabele `DATAVAULT_LOG_FILE`. Boven 10 MB wordt het oude log `server.log.1`.
  Gestart via de DataVault-app blijft de uitvoer gewoon in `/tmp/datavault_server.log`.
- **Stoppen:** Ctrl+C in het Terminal-venster stopt de herstarte server niet meer (dat venster
  is alweer terug bij de prompt). Stop hem met `./stop_datavault.sh` (zie hieronder).

## Alleen stoppen

```bash
./stop_datavault.sh
```

Dit werkt ook voor een server die vanuit de app is herstart: het stopt het proces dat op poort
8000 luistert.

## Controleren of het werkt

```bash
curl http://127.0.0.1:8000/api/health
```

`"vault_available": true` betekent dat de T7 gekoppeld is en de app het archief ziet.

## "Address already in use"

Er luistert nog iets op poort 8000:

```bash
lsof -i :8000 -sTCP:LISTEN
```

Is dat geen DataVault-server, sluit dan dat programma; `start.sh` stopt alleen de luisterende
server en nooit willekeurige Python-processen of browsers.

## venv opnieuw opbouwen

```bash
rm -rf venv
python3.12 -m venv venv
venv/bin/pip install -r requirements.txt        # app
venv/bin/pip install -r requirements-dev.txt    # app + tests
```
