"""Slim zoeken: index reuse after a purge, the relevance cut and paging (fake vectors, no model)."""
import time

import numpy as np
import pytest

from backend import semantic
from backend.database import Database


@pytest.fixture
def lib(tmp_path):
    semantic._index.reset()
    semantic._rankings.clear()
    semantic._allowed_cache.clear()
    db = Database(tmp_path / "datavault.db")
    spec = semantic.current_model()
    rng = np.random.default_rng(7)
    state = {"n": 0}

    def add(vector, name=None):
        state["n"] += 1
        i, now = state["n"], time.time()
        with db._connect() as conn:
            conn.execute(
                "INSERT INTO media (id, path, folder, name, kind, ext, size, mtime_ns, added_at, indexed_at, seen_at) "
                "VALUES (?,?,?,?, 'photo', 'jpg', 1, 1, ?, ?, ?)",
                (i, f"/v/{i}.jpg", "/v", name or f"{i}.jpg", now, now, now))
        semantic._write(db, spec, [(i, vector.astype(np.float32), "ok", 1, 1, "thumb")])
        semantic._allowed_cache.clear()      # (the media index did not see this insert)
        return i

    def mix(query, cosine):
        noise = rng.normal(size=spec.dim)
        noise -= noise @ query * query
        noise /= np.linalg.norm(noise)
        return cosine * query + np.sqrt(1 - cosine ** 2) * noise

    query = rng.normal(size=spec.dim)
    query /= np.linalg.norm(query)
    semantic.set_encoder_factory(lambda c, s: type("F", (), {
        "encode_text": lambda self, t: query, "load_text": lambda self: None})())
    cfg = type("C", (), {"MODELS_FOLDER": tmp_path})()
    yield db, cfg, add, mix, query
    semantic.set_encoder_factory(None)
    semantic._index.reset()
    semantic._rankings.clear()


def test_new_photos_are_found_after_the_newest_embeddings_were_purged(lib):
    db, cfg, add, mix, query = lib
    for _ in range(5):
        add(mix(query, 0.0))
    semantic._index.refresh(db, semantic.current_model())
    with db._connect() as conn:
        conn.execute("DELETE FROM media WHERE id IN (4, 5)")
    assert semantic.purge_orphans(db) == 2
    new = [add(mix(query, 0.0)) for _ in range(2)]      # reuse rowids 4 and 5
    semantic._index.refresh(db, semantic.current_model())
    for media_id in new:
        assert semantic._index.vector_of(media_id) is not None
    assert [it["id"] for it in semantic.search_similar(db, cfg, new[0], include_weak=True)["items"]]


def test_a_concept_that_is_half_the_library_is_still_found(lib):
    db, cfg, add, mix, query = lib
    for _ in range(40):
        add(mix(query, 0.13))
    for _ in range(40):
        add(mix(query, 0.02))
    result = semantic.search_text(db, cfg, "hond")
    assert result["total"] == 40
    assert len(semantic.search_photos(db, cfg, "hond", limit=20)) == 20


def test_nonsense_finds_nothing_on_a_small_and_a_large_library(lib):
    db, cfg, add, mix, query = lib
    for _ in range(12):
        add(mix(query, 0.08))
    assert semantic.search_text(db, cfg, "xqzv")["total"] == 0
    for _ in range(60):
        add(mix(query, 0.03))
    assert semantic.search_text(db, cfg, "xqzv")["total"] == 0


def test_a_near_copy_does_not_hide_the_similar_photos(lib):
    db, cfg, add, mix, query = lib
    base = add(query)
    add(mix(query, 0.99))                        # a copy
    friends = [add(mix(query, 0.74)) for _ in range(5)]
    for _ in range(40):
        add(mix(query, 0.0))
    result = semantic.search_similar(db, cfg, base)
    ids = {it["id"] for it in result["items"]}
    assert set(friends) <= ids


def test_paging_keeps_its_ranking_while_the_index_grows(lib):
    db, cfg, add, mix, query = lib
    for k in range(10):
        add(mix(query, 0.2 - k * 0.005))
    first = semantic.search_text(db, cfg, "x", limit=4, include_weak=True)
    add(mix(query, 0.5))                         # a new photo arrives between two pages
    second = semantic.search_text(db, cfg, "x", offset=4, limit=4, include_weak=True)
    ids = [it["id"] for it in first["items"] + second["items"]]
    assert len(ids) == len(set(ids)) == 8


def test_file_names_match_without_regard_to_case_for_every_letter(lib):
    db, cfg, add, mix, query = lib
    add(mix(query, 0.0), name="Église Ühlingen.jpg")
    assert semantic.search_filename(db, "église")["total"] == 1
    assert semantic.search_filename(db, "ÜHLINGEN")["total"] == 1


def test_search_photos_falls_back_to_the_best_weak_hits(lib):
    db, cfg, add, mix, query = lib
    for _ in range(40):
        add(mix(query, 0.0))
    best = add(mix(query, 0.08))      # under the cut, still the best of a weak crowd
    assert semantic.search_text(db, cfg, "kort")["total"] == 0
    found = semantic.search_photos(db, cfg, "kort", limit=10)
    assert found and found[0]["id"] == best and len(found) <= semantic.WEAK_FALLBACK
