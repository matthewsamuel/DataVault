"""
DataVault Dashboard - de mappen van het archief herschikken volgens een plan

Zo gebruik je het (in Terminal):

    cd <map van de app>/code
    venv/bin/python reorganize_vault.py <plan.json>                    # proefrun: verandert niets
    venv/bin/python reorganize_vault.py <plan.json> --uitvoeren        # echt (vraagt eerst 'ja')

Op een tweede schijf met dezelfde opbouw (een back-up, zonder database van de app):

    venv/bin/python reorganize_vault.py <plan.json> --vault /Volumes/HDD/vault --zonder-database
    venv/bin/python reorganize_vault.py <plan.json> --vault /Volumes/HDD/vault --zonder-database --uitvoeren

Terugdraaien wat op een schijf gedaan is (in omgekeerde volgorde):

    venv/bin/python reorganize_vault.py --terug <vault>/_reorganisatie/journaal.jsonl [--uitvoeren]

Wat er gebeurt (zie backend/vault_reorg.py voor de regels):
1. Altijd eerst een proefrun op een kopie van alleen de mappen en bestandsnamen (lege
   bestanden, in een tijdelijke map) en, met database, een kopie van de database. Na de
   proefrun moet elk bestand dat de database kent op zijn nieuwe plek staan.
2. Met --uitvoeren en database: de app moet gestopt zijn (stop_datavault.sh). De database
   wordt eerst bewaard in de back-upmap (datavault-voor-reorganisatie-<tijd>.db).
3. Elke stap komt in <vault>/_reorganisatie/journaal.jsonl (exact) en LOGBOEK.md (leesbaar).
Er wordt nooit iets overschreven, samengevoegd, gekopieerd of gewist.
"""
import argparse
import fcntl
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from backend import backup, vault_reorg  # noqa: E402
from backend.config import Config  # noqa: E402

LOG_FOLDER = "_reorganisatie"


def _disk_label(root: Path) -> str:
    parts = root.parts
    return parts[2] if len(parts) > 2 and parts[1] == "Volumes" else str(root)


def _db_copy(src: Path, dst: Path) -> None:
    source = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=30)
    try:
        dest = sqlite3.connect(str(dst))
        try:
            source.backup(dest)
        finally:
            dest.close()
    finally:
        source.close()


def _missing_media(db_path: Path, root: Path) -> list:
    """Files the database shows (not hidden as missing) that are not on disk under root."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT path FROM media WHERE missing = 0").fetchall()
    finally:
        conn.close()
    prefix = str(root) + "/"
    return [p for (p,) in rows if p.startswith(prefix) and not os.path.lexists(p)]


def _report(counts, runner_vault, steps) -> bool:
    print(f"\n{counts['done']} stappen gedaan, {counts['skipped']} overgeslagen"
          + (f", {counts['db_rows']} databaseregels mee verhuisd" if counts["db_rows"] else ""))
    extra = vault_reorg.untouched_children(runner_vault, steps)
    if extra:
        print("\nMappen die het plan niet noemt (blijven waar ze staan; beslis later of ze mee moeten):")
        for parent, names in extra.items():
            print(f"  {parent}/: " + ", ".join(names))
    if counts["stopped"]:
        print(f"\nGESTOPT bij stap {counts['stopped']}: de stappen daarna zijn niet gedaan.")
        return False
    return True


def dry_run(vault, steps, db_path, label) -> bool:
    work = Path(tempfile.mkdtemp(prefix="datavault-proef-"))
    try:
        print(f"Proefrun op een kopie van de mappen in {work} ...")
        shadow = vault_reorg.mirror(vault, steps, work / "schijf")
        shadow_db = None
        if db_path is not None:
            shadow_db = work / "datavault.db"
            _db_copy(db_path, shadow_db)
            conn = sqlite3.connect(str(shadow_db), isolation_level=None)
            try:
                cols = vault_reorg.path_columns(conn, vault.root)
                conn.execute("BEGIN")
                vault_reorg.db_move(conn, cols, str(vault.root), str(shadow.root), None, None)
                conn.execute("COMMIT")
            finally:
                conn.close()
            before = _missing_media(shadow_db, shadow.root)
            print(f"Database: {len(before)} bestanden die de index kent staan nu al niet op de schijf")
        runner = vault_reorg.Runner(shadow, steps, db_path=shadow_db, label=f"proef {label}",
                                    trash_day=time.strftime("%Y-%m-%d"))
        counts = runner.run()
        ok = _report(counts, shadow, steps)
        if shadow_db is not None:
            after = _missing_media(shadow_db, shadow.root)
            print(f"Database na de proef: {len(after)} bestanden niet op hun nieuwe plek"
                  + (" (zoals ervoor)" if len(after) <= len(before) else " (MEER DAN ERVOOR)"))
            for path in after[:10]:
                print("   ", path)
            ok = ok and len(after) <= len(before)
        return ok
    finally:
        shutil.rmtree(work, ignore_errors=True)   # our own temporary folder


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Herschik de mappen van het archief volgens een plan.")
    parser.add_argument("plan", nargs="?", help="het plan (JSON)")
    parser.add_argument("--vault", help="de vault (anders die uit config.json)")
    parser.add_argument("--config", help="config.json (anders $DATAVAULT_CONFIG of code/config.json)")
    parser.add_argument("--zonder-database", action="store_true", help="alleen mappen (een schijf zonder app)")
    parser.add_argument("--terug", help="draai de stappen uit dit journaal terug")
    parser.add_argument("--uitvoeren", action="store_true", help="echt uitvoeren (anders alleen de proefrun)")
    parser.add_argument("--ja", action="store_true", help="niet om bevestiging vragen")
    args = parser.parse_args(argv)
    if bool(args.plan) == bool(args.terug):
        parser.error("geef een plan, of --terug <journaal>")

    config = Config(args.config)
    root = Path(os.path.realpath(args.vault)) if args.vault else Path(config.VAULT_ROOT)
    own = root == Path(config.VAULT_ROOT)
    if own:
        vault = vault_reorg.Vault(root, config.TRASH_FOLDER, [config.CACHE_FOLDER, config.DUPLICATES_FOLDER,
                                                              root / LOG_FOLDER])
    else:
        vault = vault_reorg.Vault(root, root / "TRASH", [root / ".datavault_cache", root / "RAW_DUPLICATES",
                                                         root / LOG_FOLDER])
    use_db = own and not args.zonder_database
    if not own and not args.zonder_database:
        print("Dit is niet de vault van de app: gebruik --zonder-database (alleen mappen).")
        return 2
    db_path = Path(config.DB_PATH) if use_db else None
    label = _disk_label(root)

    if args.terug:
        steps = vault_reorg.undo_steps(Path(args.terug), disk=label)
        if not steps:
            print(f"Het journaal heeft niets dat op {label} gedaan is.")
            return 1
        print(f"Terugdraaien op {label}: {len(steps)} verplaatsingen")
    else:
        _, steps = vault_reorg.load_plan(Path(args.plan))
        print(f"Plan: {args.plan} ({len(steps)} stappen) op {root}" + (" met database" if use_db else " zonder database"))

    if not dry_run(vault, steps, db_path, label):
        print("\nDe proefrun ging niet goed: er is niets veranderd.")
        return 1
    print("\nDe proefrun ging goed.")
    if not args.uitvoeren:
        print("Er is niets veranderd. Voeg --uitvoeren toe om het echt te doen.")
        return 0

    lock = None
    if use_db:
        servers = backup.running_servers()
        if servers:
            print("DataVault draait nog (stop de app eerst met stop_datavault.sh):\n  " + "\n  ".join(servers))
            return 1
        lock = open(backup.lock_path(config), "a")
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print("De database is nog in gebruik door een proces van DataVault: stop de app eerst.")
            return 1
    if not args.ja and input(f"\nTyp 'ja' om dit op {root} uit te voeren: ").strip().lower() != "ja":
        print("Niets veranderd.")
        return 1
    if use_db:
        folder = backup.folder_of(config)
        folder.mkdir(parents=True, exist_ok=True)
        saved = folder / f"datavault-voor-reorganisatie-{time.strftime('%Y%m%d-%H%M%S')}.db"
        _db_copy(db_path, saved)
        print(f"Database bewaard: {saved}")
        before = len(_missing_media(db_path, root))

    logs = root / LOG_FOLDER
    if args.plan and not (logs / Path(args.plan).name).exists():
        logs.mkdir(exist_ok=True)
        shutil.copy2(args.plan, logs / Path(args.plan).name)
    runner = vault_reorg.Runner(vault, steps, db_path=db_path,
                                thumbs=Path(config.THUMB_CACHE_PATH) if use_db else None,
                                journal=logs / "journaal.jsonl", log=logs / "LOGBOEK.md", label=label)
    counts = runner.run()
    ok = _report(counts, vault, steps)
    if use_db:
        after = _missing_media(db_path, root)
        print(f"Database: {len(after)} bestanden niet op hun plek (ervoor: {before})")
        ok = ok and len(after) <= before
        print("Start de app weer en laat één keer 'Bibliotheek bijwerken' met de volledige controle lopen: "
              "dan kent de index ook de nieuwe mappen van losse bestanden.")
    if lock is not None:
        lock.close()
    print(f"\nJournaal: {logs / 'journaal.jsonl'}\nLogboek:  {logs / 'LOGBOEK.md'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
