"""
Herschikken van de mappen (backend/vault_reorg.py, reorganize_vault.py): hele mappen
verhuizen zonder ooit te overschrijven, de database volgt elk pad (index, Elo, slimme
albums), het journaal draait alles terug, en hetzelfde plan herhaalt zich op een tweede
schijf met meer data (zonder database).
"""
import json
import os
import shutil
import sqlite3
from pathlib import Path

import pytest

from backend import vault_reorg
from test_media_index import _index

ROME = "library/Reis Italië/rome.jpg"
VENICE = "library/Reis Italië/venetië.jpg"


def _plan(tmp_path, steps):
    path = tmp_path / "plan.json"
    path.write_text(json.dumps({"steps": steps}), encoding="utf-8")
    return vault_reorg.load_plan(path)[1]


def _runner(config, steps, tmp_path, db=True):
    vault = vault_reorg.Vault(config.VAULT_ROOT, config.TRASH_FOLDER, [config.CACHE_FOLDER, config.DUPLICATES_FOLDER])
    return vault_reorg.Runner(vault, steps, db_path=config.DB_PATH if db else None,
                              thumbs=config.THUMB_CACHE_PATH if db else None,
                              journal=tmp_path / "journaal.jsonl", log=tmp_path / "LOGBOEK.md", label="test",
                              say=lambda _: None)


def _db(config, sql, *args):
    with sqlite3.connect(str(config.DB_PATH)) as conn:
        return conn.execute(sql, args).fetchall()


def _snapshot(root: Path):
    out = {}
    for folder, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d != ".datavault_cache"]
        for name in files:
            p = Path(folder) / name
            st = os.lstat(p)
            out[str(p.relative_to(root))] = (st.st_size, st.st_mtime_ns, st.st_ino)
    return out


def test_a_folder_moves_whole_and_every_path_in_the_database_follows(client, config, vault, tmp_path):
    _index(client)
    rome, venice = (vault / ROME).resolve(), (vault / VENICE).resolve()
    assert client.post("/api/shows/compare", json={"a": str(rome), "b": str(venice), "winner": "a"}).status_code == 200
    with sqlite3.connect(str(config.DB_PATH)) as conn:
        conn.execute("INSERT INTO smart_albums (name, filters, sort, icon, position, created_at, updated_at) "
                     "VALUES ('Italië', ?, '-taken', 'fa-images', 9, 0, 0)",
                     (json.dumps({"folder": "library/Reis Italië"}),))
    before = _snapshot(vault)
    steps = _plan(tmp_path, [{"op": "move", "from": "library/Reis Italië", "to": "library/familie/2023_italie"}])
    counts = _runner(config, steps, tmp_path).run()
    assert counts["done"] == 1 and counts["stopped"] is None

    new = (vault / "library/familie/2023_italie").resolve()
    assert not (vault / "library/Reis Italië").exists() and (new / "rome.jpg").is_file()
    after = _snapshot(vault)
    # Same files, same inodes and mtimes, only the names of the folder changed
    assert sorted(after.values()) == sorted(before.values())
    old_prefix = str((vault / "library").resolve() / "Reis Italië")
    for table, col in vault_reorg.path_columns(sqlite3.connect(str(config.DB_PATH)), Path(config.VAULT_ROOT)):
        assert not _db(config, f'SELECT 1 FROM "{table}" WHERE "{col}" = ? OR "{col}" LIKE ?',
                       old_prefix, old_prefix + "/%"), (table, col)
    assert _db(config, "SELECT folder FROM media WHERE path = ?", str(new / "rome.jpg")) == [(str(new),)]
    assert _db(config, "SELECT count(*) FROM elo_ratings WHERE path LIKE ?", str(new) + "/%") == [(2,)]
    assert _db(config, "SELECT path_a, path_b FROM elo_comparisons") == [(str(new / "rome.jpg"), str(new / "venetië.jpg"))]
    assert json.loads(_db(config, "SELECT filters FROM smart_albums WHERE name = 'Italië'")[0][0]) == {
        "folder": "library/familie/2023_italie"}

    journal = [json.loads(line) for line in (tmp_path / "journaal.jsonl").read_text().splitlines()]
    assert journal[0]["moved"] == [["library/Reis Italië", "library/familie/2023_italie"]]
    assert "library/familie/2023_italie" in (tmp_path / "LOGBOEK.md").read_text()

    # The journal turns it all back
    back = _runner(config, vault_reorg.undo_steps(tmp_path / "journaal.jsonl", disk="test"), tmp_path / "terug")
    assert back.run()["done"] == 1
    assert _snapshot(vault) == before
    assert _db(config, "SELECT path_a FROM elo_comparisons") == [(str(rome),)]
    assert json.loads(_db(config, "SELECT filters FROM smart_albums WHERE name = 'Italië'")[0][0]) == {
        "folder": "library/Reis Italië"}


def test_an_existing_destination_is_never_merged_or_overwritten(config, vault, tmp_path):
    before = _snapshot(vault)
    steps = _plan(tmp_path, [{"op": "move", "from": "library/Reis Italië", "to": "library/Familie"},
                             {"op": "move", "from": "library/Reis Italië", "to": "library/reis"}])
    counts = _runner(config, steps, tmp_path, db=False).run()
    assert counts["stopped"] == 1 and counts["done"] == 0
    assert _snapshot(vault) == before


def test_loose_files_move_into_a_subfolder_and_empty_folders_go_to_the_trash(config, vault, tmp_path):
    loose = vault / "library/annick"
    loose.mkdir()
    for name in ("a.jpg", "b.jpg", ".DS_Store"):
        (loose / name).write_bytes(b"x")
    (vault / "library/leeg/sub").mkdir(parents=True)
    (vault / "library/leeg/.DS_Store").write_bytes(b"x")
    (vault / "library/vol").mkdir()
    (vault / "library/vol/blijft.jpg").write_bytes(b"x")
    steps = _plan(tmp_path, [{"op": "move_loose", "from": "library/annick", "to": "library/annick/fotos"},
                             {"op": "trash_if_empty", "path": "library/leeg"},
                             {"op": "trash_if_empty", "path": "library/vol"}])
    runner = _runner(config, steps, tmp_path, db=False)
    counts = runner.run()
    assert counts["done"] == 2 and counts["skipped"] == 1
    assert sorted(os.listdir(loose / "fotos")) == ["a.jpg", "b.jpg"] and (loose / ".DS_Store").exists()
    trashed = Path(config.TRASH_FOLDER) / f"_reorganisatie-{runner.trash_day}" / "library/leeg"
    assert (trashed / "sub").is_dir() and not (vault / "library/leeg").exists()
    assert (vault / "library/vol/blijft.jpg").is_file()


def test_a_case_only_rename_works_on_a_case_insensitive_disk(config, vault, tmp_path):
    steps = _plan(tmp_path, [{"op": "move", "from": "library/Familie", "to": "library/familie"}])
    assert _runner(config, steps, tmp_path, db=False).run()["done"] == 1
    assert "familie" in os.listdir(vault / "library") and "Familie" not in os.listdir(vault / "library")


def test_a_plan_runs_again_without_changes_and_resumes_after_a_stop(config, vault, tmp_path):
    steps = _plan(tmp_path, [{"op": "move", "from": "library/Familie", "to": "library/familie"},
                             {"op": "move", "from": "library/Reis Italië", "to": "library/familie/2023_italie"}])
    assert _runner(config, steps, tmp_path, db=False).run()["done"] == 2
    before = _snapshot(vault)
    again = _runner(config, steps, tmp_path, db=False).run()
    assert again["done"] == 0 and again["skipped"] == 2 and _snapshot(vault) == before


def test_the_same_plan_on_a_backup_disk_with_more_data_carries_the_extra_files(config, vault, tmp_path):
    other = tmp_path / "hdd" / "vault"
    shutil.copytree(vault, other, symlinks=True, ignore=shutil.ignore_patterns(".datavault_cache"))
    (other / "library/Reis Italië/alleen_op_hdd.jpg").write_bytes(b"extra")
    (other / "library/Oud feest 2001").mkdir()
    steps = _plan(tmp_path, [{"op": "move", "from": "library/Reis Italië", "to": "library/familie/2023_italie"},
                             {"op": "move", "from": "library/bestaat_niet", "to": "library/x"}])
    disk = vault_reorg.Vault(other)
    counts = vault_reorg.Runner(disk, steps, say=lambda _: None).run()
    assert counts["done"] == 1 and counts["skipped"] == 1
    assert (other / "library/familie/2023_italie/alleen_op_hdd.jpg").read_bytes() == b"extra"
    assert "Oud feest 2001" in vault_reorg.untouched_children(disk, steps)["library"]


def test_paths_outside_the_vault_the_app_folders_and_other_disks_are_refused(config, vault, tmp_path):
    with pytest.raises(vault_reorg.ReorgError):
        _plan(tmp_path, [{"op": "move", "from": "/etc", "to": "library/x"}])
    with pytest.raises(vault_reorg.ReorgError):
        _plan(tmp_path, [{"op": "move", "from": "../../x", "to": "library/x"}])
    Path(config.CACHE_FOLDER).mkdir(exist_ok=True)
    steps = _plan(tmp_path, [{"op": "move", "from": ".datavault_cache", "to": "library/cache"}])
    assert _runner(config, steps, tmp_path, db=False).run()["stopped"] == 1
    assert Path(config.CACHE_FOLDER).is_dir()


def test_the_command_line_dry_run_changes_nothing_and_then_runs(config_file, config, client, vault, tmp_path,
                                                                 monkeypatch, capsys):
    import reorganize_vault
    from backend import backup
    _index(client)
    monkeypatch.setattr(backup, "running_servers", lambda: [])
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"steps": [
        {"op": "move", "from": "library/Familie", "to": "library/familie"},
        {"op": "move", "from": "library/Reis Italië", "to": "library/familie/2023_italie"}]}))
    before = _snapshot(vault)
    assert reorganize_vault.main([str(plan), "--config", str(config_file)]) == 0
    out = capsys.readouterr().out
    assert "De proefrun ging goed" in out and "0 bestanden niet op hun nieuwe plek" in out
    assert _snapshot(vault) == before
    backup.release_app_lock(config)          # as if the app were stopped
    assert reorganize_vault.main([str(plan), "--config", str(config_file), "--uitvoeren", "--ja"]) == 0
    assert (vault / "library/familie/2023_italie/rome.jpg").is_file()
    assert (vault / "_reorganisatie/journaal.jsonl").is_file() and (vault / "_reorganisatie/plan.json").is_file()
    assert list(Path(backup.folder_of(config)).glob("datavault-voor-reorganisatie-*.db"))
