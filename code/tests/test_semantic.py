"""
Slim zoeken (backend/semantic.py, routes/search.py): the embeddings table and its
migration, the 'semantic' and 'model_download' jobs, the search API with the library
filters, and a check of the real model when it is installed.

Most tests use FakeEncoder: an 'embedding' made from a photo's average colour, and
colour words as queries, so the ranking logic is tested without the 1 GB model.
"""
import hashlib
import io
import json
import os
import shutil
import sqlite3
import time
from pathlib import Path

import numpy as np
import pytest

from fixtures.vault_builder import BURST, STRAND, STRAND_COPIES, _photo

DIM = 768
COLOURS = {
    "blauw": (30, 120, 200),      # the strand photo and its copies
    "oranje": (200, 120, 30),     # zonsondergang
    "roze": (250, 200, 220),      # taart
    "groen": (10, 200, 10),       # no exif
    "paars": (100, 50, 150),      # the burst
}


def _colour_vector(rgb):
    v = np.zeros(DIM, dtype=np.float32)
    v[:3] = np.asarray(rgb, dtype=np.float32) / 255.0 - 0.45
    v[3] = 0.05
    return v / np.linalg.norm(v)


class FakeEncoder:
    """The Encoder interface, without onnxruntime: colours in, colour words out."""

    def __init__(self, *args):
        self.images = 0
        self.texts = []
        self.on_image = None

    def load_image(self, device="auto"):
        return "cpu"

    def unload_image(self):
        pass

    def load_text(self):
        pass

    def preprocess(self, img):
        return np.asarray(img.convert("RGB"), dtype=np.float32).reshape(-1, 3).mean(axis=0)

    def encode_image(self, pixels):
        self.images += 1
        if self.on_image:
            self.on_image(self.images)
        return _colour_vector(pixels)

    def encode_text(self, text):
        self.texts.append(text)
        for word, rgb in COLOURS.items():
            if word in text.lower():
                return _colour_vector(rgb)
        return _colour_vector((128, 128, 128))


@pytest.fixture
def fake(monkeypatch):
    from backend import semantic
    encoder = FakeEncoder()
    semantic.set_encoder_factory(lambda config, spec: encoder)
    semantic.vector_index().reset()
    yield encoder
    semantic.set_encoder_factory(None)
    semantic.vector_index().reset()


def _manager():
    from backend.routes.jobs import get_manager
    return get_manager()


def _job(client, job_type, params=None, expect="done"):
    r = client.post("/api/jobs", json={"type": job_type, "params": params or {}})
    assert r.status_code in (200, 201), r.text
    job = _manager().wait(r.json()["job"]["id"], timeout=60)
    assert job["status"] == expect, job
    if expect != "done":
        return job
    return client.get(f"/api/jobs/{job['id']}/result").json()["result"]


def _index(client):
    return _job(client, "index", {"full": True})


def _embed(client):
    return _job(client, "semantic")


def _names(answer):
    return [item["name"] for item in answer["items"]]


# ---------- migration ----------

def test_migration_40_adds_the_embeddings_table_and_keeps_every_user_row(tmp_path, monkeypatch):
    from backend import migrations
    from backend.database import Database
    from test_duplicates_runs import _migrate_to
    from test_foundation import _make_legacy_db
    db_path = tmp_path / "datavault.db"
    _make_legacy_db(db_path)
    _migrate_to(db_path, 12, monkeypatch)
    now = time.time()
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("INSERT INTO keurer_decisions (path, folder, decision, dest_path, dest_ino, dest_size) "
                     "VALUES ('/v/RAW/weg.jpg', '/v/RAW', 'delete', '/v/TRASH/weg.jpg', 77, 1234)")
        conn.execute("INSERT INTO app_settings VALUES ('chat_model', '\"qwen2.5:3b\"', 5.0)")
        conn.execute("INSERT INTO jobs (type, params, status, processed, created_at, updated_at, checkpoint) "
                     "VALUES ('index', '{\"full\": true}', 'paused', 12, ?, ?, '{\"folder\": 3}')", (now, now))
        conn.execute("INSERT INTO media (path, folder, name, kind, ext, size, mtime_ns, added_at, indexed_at, seen_at) "
                     "VALUES ('/v/RAW/a.jpg', '/v/RAW', 'a.jpg', 'photo', '.jpg', 10, 1, 1, 1, 1)")
        conn.execute("INSERT INTO media_folders VALUES ('/v/RAW', '/v', 1, 1.0)")
        conn.execute("INSERT INTO recovery_actions (kind, title, folder, details, created_at) "
                     "VALUES ('viewer_move', 'x', '/v/RAW', '{}', 1.0)")
        conn.execute("INSERT INTO recovery_moves (action_id, src_path, dst_path, dst_ino, dst_size, moved_at) "
                     "VALUES (1, '/v/RAW/b.jpg', '/v/RAW/c/b.jpg', 5, 6, 1.0)")
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name != 'schema_version'")]
        before = {t: conn.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall() for t in tables}
    assert before["sqlite_sequence"] and before["jobs"] and before["media"] and before["recovery_moves"]

    db = Database(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        assert migrations.get_schema_version(conn) == migrations.LATEST_VERSION >= 40
        for table, rows in before.items():
            assert conn.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall() == rows, table
        assert {r[1] for r in conn.execute("PRAGMA table_info(semantic_embeddings)")} == {
            "media_id", "model", "dim", "vector", "status", "size", "mtime_ns", "source", "created_at"}
        indexes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
        assert "idx_semantic_model" in indexes
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert len(list((tmp_path / migrations.BACKUP_DIR_NAME).glob("datavault-v12-*.db"))) == 1
    assert db.get_rating("/v/RAW/a.jpg") == 1150
    assert db.get_keurer_stats()["streak"] == 3
    assert db.get_delete_operations("run-1")[0]["dst_path"] == "/v/RAW_DUPLICATES/b.jpg"
    assert db.get_recent_keurer_decisions(1)[0]["dest_ino"] == 77
    assert db.get_setting("chat_model") == "qwen2.5:3b"
    assert db.get_recovery_moves(1)[0]["dst_ino"] == 5
    db.close_all()


def test_migrations_stay_sorted_and_unique():
    from backend import migrations
    versions = [m[0] for m in migrations.MIGRATIONS]
    assert versions == sorted(versions) and len(set(versions)) == len(versions)


# ---------- without the model ----------

def test_without_the_model_search_explains_how_to_install_it_and_nothing_else_breaks(client, tmp_path, monkeypatch):
    from backend.routes import search
    monkeypatch.setattr(search.config, "MODELS_FOLDER", tmp_path / "geen-modellen")
    _index(client)
    status = client.get("/api/search/status").json()
    assert status["ready"] is False and status["model"]["installed"] is False
    assert status["model"]["size"] > 900_000_000
    assert all(src.startswith("https://huggingface.co/") for src in status["model"]["sources"])
    assert status["total"] > 10 and status["pending"] == status["total"]
    r = client.get("/api/search/semantic", params={"q": "hond op het strand"})
    assert r.status_code == 409
    assert "niet geïnstalleerd" in r.json()["error"]["message"] and "Instellingen" in r.json()["error"]["message"]
    r = client.post("/api/jobs", json={"type": "semantic", "params": {}})
    assert r.status_code == 409
    # Searching by file name works without the model, and so does the rest of the library
    found = client.get("/api/search/filename", params={"q": "taart"}).json()
    assert _names(found) == ["🎂 taart.png"]
    assert client.get("/api/library/media").status_code == 200
    assert client.post("/api/search/warm").json() == {"ready": False}


def test_the_app_never_downloads_by_itself(client, config, monkeypatch):
    """No route or start-up path fetches the model: only the model_download job does."""
    from backend import semantic
    calls = []
    monkeypatch.setattr(semantic, "_open_url", lambda url, start: calls.append(url))
    _index(client)
    client.get("/api/search/status")
    client.get("/api/search/semantic", params={"q": "x"})
    client.post("/api/search/warm")
    assert calls == []


# ---------- the embedding job and search, with the fake encoder ----------

def test_photos_are_recognised_and_found_by_text(client, config, vault, fake):
    _index(client)
    result = _embed(client)
    status = client.get("/api/search/status").json()
    # corrupt.jpg, empty.jpg, fake.heic and kapot.mp4 cannot be read as images
    assert result["undecodable"] == 4 and result["errors"] == 0 and result["complete"]
    assert result["embedded"] == status["embedded"] == status["total"] - 4
    assert status["pending"] == 0 and status["undecodable"] == 4
    assert result["made_thumbs"] == result["embedded"]   # the thumbnails were made on the way
    answer = client.get("/api/search/semantic", params={"q": "iets blauws"}).json()
    strand_names = {Path(STRAND).name, "strand 001 kopie.jpg", "dup van strand.jpg", "strand klein.jpg"}
    assert set(_names(answer)[:4]) == strand_names
    assert answer["items"][0]["score"] >= answer["items"][-1]["score"]
    assert answer["total"] >= 4 and answer["indexed"] == result["embedded"] and answer["pending"] == 0
    first = answer["items"][0]
    assert {"id", "path", "name", "kind", "taken", "score"} <= set(first)
    assert client.get("/api/search/semantic", params={"q": "oranje"}).json()["items"][0]["name"] == "IMG_0002.JPG"
    # The second run has nothing left to do
    assert _embed(client)["files"] == 0


def test_search_combines_with_the_library_filters_and_pages(client, config, vault, fake):
    _index(client)
    _embed(client)
    videos = client.get("/api/search/semantic", params={"q": "blauw", "kind": "video", "include_weak": True}).json()
    assert videos["items"] and {i["kind"] for i in videos["items"]} == {"video"}
    old = client.get("/api/search/semantic", params={"q": "blauw", "date_to": "2019-12-31",
                                                     "include_weak": True, "limit": 200}).json()
    assert old["items"] and all(i["taken"] < time.mktime((2020, 1, 1, 0, 0, 0, 0, 0, -1)) for i in old["items"])
    folder = client.get("/api/search/semantic", params={"q": "paars", "folder": str(vault / "RAW/2022/bursts"),
                                                        "include_weak": True}).json()
    assert {Path(i["path"]).parent for i in folder["items"]} == {vault / "RAW/2022/bursts"}
    # Paging: the pages together are the whole ranking, in order, without repeats
    full = client.get("/api/search/semantic", params={"q": "blauw", "include_weak": True, "limit": 200}).json()
    pages, offset = [], 0
    while offset is not None:
        page = client.get("/api/search/semantic", params={"q": "blauw", "include_weak": True, "limit": 7,
                                                          "offset": offset}).json()
        pages += page["items"]
        offset = page["next_offset"]
    assert [i["id"] for i in pages] == [i["id"] for i in full["items"]]
    assert len(full["items"]) == full["ranked"] >= full["total"]
    # Without include_weak the pages stop at the relevance cut
    strong = client.get("/api/search/semantic", params={"q": "blauw", "limit": 200}).json()
    assert len(strong["items"]) == strong["total"] and strong["next_offset"] is None
    assert client.get("/api/search/semantic", params={"q": "blauw", "orientation": "sideways"}).status_code == 400
    assert client.get("/api/search/semantic", params={"q": "blauw", "date_from": "gisteren"}).status_code == 400
    assert client.get("/api/search/semantic", params={"q": "x" * 500}).status_code in (400, 422)
    assert client.get("/api/search/semantic", params={"q": "blauw", "folder": "/etc"}).status_code == 403


def test_similar_photos_are_found_from_a_photo(client, config, vault, fake):
    _index(client)
    _embed(client)
    answer = client.get("/api/search/similar", params={"path": str(vault / STRAND)}).json()
    names = _names(answer)
    assert Path(STRAND).name not in names     # not the photo itself
    assert set(names[:3]) == {"strand 001 kopie.jpg", "dup van strand.jpg", "strand klein.jpg"}
    assert client.get("/api/search/similar", params={"path": str(vault / "RAW/misc/notes.txt")}).status_code == 404
    assert client.get("/api/search/similar", params={"path": "/etc/passwd"}).status_code == 403
    assert client.get("/api/search/similar").status_code == 400


def test_a_changed_photo_is_recognised_again_and_search_sees_it_at_once(client, config, vault, fake):
    from backend import semantic
    _index(client)
    _embed(client)
    target = vault / BURST[0]
    assert "burst_00.jpg" not in _names(client.get("/api/search/semantic", params={"q": "oranje"}).json())[:1]
    _photo(target, (200, 120, 30), "nu oranje", "2022:05:01 12:00:00")
    os.utime(target, (time.time() + 5, time.time() + 5))
    _index(client)
    assert semantic.counts(_manager().db)["pending"] == 1
    assert _embed(client)["embedded"] == 1
    top = _names(client.get("/api/search/semantic", params={"q": "oranje"}).json())[:2]
    assert "burst_00.jpg" in top
    with sqlite3.connect(str(config.DB_PATH)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM semantic_embeddings WHERE media_id = "
                            "(SELECT id FROM media WHERE path = ?)", (str(target),)).fetchone()[0] == 1


def test_a_moved_photo_keeps_its_embedding_and_one_in_trash_is_not_found(client, config, vault, fake):
    from backend import semantic
    from backend.file_ops import move_no_overwrite
    _index(client)
    _embed(client)
    db = _manager().db
    src = vault / STRAND_COPIES[0]
    moved = move_no_overwrite(src, vault / "RAW/2019" / src.name)
    assert semantic.counts(db)["pending"] == 0
    names = _names(client.get("/api/search/semantic", params={"q": "blauw"}).json())
    assert moved.name in names
    paths = [i["path"] for i in client.get("/api/search/semantic", params={"q": "blauw"}).json()["items"]]
    assert str(moved) in paths and str(src) not in paths
    trashed = move_no_overwrite(vault / STRAND, config.TRASH_FOLDER / STRAND)
    paths = [i["path"] for i in client.get("/api/search/semantic", params={"q": "blauw"}).json()["items"]]
    assert str(vault / STRAND) not in paths and str(trashed) not in paths


def test_embeddings_of_removed_files_are_purged(client, config, vault, fake):
    _index(client)
    _embed(client)
    gone = vault / "RAW/2021/🎉 feest/no exif.jpg"
    shutil.move(gone, config.CACHE_FOLDER.parent.parent / "buiten de vault.jpg")   # gone from the vault (as in Finder)
    _index(client)
    _index(client)    # gone at the end of two passes: the media row is removed
    with sqlite3.connect(str(config.DB_PATH)) as conn:
        before = conn.execute("SELECT COUNT(*) FROM semantic_embeddings").fetchone()[0]
    _embed(client)
    with sqlite3.connect(str(config.DB_PATH)) as conn:
        after = conn.execute("SELECT COUNT(*) FROM semantic_embeddings").fetchone()[0]
        orphans = conn.execute("SELECT COUNT(*) FROM semantic_embeddings WHERE media_id NOT IN "
                               "(SELECT id FROM media)").fetchone()[0]
    assert after == before - 1 and orphans == 0
    assert "no exif.jpg" not in _names(client.get("/api/search/semantic", params={"q": "groen"}).json())


@pytest.mark.parametrize("how", ["pause", "cancel"])
def test_an_interrupted_run_continues_without_doing_work_twice(client, config, vault, fake, monkeypatch, how):
    from backend import semantic
    monkeypatch.setattr(semantic, "EMBED_BATCH", 3)
    _index(client)
    job = {}

    def stop_at(n):
        if n == 6:
            (_manager().pause if how == "pause" else _manager().cancel)(job["id"])

    fake.on_image = stop_at
    r = client.post("/api/jobs", json={"type": "semantic", "params": {}})
    job["id"] = r.json()["job"]["id"]
    stopped = _manager().wait(job["id"], timeout=60)
    assert stopped["status"] == ("paused" if how == "pause" else "cancelled"), stopped
    db = _manager().db
    left = semantic.counts(db)["pending"]
    total = semantic.counts(db)["total"]
    assert 0 < left < total
    fake.on_image = None
    if how == "pause":
        _manager().resume(job["id"])
        done = _manager().wait(job["id"], statuses=("queued", "running"), timeout=60)
        assert done["status"] == "done", done
    else:
        _embed(client)
    assert semantic.counts(db)["pending"] == 0
    with sqlite3.connect(str(config.DB_PATH)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM semantic_embeddings").fetchone()[0] == total
        ok = conn.execute("SELECT COUNT(*) FROM semantic_embeddings WHERE status = 'ok'").fetchone()[0]
    assert fake.images == ok == total - 4    # every photo encoded exactly once over both runs


def test_a_run_waits_when_the_vault_goes_away(client, config, vault, fake):
    _index(client)
    hidden = vault.with_name("vault_weg")

    def unplug(n):
        if n == 3:
            vault.rename(hidden)

    fake.on_image = unplug
    r = client.post("/api/jobs", json={"type": "semantic", "params": {}})
    job_id = r.json()["job"]["id"]
    job = _manager().wait(job_id, statuses=("queued", "running"), timeout=60)
    assert job["status"] == "waiting_for_vault", job
    fake.on_image = None
    hidden.rename(vault)
    job = _manager().wait(job_id, statuses=("queued", "running", "waiting_for_vault"), timeout=60)
    assert job["status"] == "done", job
    assert client.get("/api/search/status").json()["pending"] == 0


def test_search_by_file_name_escapes_wildcards(client, config, vault):
    _index(client)
    assert _names(client.get("/api/search/filename", params={"q": "#hash%"}).json()) == [
        "name with  double  spaces & quote's #hash%.jpg"]
    assert _names(client.get("/api/search/filename", params={"q": "_0"}).json()) == [
        "burst_04.jpg", "burst_03.jpg", "burst_02.jpg", "burst_01.jpg", "burst_00.jpg", "IMG_0002.JPG"]
    assert client.get("/api/search/filename", params={"q": "%"}).json()["total"] == 1
    assert client.get("/api/search/filename", params={"q": "TAART"}).json()["total"] == 1
    assert client.get("/api/search/filename", params={"q": "strand", "kind": "video"}).json()["total"] == 0


def test_the_automatic_runner_starts_after_the_index_and_holds_after_a_pause(client, config, fake):
    from backend import semantic
    manager = _manager()
    auto = semantic.SemanticAuto(manager, config, manager.db)
    assert auto.tick() is False            # no index pass has finished yet
    _index(client)
    assert auto.tick() is True
    job = manager.list(limit=1, job_type=semantic.EMBED_JOB)[0]
    assert job["params"] == {"auto": True}
    manager.wait(job["id"], timeout=60)
    assert auto.tick() is False            # nothing left
    # A pause by the user holds the automatic runs
    _photo(config.RAW_FOLDER / "nieuw/a.jpg", (1, 2, 3), "a")
    _photo(config.RAW_FOLDER / "nieuw/b.jpg", (3, 2, 1), "b")
    _index(client)
    fake.on_image = lambda n: manager.pause(job2["id"]) if n >= 1 else None
    job2 = {}
    r = client.post("/api/jobs", json={"type": "semantic", "params": {}})
    job2["id"] = r.json()["job"]["id"]
    assert manager.wait(job2["id"], timeout=60)["status"] == "paused"
    fake.on_image = None
    assert auto.tick() is False
    auto._since = time.time() + 10        # as after a restart
    assert auto.tick() is True
    for j in manager.list(limit=5, job_type=semantic.EMBED_JOB):
        manager.wait(j["id"], timeout=60)
    assert manager.get(job2["id"])["status"] == "paused"   # the user's own job is left as it was


# ---------- the model download ----------

def _fake_model(tmp_path, monkeypatch, payloads):
    from backend import semantic
    files = tuple(semantic.ModelFile(name, f"https://example.invalid/{name}", len(data),
                                     hashlib.sha256(data).hexdigest()) for name, data in payloads.items())
    spec = semantic.ModelSpec(**{**semantic.SIGLIP2_B16.__dict__, "id": "test-model", "files": files})
    served = []

    class Response(io.BytesIO):
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def open_url(url, start):
        served.append((url, start))
        name = url.rsplit("/", 1)[1]
        response = Response(payloads[name][start:])
        response.status = 206 if start else 200
        return response

    monkeypatch.setattr(semantic, "_open_url", open_url)
    monkeypatch.setitem(semantic.MODELS, spec.id, spec)
    return spec, served


def test_the_download_checks_every_file_and_continues_where_it_stopped(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from backend import semantic
    payloads = {"visual.onnx": os.urandom(3 * semantic.DOWNLOAD_CHUNK + 17), "textual_fp16.onnx": b"t" * 999,
                "tokenizer.json": b"{}"}
    spec, served = _fake_model(tmp_path, monkeypatch, payloads)
    config = SimpleNamespace(MODELS_FOLDER=tmp_path / "models")
    folder = semantic.model_dir(config, spec)
    assert not semantic.installed(config, spec)
    calls = {"n": 0}

    def stop_after_two_chunks():
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("pauze")

    with pytest.raises(RuntimeError):
        semantic.download_model(config, spec, check=stop_after_two_chunks)
    part = folder / ("visual.onnx" + semantic.PART_SUFFIX)
    assert part.stat().st_size == 2 * semantic.DOWNLOAD_CHUNK and not semantic.installed(config, spec)
    result = semantic.download_model(config, spec)
    assert result["installed"] and semantic.installed(config, spec)
    assert ("https://example.invalid/visual.onnx", 2 * semantic.DOWNLOAD_CHUNK) in served
    assert (folder / "visual.onnx").read_bytes() == payloads["visual.onnx"] and not part.exists()
    # Installed means: every file as verified; a changed file is not trusted
    served.clear()
    assert semantic.download_model(config, spec)["downloaded"] == 0 and served == []
    (folder / "tokenizer.json").write_bytes(b"[]")
    assert not semantic.installed(config, spec)
    semantic.download_model(config, spec)
    assert (folder / "tokenizer.json").read_bytes() == b"{}" and semantic.installed(config, spec)


def test_a_download_with_a_wrong_checksum_is_refused(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from backend import semantic
    spec, served = _fake_model(tmp_path, monkeypatch, {"visual.onnx": b"v" * 100, "textual_fp16.onnx": b"t",
                                                      "tokenizer.json": b"{}"})
    bad = semantic.ModelFile("visual.onnx", spec.files[0].url, 100, "0" * 64)
    spec = semantic.ModelSpec(**{**spec.__dict__, "files": (bad,) + spec.files[1:]})
    config = SimpleNamespace(MODELS_FOLDER=tmp_path / "models")
    with pytest.raises(ValueError, match="Controlegetal"):
        semantic.download_model(config, spec)
    folder = semantic.model_dir(config, spec)
    assert not (folder / "visual.onnx").exists() and not (folder / "visual.onnx.part").exists()
    assert not semantic.installed(config, spec)


def test_the_download_job_installs_the_model(client, config, tmp_path, monkeypatch):
    from backend import semantic
    payloads = {"visual.onnx": b"v" * 10, "textual_fp16.onnx": b"t" * 20, "tokenizer.json": b"{}"}
    spec, served = _fake_model(tmp_path, monkeypatch, payloads)
    monkeypatch.setattr(semantic, "DEFAULT_MODEL", spec.id)
    from backend.routes import search
    monkeypatch.setattr(search.config, "MODELS_FOLDER", tmp_path / "models")
    assert client.get("/api/search/status").json()["model"]["installed"] is False
    result = _job(client, "model_download")
    assert result["installed"] and result["downloaded"] == 32
    status = client.get("/api/search/status").json()
    assert status["model"]["installed"] is True and status["download"]["status"] == "done"
    assert client.post("/api/jobs", json={"type": "model_download", "params": {"model": "nee"}}).status_code == 400


# ---------- speed of the search itself (synthetic vectors) ----------

def test_ranking_100k_vectors_is_fast(client, config, fake, monkeypatch):
    """100k random float16 vectors in the table: the first query loads them, the next is quick."""
    from backend import semantic
    _index(client)
    db = _manager().db
    rng = np.random.default_rng(1)
    n = 100_000
    with db._connect() as conn:
        base = conn.execute("SELECT COALESCE(MAX(id), 0) FROM media").fetchone()[0]
        now = time.time()
        conn.executemany(
            "INSERT INTO media (id, path, folder, name, kind, ext, size, mtime_ns, taken, added_at, indexed_at, "
            "seen_at) VALUES (?, ?, '/x', ?, 'photo', '.jpg', 1, 1, ?, ?, ?, ?)",
            [(base + 1 + i, f"/x/{i}.jpg", f"{i}.jpg", now - i, now, now, now) for i in range(n)])
        vectors = rng.standard_normal((n, DIM)).astype(np.float32)
        vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
        conn.executemany(
            "INSERT INTO semantic_embeddings (media_id, model, dim, vector, status, size, mtime_ns, created_at) "
            "VALUES (?, ?, ?, ?, 'ok', 1, 1, ?)",
            [(base + 1 + i, semantic.DEFAULT_MODEL, DIM, vectors[i].astype(np.float16).tobytes(), now)
             for i in range(n)])
    semantic._bump()
    start = time.perf_counter()
    cold = semantic.search_text(db, config, "blauw")
    cold_s = time.perf_counter() - start
    start = time.perf_counter()
    warm = semantic.search_text(db, config, "roze", {"kind": "photo"})
    warm_s = time.perf_counter() - start
    assert cold["ranked"] == semantic.MAX_RANKED and warm["items"]
    # Generous bounds for a busy test machine; the measured numbers are in the README
    assert cold_s < 6 and warm_s < 1.5, (cold_s, warm_s)


# ---------- the real model (only when it is installed) ----------

def _real_models_folder():
    from backend.config import DEFAULT_MODELS_FOLDER
    from backend import semantic
    for folder in (os.environ.get("DATAVAULT_MODELS_FOLDER"), DEFAULT_MODELS_FOLDER):
        if folder and semantic.installed(type("C", (), {"MODELS_FOLDER": Path(folder)})()):
            return Path(folder)
    return None


@pytest.mark.skipif(_real_models_folder() is None, reason="Slim zoeken model not installed (scripts/download_models.py)")
def test_the_real_model_finds_the_right_photo_for_dutch_queries():
    from PIL import Image
    from backend import semantic
    spec = semantic.current_model()
    encoder = semantic.Encoder(spec, _real_models_folder() / spec.id)
    encoder.load_image("cpu")
    folder = Path(__file__).parent / "fixtures" / "semantic"
    photos = sorted(folder.glob("*.jpg"))
    names = [p.stem for p in photos]
    vectors = np.stack([encoder.encode_image(encoder.preprocess(Image.open(p))) for p in photos])
    expected = {
        "hond op het strand": "hond_strand",
        "verjaardagstaart": "taart_kaarsjes",
        "zonsondergang boven zee": "zonsondergang_zee",
        "sneeuw in de bergen": "besneeuwde_bergen",
        "een kat": "kat",
        "stad bij nacht": "stad_nacht",
        "pizza": "pizza",
    }
    for query, photo in expected.items():
        order = np.argsort(-(vectors @ encoder.encode_text(query)))
        top3 = [names[i] for i in order[:3]]
        assert photo in top3, (query, top3)
        assert names[order[0]] == photo, (query, top3)


@pytest.mark.skipif(_real_models_folder() is None, reason="Slim zoeken model not installed (scripts/download_models.py)")
def test_the_real_model_gives_results_through_search_text(tmp_path):
    """The relevance cut must not hide right photos: short queries find something strong."""
    import time
    from PIL import Image
    from backend import semantic
    from backend.database import Database
    semantic._index.reset()
    semantic._rankings.clear()
    semantic._allowed_cache.clear()
    from types import SimpleNamespace
    cfg = SimpleNamespace(MODELS_FOLDER=_real_models_folder())
    db = Database(tmp_path / "datavault.db")
    spec = semantic.current_model()
    encoder = semantic.get_encoder(cfg)
    encoder.load_image("cpu")
    photos = sorted((Path(__file__).parent / "fixtures" / "semantic").glob("*.jpg"))
    now = time.time()
    rows = []
    with db._connect() as conn:
        for i, p in enumerate(photos, 1):
            conn.execute(
                "INSERT INTO media (id, path, folder, name, kind, ext, size, mtime_ns, added_at, indexed_at, seen_at) "
                "VALUES (?,?,?,?, 'photo', 'jpg', 1, 1, ?, ?, ?)", (i, f"/v/{p.name}", "/v", p.name, now, now, now))
            with Image.open(p) as img:
                rows.append((i, encoder.encode_image(encoder.preprocess(img)), "ok", 1, 1, "thumb"))
    semantic._write(db, spec, rows)
    try:
        for query, name in (("pizza", "pizza"), ("een kat", "kat"), ("hond op het strand", "hond_strand")):
            result = semantic.search_text(db, cfg, query, {}, 0, 20)
            assert result["total"] > 0, query
            assert name in result["items"][0]["name"], (query, result["items"][0]["name"])
    finally:
        semantic._index.reset()
        semantic._rankings.clear()
