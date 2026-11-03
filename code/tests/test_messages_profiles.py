"""
Berichten: where the other person of a chat can be found online (msg_profiles) and the
full-size photo for the viewer (/api/messages/attachments/...?size=full).

Instagram's export has display names only; the username comes from your reply to their
story, or from the chat folder when it matches one account you follow or that follows you.
Every export is synthetic.
"""
import json
import sqlite3
import zipfile
from pathlib import Path

from backend.datagatherer import messages_store, msg_profiles
from fake_messages import T0, fb_thread, tiny_jpeg

ME = "Matthias Voorbeeld"
INBOX = "your_instagram_activity/messages/inbox"


def _export(path: Path) -> Path:
    anna = fb_thread([ME, "Anna Jansen"], [
        {"sender_name": "Anna Jansen", "timestamp_ms": T0 * 1000, "content": "Haha",
         "share": {"link": "https://www.instagram.com/stories/matthias.voorbeeld/111"}},
        {"sender_name": ME, "timestamp_ms": T0 * 1000 + 1000, "content": "Mooi!",
         "share": {"link": "https://www.instagram.com/stories/anna.jansen.99/222"}},
        {"sender_name": "Anna Jansen", "timestamp_ms": T0 * 1000 + 2000,
         "photos": [{"uri": f"{INBOX}/annajansen_20001/photos/1.jpg", "creation_timestamp": T0 + 2}]},
    ], title="Anna Jansen")
    bob = fb_thread([ME, "Bob Peeters"], [
        {"sender_name": "Bob Peeters", "timestamp_ms": T0 * 1000, "content": "Hoi"}], title="Bob Peeters")
    carl = fb_thread([ME, "Carl Vos"], [
        {"sender_name": "Carl Vos", "timestamp_ms": T0 * 1000, "content": "Yo"}], title="Carl Vos")
    gone = fb_thread([ME, "Instagram user"], [
        {"sender_name": "Instagram user", "timestamp_ms": T0 * 1000, "content": "?"}], title="Instagram user")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("personal_information/personal_information/personal_information.json", json.dumps(
            {"profile_user": [{"string_map_data": {"Name": {"value": ME}}}]}))
        for folder, thread in (("annajansen_20001", anna), ("bobpeeters_20002", bob), ("carlvos_20003", carl),
                               ("instagramuser_20004", gone)):
            zf.writestr(f"{INBOX}/{folder}/message_1.json", json.dumps({**thread, "thread_path": f"inbox/{folder}"}))
        zf.writestr(f"{INBOX}/annajansen_20001/photos/1.jpg", tiny_jpeg())
        zf.writestr("connections/followers_and_following/following.json", json.dumps({"relationships_following": [
            {"title": "bob.peeters", "string_list_data": [{"href": "https://www.instagram.com/_u/bob.peeters"}]}]}))
        # An account whose squashed name is not a chat folder, and one that would be ambiguous
        zf.writestr("connections/followers_and_following/followers_1.json", json.dumps([
            {"string_list_data": [{"value": "carl_other"}]}, {"string_list_data": [{"value": "carl.vos.x"}]}]))
    return path


def _upload(client, app, path: Path):
    r = client.post(f"/api/datagatherer/sources/instagram/upload?name={path.name}", content=path.read_bytes())
    assert r.status_code in (200, 201), r.text
    job = r.json()["job"]
    assert app.state.jobs.wait(job["id"], timeout=30)["status"] == "done"
    result = app.state.jobs.result(job["id"])
    if result.get("import_job"):
        assert app.state.jobs.wait(result["import_job"], timeout=30)["status"] == "done"


def _convs(client):
    return {c["title"]: c for c in client.get("/api/messages/conversations?source=instagram").json()["conversations"]}


def test_instagram_profiles_come_from_story_replies_and_known_accounts(client, app, tmp_path):
    _upload(client, app, _export(tmp_path / "instagram-matthias.voorbeeld-2024-06-01-AbC123.zip"))
    convs = _convs(client)
    profile = lambda title: client.get(f"/api/messages/conversations/{convs[title]['id']}/profile").json()["profile"]

    # Your reply to her story names her; her reply to yours (your own account) does not
    assert profile("Anna Jansen") == {"handle": "anna.jansen.99", "how": "story", "site": "Instagram",
                                      "url": "https://www.instagram.com/anna.jansen.99/"}
    # The chat folder 'bobpeeters' is exactly one account you follow
    assert profile("Bob Peeters")["url"] == "https://www.instagram.com/bob.peeters/"
    assert profile("Bob Peeters")["how"] == "follows"
    # No username anywhere: only a web search for the name; a deleted account gets nothing
    carl = profile("Carl Vos")
    assert "url" not in carl and "Carl+Vos" in carl["search_url"] and "site%3Ainstagram.com" in carl["search_url"]
    assert profile("Instagram user") is None
    assert client.get("/api/messages/conversations/999999/profile").status_code == 404


def test_the_viewer_gets_the_photo_itself_and_the_bubble_a_preview(client, app, tmp_path):
    _upload(client, app, _export(tmp_path / "instagram-matthias.voorbeeld-2024-06-01-AbC123.zip"))
    with sqlite3.connect(str(app.state.config.DB_PATH)) as conn:
        msg_id = conn.execute("SELECT id FROM msg_messages WHERE attachments IS NOT NULL").fetchone()[0]
    full = client.get(f"/api/messages/attachments/{msg_id}/0?size=full")
    assert full.status_code == 200 and full.headers["content-type"] == "image/jpeg"
    assert full.content == tiny_jpeg()
    preview = client.get(f"/api/messages/attachments/{msg_id}/0")
    assert preview.status_code == 200 and preview.headers["content-type"] == "image/jpeg"
    assert client.get(f"/api/messages/attachments/{msg_id}/0?size=huge").status_code == 422


def test_a_whatsapp_chat_with_a_phone_number_opens_in_whatsapp(app):
    db = app.state.db
    by_number = messages_store.conversation(db, "whatsapp", "32470123456@s.whatsapp.net", title="Anna")
    by_lid = messages_store.conversation(db, "whatsapp", "105278926233604@lid", title="Bob")
    group = messages_store.conversation(db, "whatsapp", "120363@g.us", title="Familie", is_group=True)
    get = lambda cid: msg_profiles.profile(db, messages_store.get_conversation(db, cid))
    assert get(by_number) == {"site": "WhatsApp", "handle": "+32470123456", "how": "number",
                              "url": "https://wa.me/32470123456"}
    assert get(by_lid) is None and get(group) is None
