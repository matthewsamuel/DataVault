"""
DataGatherer for messages and social: the msg_* and social_items tables (migrations 90-91),
Meta exports (Facebook, Instagram, Messenger secure storage: mojibake, Dutch and English
folder names, the 'since' date), WhatsApp (the Mac database from a snapshot with its -wal,
an iPhone ChatStorage.sqlite, 'Export chat' in four phone formats, merged without
duplicates), iMessage (attributedBody, nanosecond dates, tapbacks), Full Disk Access via the
helper, FTS search, the Berichten API and Pasha's opt-in.

Every database and export is synthetic (fake_messages.py); the 'home folder' with the chat
databases is a temp folder (DATAVAULT_GATHER_HOME, set by conftest).
"""
import json
import os
import sqlite3
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from backend.datagatherer import cards, daemon, local_db, messages_store, registry, state
from backend.datagatherer.formats import imessage_db, meta_dyi, whatsapp_txt
from fake_messages import (APPLE_EPOCH, T0, WHATSAPP_TXT, fb_thread, instagram_export, make_chat_db,
                           make_chatstorage, meta_export, mojibake, secure_storage, tiny_jpeg, typedstream,
                           whatsapp_zip)
from helpers import kept_rows

BRUSSELS = ZoneInfo("Europe/Brussels")
ME = "Matthias Voorbeeld"


def _wait(app, job_id, timeout=30):
    return app.state.jobs.wait(job_id, timeout=timeout)


def _upload(client, app, source: str, path: Path, name=None):
    r = client.post(f"/api/datagatherer/sources/{source}/upload?name={name or path.name}", content=path.read_bytes())
    assert r.status_code in (200, 201), r.text
    job = r.json()["job"]
    assert _wait(app, job["id"])["status"] == "done", app.state.jobs.get(job["id"])
    result = app.state.jobs.result(job["id"])
    if result.get("import_job"):
        result["import"] = _wait(app, result["import_job"])
        result["import_result"] = app.state.jobs.result(result["import_job"])
    return result


def _sync(client, app, source):
    r = client.post(f"/api/datagatherer/sources/{source}/sync")
    assert r.status_code in (200, 201), r.text
    job = _wait(app, r.json()["job"]["id"])
    return job, app.state.jobs.result(job["id"]) if job["status"] == "done" else None


def _rows(app, sql, args=()):
    with sqlite3.connect(str(app.state.config.DB_PATH)) as conn:
        return conn.execute(sql, args).fetchall()


def _home() -> Path:
    return Path(os.environ["DATAVAULT_GATHER_HOME"])


# ---------- formats ----------

def test_mojibake_is_repaired_and_correct_text_stays():
    assert meta_dyi.fix_mojibake(mojibake("Café met Zoë 😀")) == "Café met Zoë 😀"
    assert meta_dyi.fix_mojibake("Café") == "Café"           # already right: unchanged
    assert meta_dyi.fix_mojibake({"a": [mojibake("één")]}) == {"a": ["één"]}


@pytest.mark.parametrize("fmt,first,precision", [
    ("ios_nl", (2024, 6, 1, 10, 0, 5), "s"),
    ("ios_en", (2024, 6, 1, 10, 1, 0), "s"),
    ("android_nl", (2024, 6, 1, 10, 1, 0), "min"),
    ("android_us", (2024, 6, 1, 10, 1, 0), "min"),
    ("android_fr", (2024, 6, 1, 10, 1, 0), "min"),
])
def test_whatsapp_export_lines_in_every_phone_format(fmt, first, precision):
    from datetime import datetime
    parsed = whatsapp_txt.parse(WHATSAPP_TXT[fmt], BRUSSELS)
    assert parsed["precision"] == precision
    got = datetime.fromtimestamp(parsed["messages"][0]["ts_ms"] / 1000, BRUSSELS)
    assert got.timetuple()[:6] == first
    assert parsed["messages"][0]["sender"] == "Anna Jansen"
    if fmt in ("ios_nl", "android_nl"):
        multi = parsed["messages"][1 if fmt == "android_nl" else 2]
        assert multi["text"] == "Ja, graag\nom 11 uur?"
        media = parsed["messages"][-1 if fmt == "android_nl" else 3]
        assert media["kind"] == "media" and media["attachments"][0]["name"].endswith(".jpg")
    if fmt == "android_us":    # month first: 6/13 is 13 June, in the evening
        assert datetime.fromtimestamp(parsed["messages"][1]["ts_ms"] / 1000, BRUSSELS).timetuple()[:5] == (2024, 6, 13, 18, 30)
    if fmt == "android_fr":
        assert whatsapp_txt.looks_like_chat(WHATSAPP_TXT[fmt]) and len(parsed["messages"]) == 2
        assert datetime.fromtimestamp(parsed["messages"][1]["ts_ms"] / 1000, BRUSSELS).timetuple()[:5] == (2024, 6, 13, 21, 34)


def test_whatsapp_chat_names_from_file_names():
    assert whatsapp_txt.chat_name("WhatsApp Chat - Anna Jansen.zip") == "Anna Jansen"
    assert whatsapp_txt.chat_name("WhatsApp-chat met Anna Jansen (1).txt") == "Anna Jansen"
    assert whatsapp_txt.chat_name("WhatsApp Chat with Bob.txt") == "Bob"
    assert whatsapp_txt.chat_name("vakantie.zip") is None


def test_attributed_body_is_decoded_short_and_long():
    assert imessage_db.decode_attributed_body(typedstream("Hallo daar 👋")) == "Hallo daar 👋"
    long_text = "Lange tekst. " * 40
    assert imessage_db.decode_attributed_body(typedstream(long_text)) == long_text
    assert imessage_db.decode_attributed_body(b"garbage") is None
    # Apple's epoch: nanoseconds (macOS 10.13+) and seconds both give the same moment
    assert imessage_db.to_unix(int((T0 - APPLE_EPOCH) * 1e9)) == pytest.approx(T0)
    assert imessage_db.to_unix(T0 - APPLE_EPOCH) == pytest.approx(T0)


# ---------- migrations ----------

def test_migrations_90_91_add_tables_and_keep_every_user_row(tmp_path, monkeypatch):
    from backend import migrations
    from backend.database import Database
    from test_duplicates_runs import _migrate_to
    from test_foundation import _make_legacy_db
    db_path = tmp_path / "datavault.db"
    _make_legacy_db(db_path)
    _migrate_to(db_path, 61, monkeypatch)
    now = time.time()
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("INSERT INTO app_settings VALUES ('chat_model', '\"qwen2.5:3b\"', 5.0)")
        conn.execute("INSERT INTO jobs (type, params, status, processed, created_at, updated_at, checkpoint) "
                     "VALUES ('index', '{}', 'paused', 3, ?, ?, '{\"folder\": 1}')", (now, now))
        conn.execute("INSERT INTO dg_connectors (source_id, settings, updated_at) VALUES ('spotify', '{\"x\": 1}', 1)")
        conn.execute("INSERT INTO spotify_plays (play_key, source, ts_ms, content, first_seen_at) "
                     "VALUES ('k', 'export_extended', 1, 'track', 1)")
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name != 'schema_version'")]
        before = {t: conn.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall() for t in tables}
    db = Database(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        assert migrations.get_schema_version(conn) == migrations.LATEST_VERSION >= 91
        for table, rows in before.items():
            assert kept_rows(conn, table, rows) == rows, table
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
        assert {"msg_conversations", "msg_messages", "msg_fts", "social_items"} <= names
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert db.get_rating("/v/RAW/a.jpg") == 1150
    assert db.get_keurer_stats()["streak"] == 3
    assert db.get_setting("chat_model") == "qwen2.5:3b"
    # The FTS index follows inserts and updates
    conv = messages_store.conversation(db, "whatsapp", "x@s.whatsapp.net", "X")
    messages_store.insert_messages(db, "whatsapp", [messages_store.message_row(
        conv, "k1", "whatsapp_mac", T0 * 1000, source="whatsapp", body="koffie in het café")])
    assert [h["title"] for h in messages_store.search(db, "cafe")] == ["X"]


# ---------- Facebook, Messenger, Instagram ----------

def _fb_threads():
    anna = fb_thread([ME, "Anna Jansen"], [
        {"sender_name": "Anna Jansen", "timestamp_ms": T0 * 1000 + 5000, "content": "Zin in een café? ☕",
         "reactions": [{"reaction": "❤", "actor": ME}]},
        {"sender_name": ME, "timestamp_ms": T0 * 1000 + 9000, "content": "Graag, Zoë komt ook",
         "photos": [{"uri": "your_facebook_activity/messages/inbox/anna_10001/photos/1.jpg",
                     "creation_timestamp": T0}]},
    ], title="Anna Jansen")
    group = fb_thread([ME, "Anna Jansen", "Bob de Bouwer"], [
        {"sender_name": "Bob de Bouwer", "timestamp_ms": T0 * 1000 + 60000, "content": "Wie brengt de taart mee?"},
    ], title="Verjaardag")
    return {"anna_10001": anna, "verjaardag_10002": group}


def _fb_social():
    posts = [{"timestamp": T0 + 100, "data": [{"post": mojibake("Eerste dag in Zürich")}], "title": mojibake(f"{ME} updated his status.")}]
    comments = [{"timestamp": T0 + 200, "data": [{"comment": {"timestamp": T0 + 200, "comment": mojibake("Mooi zo! 👏"),
                                                               "author": mojibake(ME)}}], "title": mojibake(f"{ME} commented.")}]
    reactions = [{"timestamp": T0 + 300, "data": [{"reaction": {"reaction": "LIKE", "actor": mojibake(ME)}}],
                  "title": mojibake(f"{ME} likes Anna's post.")}]
    return posts, comments, reactions


def test_facebook_export_is_recognised_imported_once_and_repaired(client, app, tmp_path):
    posts, comments, reactions = _fb_social()
    export = meta_export(tmp_path / "facebook-matthias-2024.zip", "en", _fb_threads(), posts, comments, reactions,
                         photos={"your_facebook_activity/messages/inbox/anna_10001/photos/1.jpg": tiny_jpeg()})
    fb, ig, ms = (registry.get(i) for i in ("facebook", "instagram", "messenger"))
    from backend.datagatherer import raw_store
    names = raw_store.read_zip_names(export)
    assert fb.detect(export, names) == "dyi" and ig.detect(export, names) is None and ms.detect(export, names) is None

    result = _upload(client, app, "facebook", export)
    stats = result["import_result"]
    assert stats["imports"] == 1 and stats["errors"] == 0
    convs = {r[0]: r[1:] for r in _rows(app, "SELECT title, source, is_group, message_count FROM msg_conversations")}
    assert convs["Anna Jansen"] == ("messenger", 0, 2) and convs["Verjaardag"] == ("messenger", 1, 1)
    msgs = _rows(app, "SELECT sender, is_me, body, reactions, attachments FROM msg_messages ORDER BY sent_at_ms")
    assert msgs[0][2] == "Zin in een café? ☕" and msgs[0][1] == 0          # mojibake repaired, not mine
    assert json.loads(msgs[0][3]) == [{"actor": ME, "reaction": "❤"}]
    assert msgs[1][1] == 1 and json.loads(msgs[1][4])[0]["name"] == "1.jpg"  # mine (from the profile file)
    items = _rows(app, "SELECT kind, body FROM social_items WHERE source = 'facebook' ORDER BY ts_ms")
    assert items == [("post", "Eerste dag in Zürich"), ("comment", "Mooi zo! 👏"), ("reaction", "LIKE")]

    # The same export again: copied once, nothing added
    again = _upload(client, app, "facebook", export)
    assert again["created"] is False
    r = client.post("/api/datagatherer/sources/facebook/import")
    assert _wait(app, r.json()["job"]["id"])["status"] == "done"
    assert len(_rows(app, "SELECT id FROM msg_messages")) == 3
    assert len(_rows(app, "SELECT id FROM social_items")) == 3

    # The photo in the export has a preview; the card shows the data and the 'since' date
    msg_id = _rows(app, "SELECT id FROM msg_messages WHERE attachments IS NOT NULL")[0][0]
    preview = client.get(f"/api/messages/attachments/{msg_id}/0")
    assert preview.status_code == 200 and preview.headers["content-type"] == "image/jpeg"
    assert client.get(f"/api/messages/attachments/{msg_id}/5").status_code == 404
    detail = client.get("/api/datagatherer/sources/facebook").json()
    assert detail["records"] == 6 and detail["since"] and detail["export_kinds"][0]["date_range"] is True


def test_a_facebook_export_with_instagram_in_a_chat_name_stays_facebook(tmp_path):
    from backend.datagatherer import raw_store
    threads = {"instagramlover_99": fb_thread([ME, "Insta Gramlover"], [
        {"sender_name": "Insta Gramlover", "timestamp_ms": T0 * 1000, "content": "Hoi"}])}
    fb_zip = meta_export(tmp_path / "facebook-x.zip", "en", threads)
    nl_zip = meta_export(tmp_path / "export.zip", "nl", threads)
    ig_zip = instagram_export(tmp_path / "meta-2024.zip", threads={"anna_1": fb_thread([ME, "Anna"], [
        {"sender_name": "Anna", "timestamp_ms": T0 * 1000, "content": "Hoi"}])})
    fb, ig = registry.get("facebook"), registry.get("instagram")
    for path in (fb_zip, nl_zip):
        names = raw_store.read_zip_names(path)
        assert fb.detect(path, names) == "dyi" and ig.detect(path, names) is None, path.name
    names = raw_store.read_zip_names(ig_zip)
    assert ig.detect(ig_zip, names) == "dyi" and fb.detect(ig_zip, names) is None


def test_an_overlapping_newer_export_adds_only_what_is_new(client, app, tmp_path):
    first = meta_export(tmp_path / "facebook-1.zip", "en", _fb_threads())
    _upload(client, app, "facebook", first)
    threads = _fb_threads()
    threads["anna_10001"]["messages"].append({"sender_name": mojibake("Anna Jansen"), "timestamp_ms": T0 * 1000 + 99000,
                                              "content": mojibake("Tot straks!")})
    second = meta_export(tmp_path / "facebook-2.zip", "en", threads)
    result = _upload(client, app, "facebook", second)
    assert result["created"] is True and result["import_result"]["added"] == 1
    assert len(_rows(app, "SELECT id FROM msg_messages")) == 4


def test_dutch_folder_names_are_read_the_same(client, app, tmp_path):
    export = meta_export(tmp_path / "facebook-nl.zip", "nl", _fb_threads())
    from backend.datagatherer import raw_store
    assert registry.get("facebook").detect(export, raw_store.read_zip_names(export)) == "dyi"
    _upload(client, app, "facebook", export)
    assert len(_rows(app, "SELECT id FROM msg_messages WHERE source = 'messenger'")) == 3


def test_messenger_secure_storage_merges_with_the_facebook_export(client, app, tmp_path):
    _upload(client, app, "facebook", meta_export(tmp_path / "facebook.zip", "en", _fb_threads()))
    chat = {"participants": [ME, "Anna Jansen"], "threadName": "Anna Jansen", "messages": [
        # the same message as in the Facebook export (proper UTF-8 here, no mojibake)
        {"senderName": "Anna Jansen", "text": "Zin in een café? ☕", "timestamp": T0 * 1000 + 5000, "type": "text",
         "isUnsent": False, "reactions": [], "media": []},
        # newer, end-to-end encrypted only
        {"senderName": ME, "text": "Ik ben er om 3 uur", "timestamp": T0 * 1000 + 7_200_000, "type": "text",
         "isUnsent": False, "reactions": [{"actor": "Anna Jansen", "reaction": "👍"}],
         "media": [{"uri": "./media/foto.jpg"}]},
    ]}
    zip_path = secure_storage(tmp_path / "messages.zip", {"anna_jansen": chat}, {"media/foto.jpg": tiny_jpeg()})
    from backend.datagatherer import raw_store
    names = raw_store.read_zip_names(zip_path)
    assert registry.get("messenger").detect(zip_path, names) == "secure"
    assert registry.get("facebook").detect(zip_path, names) is None
    result = _upload(client, app, "messenger", zip_path)
    assert result["import_result"]["added"] == 1           # the overlapping message was already there
    convs = _rows(app, "SELECT id, message_count FROM msg_conversations WHERE title = 'Anna Jansen' "
                       "AND merged_into IS NULL")
    assert len(convs) == 1 and convs[0][1] == 3            # one chat across both exports
    thread = client.get(f"/api/messages/conversations/{convs[0][0]}").json()
    newest = thread["messages"][-1]
    assert newest["is_me"] is True and newest["origin"] == "messenger_e2ee"
    assert newest["attachments"][0]["preview"] is True
    assert client.get(f"/api/messages/attachments/{newest['id']}/0").status_code == 200


def _group(people, messages, title, folder):
    thread = fb_thread([ME] + people, [{"sender_name": who, "timestamp_ms": T0 * 1000 + sec * 1000, "content": text}
                                       for who, sec, text in messages], title=title)
    return {folder: thread}


def _open_convs(app):
    return _rows(app, "SELECT title, message_count FROM msg_conversations WHERE merged_into IS NULL "
                      "AND message_count > 0 ORDER BY id")


def test_a_member_who_left_and_a_renamed_group_stay_one_chat(client, app, tmp_path):
    msgs = [("Anna", 1, "Wie komt er zondag?"), ("Bob", 2, "Ik!"), ("Carl", 3, "Ik ook")]
    _upload(client, app, "facebook", meta_export(tmp_path / "facebook-1.zip", "en",
                                                 _group(["Anna", "Bob", "Carl"], msgs, "Familie", "familie_555")))
    # Newer, overlapping: Carl left, the group was renamed (so was its folder), one new message
    newer = _group(["Anna", "Bob"], msgs + [("Anna", 4, "Carl is uit de groep")], "Familie ❤", "familieliefde_555")
    result = _upload(client, app, "facebook", meta_export(tmp_path / "facebook-2.zip", "en", newer))
    assert result["import_result"]["added"] == 1
    assert _open_convs(app) == [("Familie", 4)]
    assert len(_rows(app, "SELECT id FROM msg_messages")) == 4


def test_different_chats_with_the_same_people_stay_apart(client, app, tmp_path):
    threads = {}
    threads.update(_group(["Anna", "Bob"], [("Anna", 1, "Fijne verjaardag")], "Familie", "familie_1"))
    threads.update(_group(["Anna", "Bob"], [("Bob", 2, "Agenda voor maandag")], "Werkgroep", "werkgroep_2"))
    # Two deactivated accounts: Meta calls both 'Facebook user'
    threads.update(_group(["Facebook user"], [("Facebook user", 3, "Hallo van de eerste")], None, "facebookuser_111"))
    threads.update(_group(["Facebook user"], [("Facebook user", 4, "Hallo van de tweede")], None, "facebookuser_222"))
    _upload(client, app, "facebook", meta_export(tmp_path / "facebook.zip", "en", threads))
    convs = _open_convs(app)
    assert len(convs) == 4 and ("Familie", 1) in convs and ("Werkgroep", 1) in convs
    # A secure storage chat of those people is ambiguous: it stays its own chat, nothing is lost
    chat = {"participants": [ME, "Anna", "Bob"], "threadName": "Familie", "messages": [
        {"senderName": "Anna", "text": "Nieuw bericht", "timestamp": T0 * 1000 + 9000, "type": "text",
         "isUnsent": False, "reactions": [], "media": []}]}
    _upload(client, app, "messenger", secure_storage(tmp_path / "messages.zip", {"familie": chat}))
    assert len(_open_convs(app)) == 5 and len(_rows(app, "SELECT id FROM msg_messages")) == 5


def test_chats_stored_by_their_people_before_are_split_and_not_doubled(client, app, tmp_path):
    """A database from before folder ids: two groups with the same members in one 'p:' chat."""
    from backend.datagatherer import meta_import, messages_store
    db = app.state.db
    old = messages_store.conversation(db, "messenger", "p:anna|bob|" + ME.casefold(), "Familie", [ME, "Anna", "Bob"], True)
    rows = [messages_store.message_row(old, f"legacy{i}", "facebook_dyi", T0 * 1000 + sec * 1000, source="messenger",
                                       sender=who, body=text)
            for i, (who, sec, text) in enumerate([("Anna", 1, "Fijne verjaardag"), ("Bob", 2, "Agenda voor maandag")])]
    rows.append(messages_store.message_row(old, "secure0", "messenger_e2ee", T0 * 1000 + 9000, source="messenger",
                                           sender="Anna", body="Versleuteld"))
    messages_store.insert_messages(db, "messenger", rows)
    threads = {}       # Werkgroep first (folder order): it must not take over the chat called Familie
    threads.update(_group(["Anna", "Bob"], [("Bob", 2, "Agenda voor maandag"), ("Bob", 5, "Nieuw")],
                          "Werkgroep", "a_werkgroep_2"))
    threads.update(_group(["Anna", "Bob"], [("Anna", 1, "Fijne verjaardag")], "Familie", "familie_1"))
    result = _upload(client, app, "facebook", meta_export(tmp_path / "facebook.zip", "en", threads))
    assert result["import_result"]["added"] == 1
    assert len(_rows(app, "SELECT id FROM msg_messages")) == 4
    by_title = {t: n for t, n in _open_convs(app)}
    assert by_title == {"Familie": 2, "Werkgroep": 2}    # Familie kept the encrypted message, Werkgroep got its own
    assert meta_import.claim_messages(db, "messenger", 999, {"participants": [], "messages": []}, "x") == 0


def test_instagram_export_reads_media_metadata_and_direct_messages(client, app, tmp_path):
    posts = [{"media": [{"uri": "media/posts/202406/1.jpg", "creation_timestamp": T0 + 50, "title": mojibake("Zee 🌊"),
                         "media_metadata": {"photo_metadata": {"exif_data": [{"latitude": 51.2, "longitude": 2.9}]}}}],
              "title": mojibake("Zee 🌊"), "creation_timestamp": T0 + 50}]
    thread = fb_thread(["matthias.voorbeeld", "Anna Jansen"], [
        {"sender_name": "Anna Jansen", "timestamp_ms": T0 * 1000 + 1000, "content": "Mooie foto!"}], title="Anna Jansen")
    likes = [{"title": "anna.jansen", "string_list_data": [{"href": "https://www.instagram.com/p/x/", "value": "👍",
                                                            "timestamp": T0 + 70}]}]
    export = instagram_export(tmp_path / "instagram-matthias-2024.zip", posts, {"anna_1": thread}, likes)
    from backend.datagatherer import raw_store
    assert registry.get("instagram").detect(export, raw_store.read_zip_names(export)) == "dyi"
    assert registry.get("facebook").detect(export, raw_store.read_zip_names(export)) is None
    _upload(client, app, "instagram", export)
    media = _rows(app, "SELECT kind, body, media FROM social_items WHERE source = 'instagram' AND kind = 'media'")
    assert media[0][1] == "Zee 🌊"
    assert json.loads(media[0][2])[0]["metadata"]["photo_metadata"]["exif_data"][0]["latitude"] == 51.2
    msgs = _rows(app, "SELECT source, body, is_me FROM msg_messages")
    assert msgs == [("instagram", "Mooie foto!", 0)]


def test_an_account_that_is_no_longer_active_makes_its_data_complete(client, app, tmp_path):
    posts = [{"media": [{"uri": "media/posts/202406/1.jpg", "creation_timestamp": T0 + 50, "title": "Zee"}],
              "title": "Zee", "creation_timestamp": T0 + 50}]
    _upload(client, app, "instagram", instagram_export(tmp_path / "instagram-oud.account-2024-06-02-AbC123.zip", posts))
    card = client.get("/api/datagatherer/sources/instagram").json()
    assert card["status"] == "achter" and not card["complete"]
    assert [(a["name"], a["records"], a["closed"]) for a in card["accounts"]] == [("oud.account", 1, False)]
    # Marked as no longer active: nothing new can come, so the card is up to date
    r = client.put("/api/datagatherer/sources/instagram/settings", json={"closed_accounts": ["oud.account"]})
    assert r.status_code == 200
    card = r.json()
    assert (card["status"], card["status_label"], card["complete"]) == ("actueel", "Actueel", True)
    assert card["accounts"][0]["closed"] and "oud.account is niet meer actief" in card["next_step"]
    # Active again: behind again
    card = client.put("/api/datagatherer/sources/instagram/settings", json={"closed_accounts": []}).json()
    assert card["status"] == "achter" and not card["accounts"][0]["closed"]
    assert client.put("/api/datagatherer/sources/instagram/settings",
                      json={"closed_accounts": "oud.account"}).status_code == 400


def test_an_export_of_an_unknown_account_keeps_the_card_honest(client, app, tmp_path):
    """A renamed export says nothing about its account: closing the other one does not make the card complete."""
    old = [{"media": [{"uri": "media/posts/202406/1.jpg", "creation_timestamp": T0 + 50, "title": "Zee"}],
            "title": "Zee", "creation_timestamp": T0 + 50}]
    _upload(client, app, "instagram", instagram_export(tmp_path / "instagram-oud-2024-06-02-AbC123.zip", old))
    later = [{"media": [{"uri": "media/posts/202406/2.jpg", "creation_timestamp": T0 + 90, "title": "Bos"}],
              "title": "Bos", "creation_timestamp": T0 + 90}]
    _upload(client, app, "instagram", instagram_export(tmp_path / "mijn export.zip", later))
    card = client.put("/api/datagatherer/sources/instagram/settings", json={"closed_accounts": ["oud"]}).json()
    assert [a["name"] for a in card["accounts"]] == ["oud"]
    assert card["status"] == "achter" and not card["complete"]


# ---------- WhatsApp ----------

def _mac_whatsapp(wal=False):
    container = _home() / "Library/Group Containers/group.net.whatsapp.WhatsApp.shared"
    container.mkdir(parents=True, exist_ok=True)
    chats = [{"pk": 1, "jid": "32470000001@s.whatsapp.net", "name": "Anna Jansen"},
             {"pk": 2, "jid": "120363000@g.us", "name": "Familie", "group": True,
              "members": [("32470000002@s.whatsapp.net", "Bob de Bouwer")]}]
    base = T0 + 36000      # 2024-06-01 10:00:00 UTC = 12:00 in Brussels
    msgs = [
        {"pk": 1, "chat": 1, "text": "Hoi! Zin in koffie?", "ts": base + 60, "stanza": "A1"},
        {"pk": 2, "chat": 1, "me": True, "text": "Ja, graag\nom 11 uur?", "ts": base + 150, "stanza": "A2"},
        {"pk": 3, "chat": 1, "ts": base + 300, "stanza": "A3", "media": "Media/32470000001@s.whatsapp.net/a/b/foto.jpg"},
        {"pk": 4, "chat": 2, "text": "Wie komt zondag?", "ts": base + 400, "stanza": "G1", "member": 200},
    ]
    later = [{"pk": 5, "chat": 1, "text": "Alleen in de WAL", "ts": base + 900, "stanza": "A5"}] if wal else None
    return make_chatstorage(container / "ChatStorage.sqlite", chats, msgs, later), container


def test_whatsapp_for_mac_is_read_from_a_snapshot_with_its_wal(client, app):
    live, container = _mac_whatsapp(wal=True)
    try:
        assert Path(str(container / "ChatStorage.sqlite") + "-wal").exists()
        job, result = _sync(client, app, "whatsapp")
        assert job["status"] == "done", job
        assert result["added"] == 5 and result["via"] == "live"
        assert _rows(app, "SELECT body FROM msg_messages WHERE body = 'Alleen in de WAL'")
        # the live database was only read
        assert live.execute("SELECT COUNT(*) FROM ZWAMESSAGE").fetchone()[0] == 5
    finally:
        live.close()
    group = _rows(app, "SELECT m.sender FROM msg_messages m JOIN msg_conversations c ON c.id = m.conv_id "
                       "WHERE c.title = 'Familie'")
    assert group == [("Bob de Bouwer",)]
    att = json.loads(_rows(app, "SELECT attachments FROM msg_messages WHERE msg_key LIKE '%A3%'")[0][0])
    assert att[0]["name"] == "foto.jpg" and att[0]["local"] is True and att[0]["path"].startswith(str(container))
    # the snapshot in the cache is gone, a second sync adds nothing
    assert not list(local_db.inbox(app.state.config, "whatsapp").glob("live-*"))
    job, result = _sync(client, app, "whatsapp")
    assert result["added"] == 0


def test_whatsapp_chat_export_merges_into_the_database_chat(client, app, tmp_path, monkeypatch):
    monkeypatch.setenv("TZ", "Europe/Brussels")
    live, _ = _mac_whatsapp()
    _sync(client, app, "whatsapp")
    # The export of the same chat (Dutch iPhone), plus a message the Mac never had
    text = ("[01-06-2024 12:01:00] Anna Jansen: Hoi! Zin in koffie?\n"
            "[01-06-2024 12:02:30] Matthias: Ja, graag\nom 11 uur?\n"
            "[20-05-2024 09:00:00] Anna Jansen: Een oud bericht dat alleen in de export staat\n")
    export = whatsapp_zip(tmp_path / "WhatsApp Chat - Anna Jansen.zip", text)
    from backend.datagatherer import raw_store
    assert registry.get("whatsapp").detect(export, raw_store.read_zip_names(export)) == "chat"
    _upload(client, app, "whatsapp", export)
    convs = client.get("/api/messages/conversations?source=whatsapp").json()["conversations"]
    anna = [c for c in convs if c["title"] == "Anna Jansen"]
    assert len(anna) == 1 and anna[0]["count"] == 4        # 3 from the Mac + 1 only in the export
    thread = client.get(f"/api/messages/conversations/{anna[0]['id']}").json()["messages"]
    bodies = [m["body"] for m in thread]
    assert bodies.count("Hoi! Zin in koffie?") == 1 and "Een oud bericht dat alleen in de export staat" in bodies
    assert thread[0]["precision"] == "s" and thread[0]["origin"] == "whatsapp_txt"
    superseded = _rows(app, "SELECT COUNT(*) FROM msg_messages WHERE superseded_by IS NOT NULL")[0][0]
    assert superseded == 2                                  # marked, never deleted
    # The export again: nothing new, nothing doubled
    _upload(client, app, "whatsapp", export, name="WhatsApp Chat - Anna Jansen (1).zip")
    assert client.get(f"/api/messages/conversations/{anna[0]['id']}").json()["conversation"]["count"] == 4


def test_a_newer_chat_export_with_the_same_name_on_the_same_day_is_one_chat(client, app, tmp_path):
    from backend.datagatherer import raw_store
    lines = "".join(f"[01-06-2024 10:{i:02d}:00] Anna Jansen: Bericht {i}\n" for i in range(10))
    newer = lines + "[02-06-2024 09:00:00] Anna Jansen: Nieuw\n"
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    _upload(client, app, "whatsapp", whatsapp_zip(tmp_path / "a" / "WhatsApp Chat - Anna.zip", lines))
    result = _upload(client, app, "whatsapp", whatsapp_zip(tmp_path / "b" / "WhatsApp Chat - Anna.zip", newer))
    assert result["import_result"]["added"] == 1
    paths = [r[0] for r in _rows(app, "SELECT path FROM dg_raw_imports WHERE source_id = 'whatsapp' ORDER BY id")]
    assert Path(paths[1]).name == "WhatsApp Chat - Anna 2.zip"         # the copy got a free name
    assert _rows(app, "SELECT title, message_count FROM msg_conversations") == [("Anna", 11)]
    # A bare _chat.txt twice on one day: the copy's ' 2' is no part of the chat's name either
    (tmp_path / "c").mkdir()
    (tmp_path / "d").mkdir()
    bob = "[03-06-2024 10:00:00] Bob: Hoi\n"
    (tmp_path / "c" / "_chat.txt").write_text(bob, encoding="utf-8")
    (tmp_path / "d" / "_chat.txt").write_text(bob + "[03-06-2024 10:05:00] Bob: Nog iets\n", encoding="utf-8")
    _upload(client, app, "whatsapp", tmp_path / "c" / "_chat.txt")
    _upload(client, app, "whatsapp", tmp_path / "d" / "_chat.txt")
    assert ("_chat", 2) in _rows(app, "SELECT title, message_count FROM msg_conversations")
    assert len(_rows(app, "SELECT id FROM msg_messages")) == 13
    # Without a manifest the copy's suffix is only dropped when the plain name is there too
    raw = {"path": paths[1], "original_path": None}
    assert raw_store.original_name(raw) == "WhatsApp Chat - Anna.zip"
    assert raw_store.original_name({"path": str(tmp_path / "Team 2.zip"), "original_path": None}) == "Team 2.zip"


def test_iphone_chatstorage_adds_only_what_the_mac_misses(client, app, tmp_path):
    _mac_whatsapp()
    _sync(client, app, "whatsapp")
    # An extracted iPhone ChatStorage.sqlite: the same stanza ids plus older history
    phone = tmp_path / "ChatStorage.sqlite"
    make_chatstorage(phone, [{"pk": 7, "jid": "32470000001@s.whatsapp.net", "name": "Anna Jansen"}], [
        {"pk": 70, "chat": 7, "text": "Hoi! Zin in koffie?", "ts": T0 + 36060, "stanza": "A1"},
        {"pk": 71, "chat": 7, "text": "Van vorig jaar", "ts": T0 - 86400 * 300, "stanza": "OLD1"},
    ])
    before = phone.read_bytes()
    assert registry.get("whatsapp").detect(phone, None) == "backup"
    result = _upload(client, app, "whatsapp", phone)
    assert result["import_result"]["added"] == 1
    archived = Path(_rows(app, "SELECT path FROM dg_raw_imports WHERE kind = 'backup'")[0][0])
    assert archived.read_bytes() == before                  # opened read-only, immutable
    assert not Path(str(archived) + "-wal").exists() and not Path(str(archived) + "-shm").exists()


@pytest.mark.parametrize("folder,name", [("WhatsApp #2", "ChatStorage #2.sqlite"),
                                         ("export? 2", "ChatStorage?x.sqlite"),
                                         ("map %41", "ChatStorage %41.sqlite")])
def test_whatsapp_paths_with_hash_question_mark_or_percent_are_read_and_nothing_is_created(
        client, app, tmp_path, folder, name):
    from urllib.parse import quote
    where = tmp_path / folder
    where.mkdir()
    phone = where / name
    make_chatstorage(phone, [{"pk": 7, "jid": "32470000001@s.whatsapp.net", "name": "Anna Jansen"}], [
        {"pk": 70, "chat": 7, "text": "Hoi met een raar pad", "ts": T0 + 36060, "stanza": "R1"},
    ])
    before = sorted(p.name for p in tmp_path.iterdir()), sorted(p.name for p in where.iterdir())
    # What the Downloads watcher does: detect it in place
    assert registry.get("whatsapp").detect(phone, None) == "backup"
    assert (sorted(p.name for p in tmp_path.iterdir()), sorted(p.name for p in where.iterdir())) == before
    # Uploaded under its odd name it is archived and its message read
    result = _upload(client, app, "whatsapp", phone, name=quote(name))
    assert result["import_result"]["added"] == 1
    assert _rows(app, "SELECT body FROM msg_messages WHERE body = 'Hoi met een raar pad'")
    archived = Path(_rows(app, "SELECT path FROM dg_raw_imports WHERE kind = 'backup'")[0][0])
    siblings = sorted(p.name for p in archived.parent.iterdir())
    assert archived.name == name                          # the odd name itself is opened
    assert siblings == sorted([archived.name, "datavault-import.json"]), siblings   # no stray empty file
    # Imported again straight from the export folder: still nothing new written there
    r = client.post("/api/datagatherer/sources/whatsapp/import")
    assert r.status_code in (200, 201), r.text
    assert _wait(app, r.json()["job"]["id"])["status"] == "done"
    assert sorted(p.name for p in archived.parent.iterdir()) == siblings


def test_whatsapp_card_asks_for_full_disk_access_and_the_helper_route_works(client, app, tmp_path):
    live, container = _mac_whatsapp()
    db_file = container / "ChatStorage.sqlite"
    card = next(s for s in client.get("/api/datagatherer/sources").json()["sources"] if s["id"] == "whatsapp")
    assert card["access"] is None and card["primary"]["action"] == "sync"
    os.chmod(db_file, 0)
    try:
        cards.forget_existing_cache()
        card = next(s for s in client.get("/api/datagatherer/sources").json()["sources"] if s["id"] == "whatsapp")
        assert card["status"] == "toegang_nodig" and card["primary"]["action"] == "access"
        assert card["status_label"] == "Toegang nodig"
        assert card["access"]["settings_path"] == local_db.FDA_SETTINGS_PATH
        assert "Volledige schijftoegang" in card["access"]["settings_path"]
        assert "fda_helper.py" in card["access"]["command"] and "--bron whatsapp" in card["access"]["command"]
        job, _ = _sync(client, app, "whatsapp")
        assert job["status"] == "failed"
        # The helper (run by the user from a Terminal with Full Disk Access) copies it into the inbox
        from backend.datagatherer import fda_helper
        readable = tmp_path / "helper-home"
        (readable / "Library/Group Containers/group.net.whatsapp.WhatsApp.shared").mkdir(parents=True)
        os.chmod(db_file, 0o600)
        import shutil
        shutil.copyfile(db_file, readable / local_db.LIVE_PATHS["whatsapp"])
        os.chmod(db_file, 0)
        assert fda_helper.main(["--bron", "whatsapp", "--naar", str(local_db.inbox(app.state.config)),
                                "--home", str(readable)]) == 0
        card = next(s for s in client.get("/api/datagatherer/sources").json()["sources"] if s["id"] == "whatsapp")
        assert card["access"] is None
        job, result = _sync(client, app, "whatsapp")
        assert job["status"] == "done" and result["via"] == "helper" and result["added"] == 4
        assert local_db.helper_snapshots(app.state.config, "whatsapp") == []     # cleaned up after reading
    finally:
        os.chmod(db_file, 0o600)


def test_helper_matches_the_server_and_never_overwrites(tmp_path):
    from backend.datagatherer import fda_helper
    assert {k: v[1] for k, v in fda_helper.SOURCES.items()} == local_db.LIVE_PATHS
    assert fda_helper.main(["--bron", "imessage", "--naar", str(tmp_path / "elders"), "--home", str(tmp_path)]) == 2
    home = tmp_path / "home"
    (home / "Library/Messages").mkdir(parents=True)
    (home / "Library/Messages/chat.db").write_bytes(b"x")
    (home / "Library/Messages/chat.db-wal").write_bytes(b"w")
    dest = tmp_path / "datagatherer-local"
    assert fda_helper.main(["--bron", "imessage", "--naar", str(dest), "--home", str(home)]) == 0
    assert fda_helper.main(["--bron", "imessage", "--naar", str(dest), "--home", str(home)]) == 0
    copies = sorted((dest / "imessage").iterdir())
    assert len(copies) == 2 and all((c / "ready.json").is_file() and (c / "chat.db-wal").is_file() for c in copies)
    assert (home / "Library/Messages/chat.db").read_bytes() == b"x"


# ---------- iMessage ----------

def _messages_db():
    folder = _home() / "Library/Messages"
    folder.mkdir(parents=True, exist_ok=True)
    make_chat_db(folder / "chat.db", {1: "+32470000001", 2: "bob@example.com"}, [
        {"rowid": 1, "guid": "iMessage;-;+32470000001", "style": 45, "handles": [1]},
        {"rowid": 2, "guid": "iMessage;+;chat123", "style": 43, "name": "Weekend", "handles": [1, 2]},
    ], [
        {"rowid": 1, "guid": "G-1", "chat": 1, "handle": 1, "text": "Ben je thuis?", "ts": T0 + 10},
        {"rowid": 2, "guid": "G-2", "chat": 1, "me": True, "body": typedstream("Ja, kom maar 🙂"), "ts": T0 + 20},
        {"rowid": 3, "guid": "G-3", "chat": 1, "handle": 1, "assoc": "p:0/G-2", "assoc_type": 2000,
         "text": "Vond 'Ja, kom maar' leuk", "ts": T0 + 30},
        {"rowid": 4, "guid": "G-4", "chat": 2, "handle": 2, "text": "Foto van zaterdag", "ts": T0 + 40},
        {"rowid": 5, "guid": "G-5", "chat": 2, "handle": 1, "text": "Oud bericht", "ts": T0 - 86400 * 2000, "seconds": True},
        # iOS 18: any emoji as a tapback, and a tapback with an emoji taken back
        {"rowid": 6, "guid": "G-6", "chat": 1, "handle": 1, "assoc": "p:0/G-1", "assoc_type": 2006, "emoji": "🎉",
         "text": "Reageerde met 🎉 op “Ben je thuis?”", "ts": T0 + 50},
        {"rowid": 7, "guid": "G-7", "chat": 1, "handle": 1, "assoc": "p:0/G-1", "assoc_type": 3006, "emoji": "🎉",
         "text": "Verwijderde 🎉", "ts": T0 + 55},
        # 'Undo send': no text left
        {"rowid": 8, "guid": "G-8", "chat": 1, "me": True, "ts": T0 + 60, "retracted": True},
    ], [{"rowid": 1, "message": 4, "filename": "~/Library/Messages/Attachments/ab/01/X/IMG_0001.HEIC",
         "mime": "image/heic", "transfer": "IMG_0001.HEIC"}])


def test_an_update_keeps_the_database_itself_in_the_vault_once_a_week(client, app):
    _messages_db()
    job, result = _sync(client, app, "imessage")
    assert job["status"] == "done", job
    from backend.datagatherer import raw_store
    folder = raw_store.owner_folder(app.state.config) / "iMessage"
    kept = folder / f"{time.strftime('%Y-%m-%d')} chat.db"
    assert result["kept"] == str(kept) and os.listdir(folder) == [kept.name]   # one file: no -wal, no partial
    assert not os.access(kept, os.W_OK)
    with sqlite3.connect(f"file:{kept}?mode=ro", uri=True) as conn:
        assert conn.execute("SELECT count(*) FROM message").fetchone()[0] == 8
    # The same week: no second copy, and today's copy is never replaced
    before = kept.stat().st_mtime_ns
    job, result = _sync(client, app, "imessage")
    assert job["status"] == "done" and result["kept"] is None and kept.stat().st_mtime_ns == before
    # A week later: a new one next to it
    copy = _home() / "copy.db"
    import shutil
    shutil.copyfile(_home() / "Library/Messages/chat.db", copy)
    assert local_db.keep_copy(folder.parent, "iMessage", "imessage", copy, now=time.time() + 3 * 86400) is None
    assert local_db.keep_copy(folder.parent, "iMessage", "imessage", copy, now=time.time() + 8 * 86400) is not None
    assert len(os.listdir(folder)) == 2


def test_after_a_helper_import_the_card_offers_bijwerken_not_toegang_geven(client, app, tmp_path):
    _messages_db()
    db_file = _home() / "Library/Messages/chat.db"
    from backend.datagatherer import fda_helper
    readable = tmp_path / "helper-home"
    (readable / "Library/Messages").mkdir(parents=True)
    import shutil
    shutil.copyfile(db_file, readable / local_db.LIVE_PATHS["imessage"])
    os.chmod(db_file, 0)
    try:
        assert fda_helper.main(["--bron", "imessage", "--naar", str(local_db.inbox(app.state.config)),
                                "--home", str(readable)]) == 0
        job, result = _sync(client, app, "imessage")
        assert job["status"] == "done" and result["via"] == "helper"
        cards.forget_existing_cache()
        card = next(s for s in client.get("/api/datagatherer/sources").json()["sources"] if s["id"] == "imessage")
        # The copy is cleaned up, so the server cannot read anything now: that is no failure
        assert card["status"] != "toegang_nodig" and card["primary"]["action"] == "access"
        assert card["primary"]["label"] == "Bijwerken" and card["access"]["title"] == "Bijwerken met de hulp"
        assert card["next_step"].startswith("Ingelezen tot ") and "Toegang nodig" not in card["next_step"]
        assert "voor nieuwe berichten start je de hulp opnieuw" in card["next_step"]
    finally:
        os.chmod(db_file, 0o600)


def test_imessage_reads_attributed_body_dates_tapbacks_and_attachments(client, app):
    _messages_db()
    job, result = _sync(client, app, "imessage")
    assert job["status"] == "done", job
    assert result["added"] == 5
    rows = {r[0]: r[1:] for r in _rows(app, "SELECT msg_key, body, is_me, sent_at_ms, reactions, attachments, sender, "
                                            "kind FROM msg_messages")}
    # 🎉 was taken back (3006): kept as removed, not shown
    assert json.loads(rows["G-1"][3]) == [{"actor": "+32470000001", "reaction": "🎉", "removed": True}]
    assert "G-6" not in rows and "G-7" not in rows and rows["G-8"][0] is None and rows["G-8"][6] == "unsent"
    assert rows["G-2"][0] == "Ja, kom maar 🙂" and rows["G-2"][1] == 1
    assert rows["G-1"][2] == (T0 + 10) * 1000 and rows["G-5"][2] == (T0 - 86400 * 2000) * 1000
    assert json.loads(rows["G-2"][3]) == [{"actor": "+32470000001", "reaction": "❤️"}]
    assert "G-3" not in rows                               # a tapback is no message of its own
    assert json.loads(rows["G-4"][4])[0]["name"] == "IMG_0001.HEIC" and rows["G-4"][5] == "bob@example.com"
    convs = {c["title"]: c for c in client.get("/api/messages/conversations?source=imessage").json()["conversations"]}
    assert convs["Weekend"]["is_group"] is True and convs["+32470000001"]["count"] == 3
    job, result = _sync(client, app, "imessage")
    assert result["added"] == 0
    assert len(_rows(app, "SELECT id FROM msg_messages")) == 5


# ---------- search, Berichten API, Pasha ----------

def _two_sources(client, app, tmp_path):
    _messages_db()
    _sync(client, app, "imessage")
    _upload(client, app, "facebook", meta_export(tmp_path / "facebook.zip", "en", _fb_threads()))


def test_search_across_sources_with_a_filter_and_marked_snippets(client, app, tmp_path):
    _two_sources(client, app, tmp_path)
    data = client.get("/api/messages/search", params={"q": "cafe"}).json()   # without the accent, prefix match
    assert [r["source"] for r in data["results"]] == ["messenger"]
    start, end = data["marks"]
    assert f"{start}café{end}" in data["results"][0]["snippet"]
    both = client.get("/api/messages/search", params={"q": "kom"}).json()["results"]
    assert {r["source"] for r in both} == {"imessage", "messenger"}
    only = client.get("/api/messages/search", params={"q": "kom", "source": "imessage"}).json()["results"]
    assert {r["source"] for r in only} == {"imessage"}
    assert client.get("/api/messages/search", params={"q": "kom", "source": "telegram"}).status_code == 400
    # the thread around a hit
    hit = only[0]
    thread = client.get(f"/api/messages/conversations/{hit['conversation']}", params={"around": hit["id"]}).json()
    assert any(m["id"] == hit["id"] for m in thread["messages"])
    sources = {s["id"]: s for s in client.get("/api/messages/sources").json()["sources"]}
    assert sources["imessage"]["messages"] == 5 and sources["messenger"]["conversations"] == 2
    assert sources["whatsapp"]["messages"] == 0 and all(s["pasha"] is False for s in sources.values())


def test_pasha_reads_messages_only_from_sources_the_user_switched_on(client, app, tmp_path):
    from backend.routes import chat
    _two_sources(client, app, tmp_path)
    question = "Wat zei Anna over het café?"
    assert "café" not in chat.build_system_prompt(question)               # off by default
    r = client.put("/api/datagatherer/sources/imessage/settings", json={"settings": {"pasha": True}})
    assert r.status_code == 200
    prompt = chat.build_system_prompt(question)
    assert "Berichten" in prompt and "imessage" in prompt and "Zin in een café" not in prompt   # Messenger still off
    client.put("/api/datagatherer/sources/messenger/settings", json={"settings": {"pasha": True}})
    assert "Zin in een café" in chat.build_system_prompt(question)
    assert client.put("/api/datagatherer/sources/messenger/settings",
                      json={"settings": {"pasha": "ja"}}).status_code == 400
    client.put("/api/datagatherer/sources/messenger/settings", json={"settings": {"pasha": False}})
    client.put("/api/datagatherer/sources/imessage/settings", json={"settings": {"pasha": False}})
    assert "Berichten (" not in chat.build_system_prompt(question)


# ---------- a synced cloud folder for Meta's recurring export ----------

def test_a_synced_folder_is_watched_without_an_open_request(client, app, tmp_path):
    synced = tmp_path / "Dropbox" / "Meta"
    synced.mkdir(parents=True)
    vault_folder = Path(app.state.config.VAULT_ROOT) / "RAW"
    assert client.put("/api/datagatherer/sources/facebook/settings",
                      json={"settings": {"watch_folder": str(vault_folder)}}).status_code == 400
    assert client.put("/api/datagatherer/sources/facebook/settings",
                      json={"settings": {"watch_folder": str(tmp_path / "bestaat-niet")}}).status_code == 400
    assert client.put("/api/datagatherer/sources/facebook/settings",
                      json={"settings": {"watch_folder": str(synced)}}).status_code == 200
    export = meta_export(synced / "facebook-week-23.zip", "en", _fb_threads())
    old = time.time() - 120
    os.utime(export, (old, old))
    assert state.open_requests(app.state.db) == []
    d = daemon.Daemon(app.state.config, app.state.db, lambda: app.state.jobs)
    started = d.tick()
    assert started["watch"] == [str(export)]
    jobs = [j for j in app.state.jobs.list() if j["type"] == "datagatherer.watch"]
    _wait(app, jobs[0]["id"])
    for job in app.state.jobs.list():
        _wait(app, job["id"])
    assert len(_rows(app, "SELECT id FROM msg_messages")) == 3
    card = next(s for s in client.get("/api/datagatherer/sources").json()["sources"] if s["id"] == "facebook")
    assert any("gesynchroniseerde map" in n["text"] for n in card["notes"])
    assert export.exists()                                  # never moved or removed
