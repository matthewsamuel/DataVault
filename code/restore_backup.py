"""
DataVault Dashboard - Een back-up van de database terugzetten

Zo gebruik je het (in Terminal, met de app GESTOPT):

    cd <map van de app>/code
    venv/bin/python restore_backup.py                  # toont de back-ups, nieuwste eerst
    venv/bin/python restore_backup.py <naam of pad>    # zet die back-up terug (vraagt eerst 'ja')

Opties: --config <config.json> (anders $DATAVAULT_CONFIG of code/config.json) en --ja
(niet vragen, voor scripts).

Wat er gebeurt:
1. De back-up wordt gecontroleerd (PRAGMA integrity_check, en of deze app het schema kent).
2. Draait DataVault nog, of heeft een proces de database nog open: dan stopt dit script
   en verandert het niets. Stop de app eerst (stop_datavault.sh of het menu van de app).
3. De huidige database wordt eerst helemaal bewaard als
   datavault-voor-herstel-JJJJMMDD-UUMMSS.db in de back-upmap. Er wordt nooit iets
   overschreven of weggegooid.
4. Pas dan wordt de back-up in de database teruggezet (SQLite backup API) en gecontroleerd.

Daarna start je de app weer. Een automatische back-up bevat geen tabellen die de app zelf
opnieuw maakt (de hash-cache, datums, de mappenlijst van de index, opgeslagen
scanresultaten, perceptuele hashes en beeldvectoren): die vult de app vanzelf weer.
Spijt? De veiligheidskopie uit stap 3 zet je op dezelfde manier terug.
"""
import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from backend import backup  # noqa: E402
from backend.config import Config  # noqa: E402


def _size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}".replace(".", ",")
        n /= 1024
    return str(n)


def _show_list(config) -> None:
    folder = backup.folder_of(config)
    items = backup.list_backups(folder)
    print(f"Back-upmap: {folder}")
    print(f"Database:   {config.DB_PATH}")
    if not items:
        print("Nog geen back-ups.")
        return
    for item in items:
        when = datetime.fromtimestamp(item["created_at"]).strftime("%d-%m-%Y %H:%M:%S")
        print(f"  {item['name']:<44} {when}  {_size(item['size']):>9}  {item['kind']}")
    print(f"\nTerugzetten: {backup.restore_command(config)} <naam>   (met de app gestopt)")


def _find(config, name: str) -> Path:
    given = Path(name).expanduser()
    if given.is_absolute() or given.parent != Path("."):
        return given
    return backup.folder_of(config) / name


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Zet een back-up van de DataVault-database terug.")
    parser.add_argument("backup", nargs="?", help="naam (uit de lijst) of pad van de back-up")
    parser.add_argument("--config", help="config.json (anders $DATAVAULT_CONFIG of code/config.json)")
    parser.add_argument("--ja", action="store_true", help="niet om bevestiging vragen")
    args = parser.parse_args(argv)

    config = Config(args.config)
    if not args.backup:
        _show_list(config)
        return 0

    source = _find(config, args.backup)
    try:
        version = backup.check_backup_file(source)
    except backup.BackupError as e:
        print(f"Niet teruggezet: {e}")
        return 2
    print(f"Back-up:  {source} (schema {version}, in orde)")
    print(f"Database: {config.DB_PATH}")
    print("De huidige database wordt eerst bewaard als veiligheidskopie in de back-upmap.")
    if not args.ja:
        answer = input("Typ 'ja' om deze back-up terug te zetten: ").strip().lower()
        if answer != "ja":
            print("Niets veranderd.")
            return 1
    try:
        result = backup.restore_backup(config, source, say=print)
    except backup.BackupError as e:
        print(f"Niet teruggezet: {e}")
        return 3
    print(f"Klaar: {result['restored']} is teruggezet.")
    if result["saved"]:
        print(f"De vorige database staat in {result['saved']}")
    print("Start de app nu weer.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
