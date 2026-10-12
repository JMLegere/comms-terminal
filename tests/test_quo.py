"""Quo SMS Connection: signature check, Texts to Threads, replies, and the echo of a console reply."""

import base64
import hashlib
import hmac
import json
from datetime import datetime, timezone

from universal_inbox import inbox, quo

KEY = b"whsec_" + base64.b64encode(b"secret-bytes")
NOW = datetime(2026, 10, 12, 21, 20, tzinfo=timezone.utc)
ME, ANA, BEN = "+15068324154", "+15066092430", "+15066093456"


def signed(body: bytes, ts: int = int(NOW.timestamp()), wid: str = "msg_1") -> dict:
    sig = base64.b64encode(hmac.new(b"secret-bytes", f"{wid}.{ts}.".encode() + body, hashlib.sha256).digest())
    return {"webhook-id": wid, "webhook-timestamp": str(ts), "webhook-signature": "v1,bogus v1," + sig.decode()}


def event(kind, mid, text, sender, recipients, at="2026-10-12T21:18:03.356Z", media=()):
    """The shape Quo delivered live on 2026-10-12 (Quo-Api-Version 2026-03-30)."""
    return json.dumps({"type": kind, "data": {
        "resource": {"id": mid, "direction": "incoming" if kind == "message.received" else "outgoing",
                     "text": text, "media": [{"type": t, "url": f"https://share.quo.com/{mid}"} for t in media],
                     "status": "received", "createdAt": at},
        "context": {"phoneNumberId": "PN2ppcGyMM", "conversationId": "CN1", "userId": "US1",
                    "senderIdentifier": sender, "recipientIdentifiers": recipients, "contacts": {"ids": []}},
        "links": {"quo": "https://my.quo.com/inbox/CN1"}}}).encode()


def setup():
    db = inbox.connect()
    inbox.migrate(db)
    with db:
        cid = db.execute("INSERT INTO connections (platform, external_id, self_id, label, connected_at)"
                         " VALUES ('quo', ?, ?, 'Quo', ?)", (ME, ME, NOW.isoformat())).lastrowid
    return db, cid


def hook(db, cid, body, wid="msg_1"):
    return quo.handle_hook(db, cid, signed(body, wid=wid), body, KEY, NOW)


def test_verify():
    body = b'{"type":"message.received"}'
    assert quo.verify(signed(body), body, KEY, NOW)
    assert not quo.verify(signed(body), body + b" ", KEY, NOW)  # tampered body
    assert not quo.verify(signed(body, ts=int(NOW.timestamp()) - 301), body, KEY, NOW)  # stale
    assert not quo.verify({}, body, KEY, NOW)
    # Standard Webhooks' published test vector
    vector = {"webhook-id": "msg_p5jXN8AQM9LWM0D4loKWxJek", "webhook-timestamp": "1614265330",
              "webhook-signature": "v1,g0hM9SsE+OTPJTGt/tmIKtSyZlE3uFJELVlNIOLJ1OE="}
    assert quo.verify(vector, b'{"test": 2432232314}', b"whsec_MfKQ9r8GKYqrTwjUPD8ILPZIo2LaLaSw",
                      datetime.fromtimestamp(1614265330, timezone.utc))


def test_texts_become_one_thread_and_a_phone_reply_answers_it():
    db, cid = setup()
    assert hook(db, cid, event("message.received", "AC1", "Need the drawings", ANA, [ME])) == 204
    rows = inbox.queue(db, "Todo")
    assert [(r["title"], r["detail"], r["where"]) for r in rows] == [("Need the drawings", "CREATED", "SMS")]
    assert [tuple(r) for r in db.execute("SELECT kind, value FROM identities")] == [("phone", ANA)]
    # Jeremy answers from the Quo app: message.delivered, no identity, same Thread, out of Todo
    hook(db, cid, event("message.delivered", "AC2", "Sending now", ME, [ANA], at="2026-10-12T21:19:00Z"), "msg_2")
    assert inbox.queue(db, "Todo") == []
    assert db.execute("SELECT count(*) FROM threads").fetchone()[0] == 1
    # Quo retries a delivery: stored once
    hook(db, cid, event("message.received", "AC1", "Need the drawings", ANA, [ME]))
    assert db.execute("SELECT count(*) FROM activities").fetchone()[0] == 2


def test_photo_is_an_attachment_and_group_texts_are_dropped():
    db, cid = setup()
    hook(db, cid, event("message.received", "AC3", "", ANA, [ME], media=["image/jpeg"]))
    data = json.loads(db.execute("SELECT data FROM activities").fetchone()[0])
    assert data["attachments"] == [["photo", "image/jpeg", 0]] and data["media"][0][1] == "https://share.quo.com/AC3"
    assert db.execute("SELECT title FROM threads").fetchone()[0] == "Photo"
    assert hook(db, cid, event("message.received", "AC4", "group", ANA, [ME, BEN])) == 204
    assert db.execute("SELECT count(*) FROM activities").fetchone()[0] == 1


def test_unsigned_or_unconnected():
    db, cid = setup()
    body = event("message.received", "AC1", "hi", ANA, [ME])
    assert quo.handle_hook(db, cid, {}, body, KEY, NOW) == 401
    assert quo.handle_hook(db, None, signed(body), body, KEY, NOW) == 503


def test_console_reply_sends_from_the_work_number_and_its_echo_is_not_stored_twice():
    db, cid = setup()
    hook(db, cid, event("message.received", "AC1", "Need the drawings", ANA, [ME]))
    sent = []

    def post(url, key, payload):
        sent.append((url, key, payload))
        return {"data": {"id": "AC9"}}

    tid = inbox.queue(db, "Todo")[0]["id"]
    inbox.reply(db, tid, "On it", quo.QuoSender("k", ME, post))
    assert sent == [(quo.SEND_URL, "k", {"content": "On it", "from": ME, "to": [ANA]})]
    hook(db, cid, event("message.delivered", "AC9", "On it", ME, [ANA], at="2026-10-12T21:19:30Z"), "msg_9")
    assert [r[0] for r in db.execute("SELECT native_id FROM activities ORDER BY id")] == ["AC1", "AC9"]
