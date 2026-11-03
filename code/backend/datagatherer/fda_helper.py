#!/usr/bin/env python3
"""
DataVault - hulp voor Volledige schijftoegang (Full Disk Access)

macOS beschermt de berichten van Berichten (iMessage/SMS) en WhatsApp voor Mac. De DataVault-
server heeft daar geen toegang toe en hoeft die ook niet te krijgen. Deze kleine hulp start jij
zelf, in een Terminal die Volledige schijftoegang heeft
(Systeeminstellingen > Privacy en beveiliging > Volledige schijftoegang > Terminal):

    /usr/bin/python3 fda_helper.py --bron imessage --naar "<cachemap>/datagatherer-local"
    /usr/bin/python3 fda_helper.py --bron alles    --naar "<cachemap>/datagatherer-local"

De kaart in DataVault toont de exacte opdracht, met de juiste map. De hulp:
- kopieert de database met zijn -wal en -shm samen naar <naar>/<bron>/helper-<tijd>/;
- schrijft daarna ready.json, zodat DataVault alleen een volledige kopie leest;
- leest of wijzigt verder niets, wist niets en maakt geen verbinding met internet.

Alleen de standaardbibliotheek van Python: werkt met /usr/bin/python3, zonder DataVault-venv.
"""
import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

# Dezelfde lijst als LIVE_PATHS in local_db.py (een test controleert dat)
SOURCES = {
    "imessage": ("Berichten (iMessage/SMS)", "Library/Messages/chat.db"),
    "whatsapp": ("WhatsApp voor Mac", "Library/Group Containers/group.net.whatsapp.WhatsApp.shared/ChatStorage.sqlite"),
}
SIDECARS = ("-wal", "-shm")
SETTINGS = "Systeeminstellingen > Privacy en beveiliging > Volledige schijftoegang"


def copy_source(source: str, home: Path, dest: Path) -> Path:
    label, rel = SOURCES[source]
    src = home / rel
    stamp = time.strftime("%Y%m%d-%H%M%S")
    folder = dest / source / f"helper-{stamp}"
    n = 1
    while folder.exists():
        n += 1
        folder = dest / source / f"helper-{stamp}-{n}"
    folder.mkdir(parents=True)
    copied = []
    for suffix in ("",) + SIDECARS:
        part = Path(str(src) + suffix)
        if suffix and not part.exists():
            continue
        target = folder / (src.name + suffix)
        with open(part, "rb") as fin, open(target, "xb") as fout:   # 'x': nooit iets overschrijven
            shutil.copyfileobj(fin, fout, 4 * 1024 * 1024)
        copied.append(target.name)
    info = {"source": source, "copied_at": time.time(), "files": copied, "from": str(src)}
    with open(folder / "ready.json", "x", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False)
    return folder


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="fda_helper", description="Kopieert berichtendatabases voor DataVault.")
    parser.add_argument("--bron", required=True, choices=sorted(SOURCES) + ["alles"])
    parser.add_argument("--naar", required=True, help="de map datagatherer-local in de cachemap van DataVault")
    parser.add_argument("--home", help=argparse.SUPPRESS)          # tests: een verzonnen thuismap
    args = parser.parse_args(argv)
    home = Path(args.home) if args.home else Path.home()
    dest = Path(args.naar)
    if dest.name != "datagatherer-local":
        print("De doelmap moet de map 'datagatherer-local' uit de opdracht van DataVault zijn.", file=sys.stderr)
        return 2
    failed = 0
    for source in (sorted(SOURCES) if args.bron == "alles" else [args.bron]):
        label, rel = SOURCES[source]
        try:
            folder = copy_source(source, home, dest)
        except PermissionError:
            failed += 1
            print(f"{label}: geen toegang. Geef deze Terminal Volledige schijftoegang ({SETTINGS}), "
                  "sluit Terminal helemaal af en probeer het opnieuw.", file=sys.stderr)
            continue
        except FileNotFoundError:
            failed += 1
            print(f"{label}: niet gevonden op {home / rel}.", file=sys.stderr)
            continue
        print(f"{label}: gekopieerd naar {folder}. Klik in DataVault op Opnieuw controleren.")
    return 1 if failed else 0


if __name__ == "__main__":
    os.umask(0o077)      # de kopie is alleen voor jou leesbaar
    sys.exit(main())
