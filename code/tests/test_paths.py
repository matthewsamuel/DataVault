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
        r = client.post("/api/duplicates/scan_folder", params={"folder_path": folder})
        assert r.status_code == 403, folder
    r = client.post("/api/duplicates/delete_single", params={"file_path": "/nonexistent/x.jpg"})
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
    mine = vault / "library" / "Familie" / "opa.jpg"
    os.utime(mine, (last_year, last_year))

    folders = ",".join([str(outside), str(outside / "sub"), f"{vault}/../{outside.name}",
                        "RAW/misc/link_to_outside", str(vault / "library")])
    photos = client.get("/api/stats/photo-on-this-day", params={"allowed_folders": folders}).json()["photos"]
    assert [p["path"] for p in photos] == [str(mine)]
    only_outside = client.get("/api/stats/photo-on-this-day", params={"allowed_folders": str(outside)}).json()
    assert only_outside["photos"] == []


def test_timeline_scan_skips_outside_folders(client, vault, outside):
    r = client.get("/api/timeline/scan", params={"folders": f"{outside},{vault}/RAW/misc/link_to_outside,/etc"})
    assert r.status_code == 200 and r.json()["total"] == 0
    r = client.get("/api/timeline/scan", params={"folders": f"{outside},{vault}/library"})
    assert r.json()["total"] == 3


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
