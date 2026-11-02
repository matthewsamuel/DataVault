"""
The backup folder chosen in Instellingen > Back-ups (paths.check_backup_folder,
routes/backup.py: /api/backup/folders, /folder/check, PUT /folder).

It must exist and be writable; never in the archive (media folders, TRASH, RAW_DUPLICATES),
DataVault's own folders, a system folder or the root of a disk. It is stored in app_settings,
survives a restart, a test backup lands there, and the restore script finds it.
"""
import time
from pathlib import Path

import pytest

from backend import backup


@pytest.fixture(autouse=True)
def no_other_servers(monkeypatch):
    monkeypatch.setattr(backup, "running_servers", lambda: [])


def _status(client):
    return client.get("/api/backup/status").json()


def _run(client, timeout=60):
    r = client.post("/api/backup/run")
    assert r.status_code == 202, r.text
    job = r.json()["job"]
    deadline = time.time() + timeout
    while job["status"] in ("queued", "running") and time.time() < deadline:
        time.sleep(0.05)
        job = client.get(f"/api/jobs/{job['id']}").json()
    return job


def test_a_chosen_folder_is_used_kept_after_a_restart_and_named_in_the_restore_command(client, app, config_file, tmp_path):
    own = tmp_path / "eigen back-ups"
    own.mkdir()
    before = _status(client)
    assert before["folder_chosen"] is False and before["default_folder"] == before["folder"]
    check = client.post("/api/backup/folder/check", json={"path": str(own)}).json()
    assert check["ok"] and check["path"] == str(own.resolve()) and check["error"] is None
    # (here the database is on the same disk: the check says a folder on another disk is safer)
    assert "dezelfde schijf" in check["warning"]
    r = client.put("/api/backup/folder", json={"path": str(own)})
    assert r.status_code == 200, r.text
    status = r.json()["status"]
    assert status["folder"] == str(own.resolve()) and status["folder_chosen"] is True
    assert "--map" in status["restore_command"] and str(own.resolve()) in status["restore_command"]
    # The test backup lands there; nothing went to the old folder
    job = _run(client)
    assert job["status"] == "done", job
    assert len(list(own.glob("datavault-*-handmatig.db.gz"))) == 1
    assert not Path(before["folder"]).exists() or not list(Path(before["folder"]).glob("datavault-*"))
    # Survives a restart (app_settings)
    from fastapi.testclient import TestClient
    from backend.main import create_app
    with TestClient(create_app(str(config_file))) as again:
        again.headers["X-DataVault-Token"] = again.get("/api/session").json()["token"]
        assert _status(again)["folder"] == str(own.resolve())


def test_back_to_the_folder_from_config_json(client):
    default = _status(client)["folder"]
    own = Path(default).parent / "andere map"
    own.mkdir(parents=True)
    assert client.put("/api/backup/folder", json={"path": str(own)}).status_code == 200
    r = client.put("/api/backup/folder", json={"path": None})
    assert r.status_code == 200
    assert r.json()["status"]["folder"] == default and r.json()["status"]["folder_chosen"] is False


@pytest.mark.parametrize("where", ["vault", "RAW", "library", "TRASH", "RAW_DUPLICATES", "cache", "missing", "file",
                                   "relative", "root", "volumes"])
def test_folders_that_cannot_hold_the_backups_are_refused(client, vault, where, tmp_path):
    cfg_folder = _status(client)["folder"]
    paths = {
        "vault": vault, "RAW": vault / "RAW", "library": vault / "library", "TRASH": vault / "TRASH",
        "RAW_DUPLICATES": vault / "RAW_DUPLICATES", "cache": vault / ".datavault_cache",
        "missing": tmp_path / "bestaat niet", "file": tmp_path / "een bestand",
        "relative": "back-ups", "root": "/", "volumes": "/Volumes",
    }
    (tmp_path / "een bestand").write_text("x")
    for p in ("TRASH", "RAW_DUPLICATES"):
        (vault / p).mkdir(exist_ok=True)
    path = str(paths[where])
    check = client.post("/api/backup/folder/check", json={"path": path}).json()
    assert check["ok"] is False and check["error"], check
    assert client.put("/api/backup/folder", json={"path": path}).status_code == 400
    assert _status(client)["folder"] == cfg_folder   # unchanged


def test_a_new_folder_is_made_one_level_deep_and_never_with_a_strange_name(client, tmp_path):
    parent = tmp_path / "schijf"
    parent.mkdir()
    r = client.put("/api/backup/folder", json={"path": str(parent), "create": "DataVault-backups"})
    assert r.status_code == 200, r.text
    assert (parent / "DataVault-backups").is_dir()
    assert r.json()["status"]["folder"] == str((parent / "DataVault-backups").resolve())
    for bad in ("../ontsnapt", ".verborgen", "a/b", "", ".."):
        assert client.put("/api/backup/folder", json={"path": str(parent), "create": bad}).status_code == 400, bad
    assert sorted(p.name for p in parent.iterdir()) == ["DataVault-backups"]
    assert not (tmp_path / "ontsnapt").exists()


def test_a_chosen_folder_on_a_disk_that_is_gone_is_not_made_again(client, tmp_path):
    """A chosen folder on the T7 while the T7 is not connected: the backup fails with a Dutch
    reason; no look-alike folder appears on the Mac's own disk."""
    own = tmp_path / "T7" / "DataVault-backups"
    own.mkdir(parents=True)
    assert client.put("/api/backup/folder", json={"path": str(own)}).status_code == 200
    (tmp_path / "T7").rename(tmp_path / "T7-los")
    r = client.post("/api/backup/run")
    assert r.status_code == 503 and "niet bereikbaar" in r.json()["error"]["message"]
    assert not (tmp_path / "T7").exists()
    status = _status(client)
    assert status["last_error"] and "niet bereikbaar" in status["last_error"]["message"]


def test_the_folder_browser_starts_in_the_temp_folder_in_test_mode_and_closes_the_archive(client, vault, tmp_path):
    roots = client.get("/api/backup/folders").json()["folders"]
    assert len(roots) == 1 and roots[0]["name"] == "Tijdelijke map"
    listing = client.get("/api/backup/folders", params={"path": str(vault.parent)}).json()
    by_name = {f["name"]: f for f in listing["folders"]}
    assert by_name["vault"]["allowed"] is False and by_name["vault"]["has_children"] is False
    assert "archief" in by_name["vault"]["reason"]
    # Into the archive or outside the starting points: refused
    assert client.get("/api/backup/folders", params={"path": str(vault / "RAW")}).status_code == 403
    assert client.get("/api/backup/folders", params={"path": "/Users"}).status_code == 403
    assert client.get("/api/backup/folders", params={"path": "relatief"}).status_code == 400


def test_the_restore_script_finds_the_chosen_folder_in_the_database(client, app, config_file, tmp_path, capsys):
    import restore_backup
    own = tmp_path / "eigen"
    own.mkdir()
    assert client.put("/api/backup/folder", json={"path": str(own)}).status_code == 200
    assert _run(client)["status"] == "done"
    assert restore_backup.main(["--config", str(config_file)]) == 0
    out = capsys.readouterr().out
    assert f"Back-upmap: {own.resolve()}" in out and "datavault-" in out
    # --map wins (for when the database itself cannot be read)
    other = tmp_path / "anders"
    other.mkdir()
    assert restore_backup.main(["--config", str(config_file), "--map", str(other)]) == 0
    assert f"Back-upmap: {other}" in capsys.readouterr().out


def test_map_maken_makes_the_folder_without_using_it_yet(client, tmp_path):
    parent = tmp_path / "plek"
    parent.mkdir()
    before = _status(client)["folder"]
    r = client.post("/api/backup/folder/create", json={"path": str(parent), "name": "Back-ups"})
    assert r.status_code == 200 and r.json()["ok"] and (parent / "Back-ups").is_dir()
    assert _status(client)["folder"] == before
    # Again: the folder is already there, which is fine (nothing is overwritten)
    assert client.post("/api/backup/folder/create", json={"path": str(parent), "name": "Back-ups"}).status_code == 200
    (parent / "een bestand").write_text("x")
    assert client.post("/api/backup/folder/create", json={"path": str(parent), "name": "een bestand"}).status_code == 400
    assert (parent / "een bestand").read_text() == "x"
