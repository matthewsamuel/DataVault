"""
One shared list of media extensions (GAP-3), honest type labels (MEDIA-THUMBS-TIMELINE-18,
LIVE-PROBE-24), content types for RAW photos (MEDIA-THUMBS-TIMELINE-22) and a name search
that ignores the Unicode form (GAP-2).
"""
import shutil
import unicodedata
from pathlib import Path

from backend import config as config_module
from backend.config import content_type, media_type
from fixtures.vault_builder import STRAND

NEW_PHOTO_FORMATS = ["a.tif", "b.heif", "c.CR3", "d.raf", "e.ORF", "f.rw2", "g.hif"]


def test_one_extension_list_knows_every_common_photo_format():
    for name in NEW_PHOTO_FORMATS + ["h.tiff", "i.jpg", "j.dng", "k.HEIC"]:
        assert media_type(Path(name)) == "photo", name
        assert config_module.is_media_file(Path(name)), name
    assert media_type(Path("clip.MTS")) == "video"
    assert media_type(Path("song.flac")) == "audio"
    for name in ["notes.txt", "report.pdf", "backup.zip", "noext"]:
        assert media_type(Path(name)) == "other", name
        assert not config_module.is_media_file(Path(name))


def test_listing_labels_documents_as_other_never_as_photo(client, vault):
    files = client.get("/api/media", params={"folder": str(vault / "RAW/misc")}).json()["files"]
    types = {f["name"]: f["type"] for f in files}
    assert types["notes.txt"] == "other"
    assert types["UPPER.JPEG"] == "photo"
    audio = client.get("/api/media", params={"folder": str(vault / "RAW/audio")}).json()["files"]
    assert {f["type"] for f in audio} == {"audio"}


def test_raw_photos_and_unknown_files_are_not_served_as_text(client, vault):
    raw = vault / "RAW/misc/camera.CR2"
    raw.write_bytes(b"II*\x00" + b"\x00" * 64)
    unknown = vault / "RAW/misc/blob.qqzz"
    unknown.write_bytes(b"\x00\x01")
    r = client.get("/api/file", params={"path": str(raw)})
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/x-canon-cr2"
    r = client.get("/api/file", params={"path": str(unknown)})
    assert r.headers["content-type"] == "application/octet-stream"
    assert content_type(Path("x.heic")) == "image/heic"
    assert content_type(Path("x.jpg")) == "image/jpeg"


def test_new_formats_reach_the_keurer_and_the_duplicate_scan(client, vault):
    folder = vault / "RAW/formats"
    folder.mkdir()
    for name in ("scan.tif", "fuji.RAF", "canon.cr3"):
        shutil.copy2(vault / STRAND, folder / name)
    names = {f["name"] for f in client.get("/api/keurer/files", params={"folder": str(folder)}).json()["files"]}
    assert names == {"scan.tif", "fuji.RAF", "canon.cr3"}
    r = client.post("/api/duplicates/scan_folder", params={"folder_path": str(folder)})
    assert r.status_code == 200, r.text
    groups = r.json()["groups"]
    assert len(groups) == 1 and len(groups[0]["copies"]) == 3


def test_name_search_ignores_case_and_unicode_form(client, vault):
    folder = vault / "RAW/zoeken"
    folder.mkdir()
    nfd_name = unicodedata.normalize("NFD", "Café_Zürich.jpg")
    shutil.copy2(vault / STRAND, folder / nfd_name)
    for query in ("café", "CAFÉ", unicodedata.normalize("NFD", "café"), "zürich"):
        photos = client.get("/api/stats/search-photos", params={"query": query}).json()["photos"]
        assert [p["name"] for p in photos if "Zu" in unicodedata.normalize("NFD", p["name"])], query
    # Documents are never a search hit, even when the name matches
    (folder / "café notities.txt").write_text("x")
    photos = client.get("/api/stats/search-photos", params={"query": "notities"}).json()["photos"]
    assert photos == []
