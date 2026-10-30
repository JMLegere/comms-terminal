"""Attachments copied on arrival (.agents/prd/2026-10-15-attachments.md P2): Gmail parts, Slack and Quo
downloads, the backfill, and expiry."""

import hashlib
import hmac
import json
import urllib.error
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

from comms_terminal import email, files, inbox, main, quo, render, serve, slack

NOW = datetime(2026, 10, 15, 15, 0, tzinfo=timezone.utc)
PDF, PNG = b"%PDF-1.7 contract", b"\x89PNG logo"


def connect(tmp_path, platform="gmail", external="me@x.io", self_id="me@x.io"):
    db = inbox.connect(str(tmp_path / "inbox.db"))
    inbox.migrate(db)
    with db:
        db.execute("INSERT INTO connections (id, platform, external_id, self_id, label, connected_at)"
                   " VALUES ((SELECT printf('%d', count(*) + 1) FROM connections), ?, ?, ?, ?, ?)", (platform, external, self_id, platform, NOW.isoformat()))
    return db, db.execute("SELECT * FROM connections").fetchone()


def gmail_raw(mid="<c1@acme.com>"):
    m = EmailMessage()
    m["From"], m["To"], m["Subject"], m["Message-ID"] = "Ana <ana@acme.com>", "me@x.io", "Contract", mid
    m.set_content("see attached")
    m.add_alternative('<p>see <img src="cid:logo1"></p>', subtype="html")
    m.get_payload()[1].add_related(PNG, "image", "png", cid="<logo1>", filename="logo.png")
    m.add_attachment(PDF, maintype="application", subtype="pdf", filename="contract.pdf")
    m.add_attachment(b"BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n", maintype="application", subtype="ics",
                     filename="invite.ics")
    return bytes(m)


def post_email(db, cid, raw):
    ts = str(int(NOW.timestamp()))
    sig = "sha256=" + hmac.new(b"k", ts.encode() + b"." + raw, hashlib.sha256).hexdigest()
    connection = db.execute("SELECT * FROM connections WHERE id = ?", (cid,)).fetchone()
    return email.handle_hook(db, connection, {"X-Inbox-Timestamp": ts, "X-Inbox-Signature": sig}, raw, b"k", NOW)


def rows(db):
    return [dict(r) for r in db.execute("SELECT * FROM attachments ORDER BY id")]


def test_gmail_parts_are_stored_with_cid_images_inline_and_ics_skipped(tmp_path):
    db, conn = connect(tmp_path)
    raw = gmail_raw()
    assert render.from_raw(raw).attachments == [("contract.pdf", "application/pdf", len(PDF))]  # unchanged
    assert post_email(db, conn["id"], raw) == 204
    got = rows(db)
    assert [(r["position"], r["name"], r["type"], r["size"], r["inline"], r["content_id"], r["state"])
            for r in got] == [(0, "logo.png", "image/png", len(PNG), 1, "logo1", "stored"),
                              (1, "contract.pdf", "application/pdf", len(PDF), 0, None, "stored")]
    assert files.attachment_path(db, got[1]["sha"]).read_bytes() == PDF
    assert json.loads(got[1]["source"]) == {"raw": hashlib.sha256(raw).hexdigest(), "part": 1}
    assert post_email(db, conn["id"], raw) == 204  # the Worker retries: a duplicate adds nothing
    assert len(rows(db)) == 2


def test_nameless_cid_images_are_inline_parts_named_from_their_cid():
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = "Ana <ana@acme.com>", "me@x.io", "Look"
    m.set_content('<p>here <img src="cid:shot1@x"></p>', subtype="html")
    m.add_related(PNG, "image", "png", cid="<shot1@x>")
    del m.get_payload()[1]["Content-Disposition"]
    raw = bytes(m)
    assert render.parts(raw) == [render.Part("shot1_x.png", "image/png", PNG, True, "shot1@x")]
    assert render.from_raw(raw).attachments == []


def test_fastmail_copies_through_the_hook_keep_link_only_chips(tmp_path):
    db, conn = connect(tmp_path, "fastmail")
    post_email(db, conn["id"], gmail_raw())
    assert rows(db) == []


def test_backfill_stores_parts_of_kept_messages_once(tmp_path, capsys):
    db, conn = connect(tmp_path)
    raw = gmail_raw()
    inc = email.parse(raw, conn)
    inc.data["raw"] = files.store_raw(db, raw)
    inbox.ingest(db, inc)  # a message from before this release: raw kept, no Attachments
    assert rows(db) == []
    main.main(["--db", str(tmp_path / "inbox.db"), "attachments-backfill"])
    assert capsys.readouterr().out == "Gmail: read 1 messages without Attachments, stored 2 Attachments\n"
    first = rows(db)
    assert [r["name"] for r in first] == ["logo.png", "contract.pdf"]
    assert email.backfill_parts(db) == (0, 0) and rows(db) == first


class Client:
    def real_name(self, user):
        return None

    def user_email(self, user):
        return None

    def name_mentions(self, text):
        return text

    def conversation_label(self, channel, kind):
        return None


def slack_event(ts="100.1", files=None):
    return {"type": "message", "subtype": "file_share", "channel": "D1", "channel_type": "im", "user": "U1",
            "text": "deck", "ts": ts, "event_ts": ts, "files": files or [
                {"id": "F1", "name": "deck.pdf", "mimetype": "application/pdf", "size": 1200,
                 "url_private": "https://files.slack.com/files-pri/T1-F1/deck.pdf",
                 "url_private_download": "https://files.slack.com/files-pri/T1-F1/download/deck.pdf"},
                {"id": "F2", "name": "shot.png", "mimetype": "image/png", "size": 300,
                 "url_private": "https://files.slack.com/files-pri/T1-F2/shot.png"}]}


def slack_db(tmp_path):
    db, conn = connect(tmp_path, "slack", "T1", "U0ME")
    t = inbox.ingest(db, slack.parse(slack_event(), conn, [], Client()))
    return db, conn, t


def test_slack_keeps_file_references_and_waits_to_download(tmp_path):
    db, conn, t = slack_db(tmp_path)
    data = json.loads(db.execute("SELECT data FROM activities").fetchone()[0])
    assert data["attachments"][0] == ["deck.pdf", "application/pdf", 1200, {
        "id": "F1", "url": "https://files.slack.com/files-pri/T1-F1/download/deck.pdf"}]
    assert [(r["name"], r["state"], json.loads(r["source"])) for r in rows(db)] == [
        ("deck.pdf", "pending", {"id": "F1", "url": "https://files.slack.com/files-pri/T1-F1/download/deck.pdf"}),
        ("shot.png", "pending", {"id": "F2", "url": "https://files.slack.com/files-pri/T1-F2/shot.png"})]
    msg = inbox.thread_view(db, t.id)["messages"][0]
    assert [(a["name"], a["type"], a["size"]) for a in msg["attachments"]] == [("deck.pdf", "application/pdf", 1200), ("shot.png", "image/png", 300)]
    # Slack data from before this release has no file ids: link-only, no rows
    inbox.ingest(db, inbox.Incoming(conn["id"], "D1", "im", "MESSAGE", "D1:99.1", None, NOW, "old",
                                    data={"channel": "D1", "ts": "99.1", "attachments": [["a.pdf", "application/pdf", 9]]}))
    assert len(rows(db)) == 2


def test_quo_media_wait_to_download(tmp_path):
    db, conn = connect(tmp_path, "quo", "+15068324154", "+15068324154")
    event = {"type": "message.received", "data": {
        "resource": {"id": "AC3", "direction": "incoming", "text": "", "createdAt": "2026-10-12T21:18:03Z",
                     "media": [{"type": "image/jpeg", "url": "https://share.quo.com/AC3"}]},
        "context": {"senderIdentifier": "+15066092430", "recipientIdentifiers": ["+15068324154"]}}}
    inbox.ingest(db, quo.parse(event, conn))
    assert [(r["name"], r["type"], r["size"], r["state"], json.loads(r["source"])) for r in rows(db)] == [
        ("photo", "image/jpeg", 0, "pending", {"url": "https://share.quo.com/AC3"})]
    inbox.ingest(db, quo.parse(event, conn))  # Quo retries: no second row
    assert len(rows(db)) == 1
    calls = []
    serve.fetch_pending(db, {"quo": lambda url: calls.append(url) or (200, "image/jpeg", b"JPEG")})
    assert calls == ["https://share.quo.com/AC3"]
    assert [(r["state"], r["size"]) for r in rows(db)] == [("stored", 4)]  # its real size replaces 0


def test_downloads_store_files_and_count_failures(tmp_path):
    db, _, _ = slack_db(tmp_path)
    answers = {"deck": (200, "application/pdf", PDF), "shot": (403, "", b"")}

    def get(url):
        return answers["deck" if "deck" in url else "shot"]

    assert serve.fetch_pending(db, {}) == 0 and [r["tries"] for r in rows(db)] == [0, 0]  # no token: wait
    assert serve.fetch_pending(db, {"slack": get}) == 1
    deck, shot = rows(db)
    assert deck["state"] == "stored" and files.attachment_path(db, deck["sha"]).read_bytes() == PDF
    assert (shot["state"], shot["tries"]) == ("pending", 1)
    answers["shot"] = (200, "text/html", b"<!DOCTYPE html><title>Slack sign in</title>")  # not the file
    serve.fetch_pending(db, {"slack": get})
    assert (rows(db)[1]["state"], rows(db)[1]["tries"]) == ("pending", 2)

    def broken(url):
        raise urllib.error.URLError("down")
    for _ in range(inbox.ATTACHMENT_TRIES - 2):
        serve.fetch_pending(db, {"slack": broken})
    assert rows(db)[1]["state"] == "failed" and inbox.pending_attachments(db) == []


def test_html_without_a_content_type_and_oversize_files_are_refused(tmp_path):
    db, _, _ = slack_db(tmp_path)
    with db:
        db.execute("UPDATE attachments SET size = ? WHERE name = 'deck.pdf'", (files.MAX_FILE + 1,))
    calls = []
    serve.fetch_pending(db, {"slack": lambda url: calls.append(url) or (200, "", b"  <html><body>login")})
    assert len(calls) == 1 and [(r["state"], r["tries"]) for r in rows(db)] == [("pending", 1), ("pending", 1)]


def test_expiry_deletes_year_old_files_unless_newer_or_staged(tmp_path):
    db, conn = connect(tmp_path)
    old, new = NOW - timedelta(days=366), NOW - timedelta(days=10)

    def message(mid, at, *blobs):
        t = inbox.ingest(db, inbox.Incoming(conn["id"], "me@x.io", "mailbox", "MESSAGE", mid,
                                            inbox.Identity("email", "ana@acme.com"), at, "hi"))
        aid = db.execute("SELECT id FROM activities WHERE native_id = ?", (mid,)).fetchone()[0]
        inbox.add_attachments(db, aid, [{"name": "f", "type": "application/pdf", "size": len(b),
                                         "sha": files.store_file(db, b)} for b in blobs])
        return t

    message("<a@x>", old, b"gone", b"shared", b"staged")
    message("<b@x>", new, b"shared")
    t = message("<c@x>", old, b"other")
    inbox.stage(db, sha=hashlib.sha256(b"staged").hexdigest(), name="f", type="application/pdf", size=6,
                thread_id=t.id)
    assert inbox.expire_attachments(db, NOW) == 2  # "gone" and "other"
    states = {(r["position"], r["activity_id"]): (r["state"], r["sha"]) for r in rows(db)}
    assert [s for s, _ in states.values()] == ["expired", "stored", "stored", "stored", "expired"]
    assert files.attachment_path(db, hashlib.sha256(b"gone").hexdigest()).exists() is False
    assert files.attachment_path(db, hashlib.sha256(b"shared").hexdigest()).exists()
    assert files.attachment_path(db, hashlib.sha256(b"staged").hexdigest()).exists()
    assert inbox.expire_attachments(db, NOW) == 0
