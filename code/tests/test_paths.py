"""
One path rule for every endpoint (backend/paths.py): inside the vault after
resolving '..' and symlinks, never the app's own files, and the same 403 for
outside paths whether they exist or not.
"""
import os
import shutil
import time
import unicodedata
from datetime import datetime
from urllib.parse import quote

import pytest
from fastapi import HTTPException

from backend import paths
from backend.paths import locate, resolve_in_vault
from fixtures.vault_builder import STRAND

KOFFIE = "RAW/2020/Café Ümlaut/koffie é à ü.jpg"
SECRET = b"SECRET-OUTSIDE-VAULT"


@pytest.fixture
def outside(vault):
    """A folder next to the vault with secrets (the vault copy's symlink points at the pristine one)."""
    target = vault.parent / f"{vault.name}_outside"
    if not target.exists():
        shutil.copytree(os.path.realpath(vault / "RAW/misc/link_to_outside"), target)
    return target


# --- unit: locate / resolve_in_vault -------------------------------------------

def test_relative_and_absolute_input(config, vault):
    assert locate("RAW/2019", config) == vault / "RAW/2019"
    assert locate(str(vault / "RAW/2019"), config) == vault / "RAW/2019"
    assert locate(str(vault), config) == vault
    assert locate(str(vault / "RAW/nieuw/nog niet.jpg"), config) == vault / "RAW/nieuw/nog niet.jpg"


@pytest.mark.parametrize("bad", [
    "..",
    "../",
    "RAW/../..",
    "RAW/../../{name}_outside/secret.txt",
    "{vault}/..",
    "{vault}/../{name}_outside/secret.txt",
    "{vault}/RAW/../../{name}_outside",
    "/etc/hosts",
    "/etc",
    "/",
    "/nonexistent/whatever.jpg",
    "{vault}_outside/secret.txt",          # shares the vault's name as a prefix
    "RAW/misc/link_to_outside",             # symlink out of the vault
    "RAW/misc/link_to_outside/secret.txt",  # through that symlink
    "RAW/misc/link_to_outside/../../x",
    "RAW/x\x00.jpg",
    "",
])
def test_outside_paths_are_refused(config, vault, bad):
    bad = bad.format(vault=vault, name=vault.name)
    assert locate(bad, config) is None
    with pytest.raises(HTTPException) as e:
        resolve_in_vault(bad, config)
    assert e.value.status_code == 403
    assert e.value.detail == paths.ACCESS_DENIED


def test_non_string_input_is_a_bad_request(config):
    for bad in (None, 42, ["a"], {"p": 1}):
        with pytest.raises(HTTPException) as e:
            resolve_in_vault(bad, config)
        assert e.value.status_code == 400


def test_symlink_inside_vault_is_kept_as_entry(config, vault):
    link = vault / "RAW/misc/link.jpg"
    os.symlink(vault / STRAND, link)
    assert locate(str(link), config) == link            # route can still see (and refuse) the link
    assert locate(str(link), config).is_symlink()
    alias = vault / "RAW/alias"
    os.symlink(vault / "RAW/2019", alias)
    assert locate(str(alias), config, folder=True) == vault / "RAW/2019"  # folders: one spelling


def test_nfd_name_finds_the_nfc_file(config, vault):
    nfd = unicodedata.normalize("NFD", str(vault / KOFFIE))
    assert nfd != str(vault / KOFFIE)
    found = resolve_in_vault(nfd, config)
    assert found.is_file()
    assert unicodedata.normalize("NFC", str(found)) == str(vault / KOFFIE)
    # NFD spelling cannot be used to escape either
    sneaky = unicodedata.normalize("NFD", f"{vault}/RAW/2020/Café Ümlaut/../../../..")
    assert locate(sneaky, config) is None


def test_new_name_is_stored_as_nfc(config, vault):
    nfd = unicodedata.normalize("NFD", str(vault / "RAW/nieuw é.jpg"))
    assert str(locate(nfd, config)) == unicodedata.normalize("NFC", nfd)


def _nfd_folder(vault):
    """A folder whose name is NFD on disk (as Finder/HFS+ copies often leave it), with one photo."""
    folder = vault / unicodedata.normalize("NFD", "RAW/Été")
    folder.mkdir()
    shutil.copy2(vault / STRAND, folder / "strand.jpg")
    return folder


def test_existing_names_get_their_on_disk_spelling(config, vault):
    """R-2.1b-3: whatever Unicode form the request uses, the answer is spelled as on disk."""
    on_disk = _nfd_folder(vault)
    nfc = unicodedata.normalize("NFC", str(on_disk))
    assert nfc != str(on_disk)
    for spelling in (nfc, str(on_disk)):
        assert str(locate(spelling, config, folder=True)) == str(on_disk)
        assert str(locate(spelling + "/strand.jpg", config)) == str(on_disk / "strand.jpg")
        # A new name in that folder: folder as on disk, new name as NFC
        new = locate(spelling + "/" + unicodedata.normalize("NFD", "nieuw é.jpg"), config)
        assert str(new) == str(on_disk) + "/" + unicodedata.normalize("NFC", "nieuw é.jpg")
    # And the other way round: an NFC name on disk asked for in NFD
    nfd = unicodedata.normalize("NFD", str(vault / KOFFIE))
    assert str(locate(nfd, config)) == str(vault / KOFFIE)
    # A rename in the folder is seen at once (the listing cache follows the folder's mtime)
    os.rename(on_disk / "strand.jpg", on_disk / unicodedata.normalize("NFD", "strände.jpg"))
    found = locate(unicodedata.normalize("NFC", f"{on_disk}/strände.jpg"), config)
    assert str(found) == str(on_disk / unicodedata.normalize("NFD", "strände.jpg"))


def test_no_folder_outside_the_vault_is_ever_listed(config, vault, outside, tmp_path, monkeypatch):
    """
    R-2.1b-7: names are only re-spelled below the vault root. A request for a non-ASCII
    path outside the vault (any website can make the browser send one) must not make
    the server list folders outside the vault, only get the usual 403.
    """
    listed = []
    real_listdir = os.listdir
    monkeypatch.setattr(paths.os, "listdir", lambda folder: (listed.append(str(folder)), real_listdir(folder))[1])
    paths._NAMES_CACHE.clear()
    (outside / "Ärger").mkdir()
    entry_link = tmp_path / "into_vault"
    os.symlink(vault / "RAW", entry_link)  # outside the vault, but pointing into it
    refused = [
        "/usr/share/é/x.jpg",
        str(outside / "Ärger" / "geheim é.txt"),
        unicodedata.normalize("NFD", str(outside / "Ärger")),
        f"{vault}/../{outside.name}/Ärger/é.jpg",
        f"{vault}/RAW/2020/Café Ümlaut/../../../../{outside.name}/Ärger",
        f"RAW/misc/link_to_outside/é/x.jpg",
    ]
    for bad in refused:
        assert locate(bad, config) is None, bad
        assert locate(bad, config, folder=True) is None, bad
    # Paths that end up inside the vault are still re-spelled, below the root only
    nfd = unicodedata.normalize("NFD", f"{entry_link}/2020/Café Ümlaut/koffie é à ü.jpg")
    assert str(locate(nfd, config)) == str(vault / KOFFIE)
    nfd = unicodedata.normalize("NFD", f"{vault}/RAW/2020/Café Ümlaut/../Café Ümlaut/koffie é à ü.jpg")
    assert str(locate(nfd, config)) == str(vault / KOFFIE)
    assert listed, "the inside paths should have needed a listing"
    for folder in listed:
        assert os.path.realpath(folder) == str(vault) or os.path.realpath(folder).startswith(f"{vault}/"), folder


def test_stored_keys_do_not_depend_on_the_request_spelling(client, vault, config):
    """Keurer folders, Shows settings and ratings use the on-disk spelling for any input form."""
    on_disk = _nfd_folder(vault)
    nfc = unicodedata.normalize("NFC", str(on_disk))
    for spelling in (nfc, str(on_disk)):
        r = client.get("/api/keurer/files", params={"folder": spelling})
        assert r.status_code == 200 and r.json()["folder"] == str(on_disk)
    r = client.post("/api/shows/vote", json={"path": nfc + "/strand.jpg", "vote": "up"})
    assert r.status_code == 200
    from backend.database import Database
    assert Database(config.DB_PATH).get_all_ratings() == {str(on_disk / "strand.jpg"): 1050}
    r = client.post("/api/shows/settings", json={"path": nfc, "enabled": False})
    assert r.status_code == 200
    assert client.get("/api/shows/settings").json()["disabled_folders"] == [str(on_disk)]
    r = client.post("/api/keurer/decide", json={"path": nfc + "/strand.jpg", "folder": nfc, "decision": "keep"})
    assert r.status_code == 200, r.text
    [row] = Database(config.DB_PATH).get_recent_keurer_decisions(5)
    assert (row["path"], row["folder"]) == (str(on_disk / "strand.jpg"), str(on_disk))


def test_app_internal_files_are_refused(config, vault):
    for p in [config.CACHE_FOLDER, config.DB_PATH, config.CACHE_FOLDER / "thumbs" / "x.jpg"]:
        with pytest.raises(HTTPException) as e:
            resolve_in_vault(str(p), config)
        assert e.value.status_code == 403
        assert resolve_in_vault(str(p), config, allow_internal=True) == p


def test_app_folder_inside_vault_is_internal_but_other_dashboard_folders_are_not(config, vault, monkeypatch):
    app_dir = vault / "dashboard"
    (app_dir / "code").mkdir(parents=True)
    monkeypatch.setattr(paths, "APP_DIR", app_dir)
    with pytest.raises(HTTPException):
        resolve_in_vault(str(app_dir / "code" / "config.json"), config)
    user_folder = vault / "werk" / "dashboard"
    user_folder.mkdir(parents=True)
    assert resolve_in_vault(str(user_folder), config) == user_folder


def test_app_folder_outside_vault_changes_nothing(config, vault):
    assert paths.app_dir_in_vault(config) is None  # the repo is not inside the temp vault


# --- API: every endpoint, every traversal spelling -----------------------------

def _get_raw(client, url, name, value, encode=True):
    """Build the query string by hand so the exact encoding reaches the server."""
    return client.get(f"{url}?{name}={quote(value, safe='') if encode else value}")


def _assert_refused(r):
    assert r.status_code == 403, (r.status_code, r.text)
    assert SECRET.decode() not in r.text


def _outside_variants(vault, outside):
    rel = f"../{outside.name}/secret.txt"
    return [
        str(outside / "secret.txt"),
        f"{vault}/{rel}",
        f"{vault}/RAW/misc/link_to_outside/secret.txt",
        f"{vault}/RAW/misc/link_to_outside/secret.jpg",
        "/etc/hosts",
        "/nonexistent/secret.txt",
        str(outside / "does-not-exist.txt"),
    ]


@pytest.mark.parametrize("url", ["/api/file", "/api/thumb"])
def test_file_and_thumb_refuse_every_outside_spelling(client, vault, outside, url):
    for value in _outside_variants(vault, outside):
        _assert_refused(_get_raw(client, url, "path", value))
    # URL-encoded once more ('%2e%2e%2f') is a literal name inside the vault: not found, never served
    double = quote(quote(f"{vault}/../{outside.name}/secret.txt", safe=""), safe="")
    r = _get_raw(client, url, "path", double, encode=False)
    assert r.status_code in (403, 404) and SECRET.decode() not in r.text
    relative_double = quote(quote(f"../{outside.name}/secret.txt", safe=""), safe="")
    r = _get_raw(client, url, "path", relative_double, encode=False)
    assert r.status_code == 404 and SECRET.decode() not in r.text


def test_outside_answer_does_not_reveal_existence(client, vault, outside):
    """SECURITY-13: same status and body for an existing and a missing path outside."""
    for url, name in [("/api/file", "path"), ("/api/media", "folder"), ("/api/keurer/files", "folder"),
                      ("/api/shows/folder-content", "path"), ("/api/stats/largest-files", "folder_path")]:
        existing = _get_raw(client, url, name, str(outside))
        missing = _get_raw(client, url, name, str(outside / "nope"))
        assert existing.status_code == missing.status_code == 403, url
        assert existing.json() == missing.json(), url
    for folder in ["/etc", "/nonexistent-folder"]:
        r = client.post("/api/duplicates/scan_folder", json={"folder_path": folder})
        assert r.status_code == 403, folder
    r = client.post("/api/duplicates/delete_single", json={"file_path": "/nonexistent/x.jpg"})
    assert r.status_code == 403


def test_inside_but_missing_is_404(client, vault):
    for url, name in [("/api/file", "path"), ("/api/thumb", "path"), ("/api/media", "folder"),
                      ("/api/keurer/files", "folder"), ("/api/shows/folder-content", "path"),
                      ("/api/stats/largest-files", "folder_path"),
                      ("/api/stats/storage-breakdown-nested", "folder_path")]:
        assert _get_raw(client, url, name, str(vault / "RAW/bestaat niet")).status_code == 404, url


def test_nfd_spelling_is_served(client, vault):
    nfd = unicodedata.normalize("NFD", str(vault / KOFFIE))
    r = _get_raw(client, "/api/file", "path", nfd)
    assert r.status_code == 200
    assert r.content == (vault / KOFFIE).read_bytes()


@pytest.mark.parametrize("url", ["/api/stats/largest-files", "/api/stats/storage-breakdown-nested"])
def test_stats_folders_refuse_traversal(client, vault, outside, url):
    """SECURITY-4 / MEDIA-THUMBS-TIMELINE-3 / LIVE-PROBE-3."""
    for value in ["..", f"../{outside.name}", "RAW/misc/link_to_outside", "../../../../../../etc",
                  "/etc", str(outside), f"RAW/../../{outside.name}", f"{vault}/.."]:
        r = _get_raw(client, url, "folder_path", value)
        _assert_refused(r)
        assert "secret" not in r.text


def test_stats_folders_still_work_inside(client, vault):
    r = client.get("/api/stats/largest-files", params={"folder_path": "RAW/2019"})
    assert r.status_code == 200
    names = {f["name"] for f in r.json()["files"]}
    assert "strand 001.jpg" in names or r.json()["files"] == []  # tiny files round to 0 MB
    big = vault / "RAW/2019/groot.mov"
    big.write_bytes(b"\x01" * (3 * 1024 * 1024))
    files = client.get("/api/stats/largest-files", params={"folder_path": "RAW/2019"}).json()["files"]
    assert files[0]["name"] == "groot.mov" and files[0]["path"] == "RAW/2019/groot.mov"
    files = client.get("/api/stats/largest-files", params={"folder_path": str(vault / "RAW/2019")}).json()["files"]
    assert files[0]["path"] == "RAW/2019/groot.mov"


def test_du_measures_folders_whose_name_starts_with_a_dash(client, vault):
    """GAP-6: '-h' was read as a du option and broke the whole breakdown."""
    for name in ["-h", "Foto's", "2024"]:
        folder = vault / "RAW" / name
        folder.mkdir()
        (folder / "data.bin").write_bytes(b"\x01" * (12 * 1024 * 1024))
    r = client.get("/api/stats/storage-breakdown-nested", params={"folder_path": "RAW"})
    assert r.status_code == 200 and "error" not in r.json(), r.text
    assert {"-h", "Foto's", "2024"} <= set(r.json()["breakdown"])

    (vault / "-h").mkdir()
    (vault / "-h" / "data.bin").write_bytes(b"\x01" * (12 * 1024 * 1024))
    top = client.get("/api/stats/storage-breakdown").json()
    assert "error" not in top and "-h" in top["breakdown"]


def test_photo_on_this_day_only_searches_inside_the_vault(client, vault, outside):
    """SECURITY-5 / MEDIA-THUMBS-TIMELINE-9."""
    today = datetime.now()
    last_year = time.mktime(today.replace(year=today.year - 1, hour=12).timetuple())
    private = outside / "sub" / "prive foto.jpg"
    private.parent.mkdir()
    private.write_bytes((vault / STRAND).read_bytes())
    os.utime(private, (last_year, last_year))
    # No EXIF, so its file date counts (setting mtime earlier also moves birthtime back)
    mine = vault / "library" / "Familie" / "zonder exif.jpg"
    mine.write_bytes((vault / "RAW/2021/🎉 feest/no exif.jpg").read_bytes())
    os.utime(mine, (last_year, last_year))

    folders = [str(outside), str(outside / "sub"), f"{vault}/../{outside.name}",
               "RAW/misc/link_to_outside", str(vault / "library")]
    photos = client.get("/api/stats/photo-on-this-day", params={"allowed_folders": folders}).json()["photos"]
    assert [p["path"] for p in photos] == [str(mine)]
    only_outside = client.get("/api/stats/photo-on-this-day", params={"allowed_folders": str(outside)}).json()
    assert only_outside["photos"] == []


def test_timeline_scan_skips_outside_folders(client, vault, outside):
    r = client.get("/api/timeline/scan", params={"folders": [str(outside), f"{vault}/RAW/misc/link_to_outside", "/etc"]})
    assert r.status_code == 404
    r = client.get("/api/timeline/scan", params={"folders": [str(outside), f"{vault}/library"]})
    assert r.json()["total"] == len(list((vault / "library").rglob("*.jpg")))
    assert r.json()["skipped"] == [str(outside)]


def test_shows_settings_and_vote_only_accept_vault_paths(client, vault, outside):
    for body in [{"path": "/evil/probe", "enabled": False}, {"path": str(outside), "enabled": False}]:
        assert client.post("/api/shows/settings", json=body).status_code == 403
    assert client.get("/api/shows/settings").json()["disabled_folders"] == []
    assert client.post("/api/shows/settings", json={"path": str(vault / "library"), "enabled": "nee"}).status_code == 400
    r = client.post("/api/shows/settings", json={"path": str(vault / "library" / "Familie"), "enabled": False})
    assert r.status_code == 200
    assert client.get("/api/shows/settings").json()["disabled_folders"] == [str(vault / "library" / "Familie")]
    assert client.post("/api/shows/vote", json={"path": str(outside / "secret.jpg"), "vote": "up"}).status_code == 403
    assert client.post("/api/shows/vote", json={"path": str(vault / STRAND), "vote": "up"}).status_code == 200
    # R-2.1b-4: no rating rows for names that are not an existing regular file
    os.symlink(vault / STRAND, vault / "RAW/misc/link.jpg")
    for missing in ["RAW/misc/nonexist.jpg", str(vault / "RAW/misc/nonexist.jpg"), "RAW/misc", "RAW/misc/link.jpg"]:
        assert client.post("/api/shows/vote", json={"path": missing, "vote": "up"}).status_code == 404, missing
    from backend.database import Database
    assert list(Database(client.app.state.config.DB_PATH).get_all_ratings()) == [str(vault / STRAND)]


def test_keurer_refuses_outside_and_internal_folders(client, vault, outside, config):
    for folder in [str(outside), f"{vault}/../{outside.name}", str(vault / "RAW/misc/link_to_outside"),
                   str(config.CACHE_FOLDER), str(config.CACHE_FOLDER / "thumbs")]:
        assert client.get("/api/keurer/files", params={"folder": folder}).status_code == 403, folder
    secret = outside / "secret.jpg"
    r = client.post("/api/keurer/decide", json={"path": str(secret), "folder": str(outside), "decision": "delete"})
    assert r.status_code == 403 and secret.exists()
    via_link = vault / "RAW/misc/link_to_outside/secret.jpg"
    r = client.post("/api/keurer/decide", json={"path": str(via_link), "folder": str(via_link.parent), "decision": "delete"})
    assert r.status_code == 403 and secret.exists()


def test_open_in_finder_refuses_outside(client, vault, outside):
    for value in _outside_variants(vault, outside):
        assert client.post("/api/open-in-finder", json={"path": value}).status_code == 403


def test_move_file_refuses_traversal_spellings(client, vault, outside):
    photo = vault / STRAND
    for dest in [f"{vault}/../{outside.name}", str(vault / "RAW/misc/link_to_outside"), "/tmp"]:
        r = client.post("/api/move-file", json={"source_path": str(photo), "destination_folder": dest})
        assert r.status_code == 403, dest
    secret = outside / "secret.jpg"
    r = client.post("/api/move-file", json={"source_path": f"{vault}/../{outside.name}/secret.jpg",
                                            "destination_folder": str(vault / "library")})
    assert r.status_code == 403
    assert photo.exists() and secret.exists()


def test_app_files_are_not_servable(client, config):
    """LIVE-PROBE-6: the live database and the thumbnail cache are not served."""
    assert config.DB_PATH.exists()
    for p in [config.DB_PATH, config.CACHE_FOLDER]:
        assert _get_raw(client, "/api/file", "path", str(p)).status_code == 403
        assert _get_raw(client, "/api/thumb", "path", str(p)).status_code == 403
        assert _get_raw(client, "/api/media", "folder", str(p)).status_code == 403
    r = _get_raw(client, "/api/file", "path", ".datavault_cache/datavault.db")
    assert r.status_code == 403 and b"SQLite format" not in r.content


def test_keurer_speaks_one_spelling_of_a_folder(client, vault):
    """A folder reached through an in-vault symlink is answered (and stored) in its real spelling."""
    real = vault / "RAW/2019/zomer vakantie"
    alias = vault / "RAW/alias"
    os.symlink(real, alias)
    data = client.get("/api/keurer/files", params={"folder": str(alias)}).json()
    assert data["folder"] == str(real)
    assert all(f["path"].startswith(str(real) + "/") for f in data["files"])
    photo = data["files"][0]
    r = client.post("/api/keurer/decide", json={"path": str(alias / photo["name"]), "folder": str(alias), "decision": "keep"})
    assert r.status_code == 200, r.text
    again = client.get("/api/keurer/files", params={"folder": str(real)}).json()
    assert photo["name"] not in {f["name"] for f in again["files"]} and again["decided"] == 1


def test_symlink_loops_and_overlong_names_are_refused_not_500(client, config, vault):
    loop = vault / "RAW" / "lus"
    os.symlink(loop, loop)
    assert locate(str(loop / "x.jpg"), config) is None
    assert _get_raw(client, "/api/file", "path", str(loop / "x.jpg")).status_code == 403
    r = _get_raw(client, "/api/file", "path", str(vault / ("a" * 5000)))
    assert r.status_code in (403, 404)


@pytest.fixture
def linked_client(vault, tmp_path):
    """
    A config that names the vault through a symlink (like /var -> /private/var on macOS,
    or a vault_root that is a link to the external disk).
    """
    from fastapi.testclient import TestClient
    from backend.main import create_app
    from fixtures.vault_builder import write_config
    link = tmp_path / "vault_link"
    os.symlink(vault, link)
    app = create_app(str(write_config(link, tmp_path / "linked_config.json")))
    with TestClient(app) as c:
        c.headers["X-DataVault-Token"] = c.get("/api/session").json()["token"]
        yield c, link


def test_config_paths_are_resolved_once(vault, tmp_path):
    from backend.config import Config
    from fixtures.vault_builder import write_config
    link = tmp_path / "vault_link"
    os.symlink(vault, link)
    cfg = Config(str(write_config(link, tmp_path / "linked_config.json")))
    assert cfg.VAULT_ROOT == vault
    assert cfg.RAW_FOLDER == vault / "RAW"
    assert cfg.TRASH_FOLDER == vault / "TRASH"
    assert cfg.DUPLICATES_FOLDER == vault / "RAW_DUPLICATES"
    assert cfg.DB_PATH == vault / ".datavault_cache/datavault.db"


def test_shows_with_a_symlinked_vault_root(linked_client, vault):
    """
    R-2.1b-6: settings and votes are stored in the resolved spelling, so the Shows walk
    must use it too: a disabled folder disappears and a vote counts in the featured show.
    """
    c, link = linked_client
    shows = c.get("/api/shows/stories").json()["shows"]
    reis = [s for s in shows if s["title"] == "Reis Italië"]
    assert len(reis) == 1
    assert c.get("/api/shows/folder-content", params={"path": reis[0]["path"]}).status_code == 200
    rome = os.path.join(reis[0]["path"], "rome.jpg")  # the path the slideshow plays it under
    for _ in range(3):
        assert c.post("/api/shows/vote", json={"path": rome, "vote": "up"}).status_code == 200
    featured = c.get("/api/shows/stories").json()["featured"]["files"]
    assert [f["rating"] for f in featured if f["name"] == "rome.jpg"] == [1150]
    # Disabling the folder (by the path /stories gave, or by the link spelling) hides it
    for spelling in (reis[0]["path"], str(link / "library/Reis Italië")):
        assert c.post("/api/shows/settings", json={"path": spelling, "enabled": False}).status_code == 200
        stories = c.get("/api/shows/stories").json()
        assert not [s for s in stories["shows"] if s["title"] == "Reis Italië"]
        assert not [f for f in stories["featured"]["files"] if "Reis" in f["path"]]
        assert c.post("/api/shows/settings", json={"path": spelling, "enabled": True}).status_code == 200
        assert [s for s in c.get("/api/shows/stories").json()["shows"] if s["title"] == "Reis Italië"]


def test_shows_library_symlinked_out_of_the_vault_is_not_walked(client, vault, outside):
    shutil.move(str(vault / "library"), str(vault / "library_real"))
    os.symlink(outside, vault / "library")
    stories = client.get("/api/shows/stories").json()
    assert stories["shows"] == [] and stories["featured"] is None


def test_storage_overview_keeps_folders_and_files_under_5_mb(client, vault):
    """A folder of a few MB rounded to 0.00 GB and dropped out of the overview (Nog open, fase 2)."""
    small = vault / "RAW" / "klein"
    small.mkdir()
    (small / "a.bin").write_bytes(b"\x01" * (300 * 1024))
    data = client.get("/api/stats/storage-breakdown-nested", params={"folder_path": "RAW"}).json()
    assert 300 * 1024 <= data["sizes"]["klein"] < 400 * 1024
    assert data["breakdown"]["klein"] == 0.0          # still there for older callers
    assert set(data["sizes"]) == set(data["breakdown"])
    assert {"2019", "misc"} <= set(data["sizes"])     # the tiny test folders too
    top = client.get("/api/stats/storage-breakdown").json()
    assert "RAW" in top["sizes"] and top["sizes"]["RAW"] > 0

    files = client.get("/api/stats/largest-files", params={"folder_path": "RAW/klein"}).json()["files"]
    assert [(f["name"], f["size"]) for f in files] == [("a.bin", 300 * 1024)]
    tiny = small / "b.txt"
    tiny.write_bytes(b"x" * 10)
    files = client.get("/api/stats/largest-files", params={"folder_path": "RAW/klein"}).json()["files"]
    assert [f["name"] for f in files] == ["a.bin", "b.txt"]   # 10 bytes no longer rounds away


def test_nested_breakdown_counts_the_files_in_the_folder_itself(client, vault):
    """A folder with only photos (no subfolders) totalled 0 B in Opslag per map."""
    folder = vault / "losse-fotos"
    folder.mkdir()
    (folder / "a.jpg").write_bytes(b"\x01" * 3000)
    (folder / "b.jpg").write_bytes(b"\x01" * 2000)
    (folder / ".verborgen").write_bytes(b"\x01" * 9000)
    (folder / "sub").mkdir()
    (folder / "sub" / "c.jpg").write_bytes(b"\x01" * 4000)
    body = client.get("/api/stats/storage-breakdown-nested", params={"folder_path": "losse-fotos"}).json()
    assert body["files_count"] == 2 and body["files_size"] == 5000
    assert set(body["sizes"]) == {"sub"}
