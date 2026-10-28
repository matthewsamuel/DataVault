"""
DataGatherer - the command the optional launchd agent runs (launchd.py)

    python -m backend.datagatherer.cli sync --config /path/to/config.json

Runs a scheduled sync of every connector that may run from the agent (Connector.launchd)
and whose schedule the user switched on, without the server. With the vault (the T7)
unplugged it does nothing: the database lives there. Each sync goes through the same
network guard (trigger 'schedule') and is logged in dg_network_log like any other.
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="datagatherer")
    sub = parser.add_subparsers(dest="command", required=True)
    sync = sub.add_parser("sync", help="scheduled sync of the connectors whose schedule is on")
    sync.add_argument("--config", required=True)
    args = parser.parse_args(argv)

    from backend.config import Config
    from backend.database import Database
    from backend.datagatherer import registry, state
    from backend.datagatherer.jobs import GatherFailed, run_sync

    config = Config(args.config)
    if not config.VAULT_ROOT.is_dir() or not config.DB_PATH.is_file():
        print(f"{time.strftime('%Y-%m-%d %H:%M')} archief niet bereikbaar; niets gedaan")
        return 0
    db = Database(config.DB_PATH)
    try:
        for connector in registry.connectors():
            if not (connector.launchd and connector.has("api_sync") and state.schedule_enabled(db, connector.id)):
                continue
            st = state.get_state(db, connector.id)
            if st["needs_reconnect"] or (st["retry_after"] and time.time() < st["retry_after"]):
                continue
            try:
                result = run_sync(config, db, connector, "schedule")
                print(f"{time.strftime('%Y-%m-%d %H:%M')} {connector.id}: {result.get('added', 0)} nieuw")
            except GatherFailed as e:
                print(f"{time.strftime('%Y-%m-%d %H:%M')} {connector.id}: {e}")
    finally:
        db.close_all()
    return 0


if __name__ == "__main__":
    sys.exit(main())
