# DataVault Dashboard - Herstarten

## Normale herstart

```bash
cd /Volumes/T7/datavault_dashboard_dev/code
./start.sh
```

`start.sh` stopt alleen het proces dat op poort 8000 *luistert* (de oude DataVault-server),
start de server opnieuw met `venv/bin/python` en opent de browser uit `config.json`
(`"browser"`, standaard Safari).

## Alleen stoppen

```bash
./stop_datavault.sh
```

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
