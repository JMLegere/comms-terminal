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
    v = inbox.thread_view(db, db.execute("SELECT id FROM threads").fetchone()[0])
    assert v["messages"][0]["attachments"] == [("photo", "image/jpeg", 0, "https://share.quo.com/AC3")]
    assert v["link"] == "https://my.quo.com/inbox/CN1" and v["where"] == f"SMS · {ANA}"
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


def call_event(kind, cid, peer, at="2026-10-12T21:18:00Z", **resource):
    """Call events as Quo's 2026-03-30 docs describe them (webhooks-event-payloads); untested live."""
    return json.dumps({"type": kind, "data": {
        "resource": {"id": cid, "createdAt": at, **resource},
        "context": {"phoneNumberId": "PN2ppcGyMM", "conversationId": "CN1", "userId": "US1",
                    "participants": {"workspace": [ME], "external": [peer], "resolution": "available"}},
        "links": {"quo": "https://my.quo.com/inbox/CN1"}}}).encode()


def completed(cid, peer, direction, status, duration=0, voicemail=False, at="2026-10-12T21:18:00Z"):
    return call_event("call.completed", cid, peer, at, direction=direction, status=status,
                      duration=duration, hasVoicemail=voicemail)


def test_call_outcomes():
    con = {"id": 1, "external_id": ME}
    out = lambda *a, **k: quo.parse(json.loads(completed("AC9", ANA, *a, **k)), con)  # noqa: E731
    assert out("incoming", "answered", 252).data["call"] == {"direction": "incoming", "outcome": "connected", "length": 252}
    assert out("incoming", "answered", 252).content == "Incoming call, 4m 12s"
    assert out("incoming", "unanswered").content == "Missed call"
    assert out("incoming", "forwarded").data["call"]["outcome"] == "missed"
    assert out("incoming", "unanswered", 42, voicemail=True).content == "Voicemail, 42s"
    assert out("outgoing", "unanswered", 30).data["call"] == {"direction": "outgoing", "outcome": "no answer", "length": 0}
    assert out("outgoing", "unanswered").sender is None and out("incoming", "unanswered").sender.value == ANA


def test_every_call_puts_its_thread_in_todo_and_joins_the_texts():
    db, cid = setup()
    hook(db, cid, event("message.received", "AC1", "Call me", ANA, [ME]))
    hook(db, cid, event("message.delivered", "AC2", "Calling now", ME, [ANA], at="2026-10-12T21:19:00Z"), "msg_2")
    assert inbox.queue(db, "Todo") == []
    # Jeremy's own outgoing call still lands in Todo, in the same Thread as the Texts
    assert hook(db, cid, completed("AC3", ANA, "outgoing", "answered", 252, at="2026-10-12T21:20:00Z"), "msg_3") == 204
    assert [(r["title"], r["detail"]) for r in inbox.queue(db, "Todo")] == [("Call me", "NEW_REPLY")]
    assert db.execute("SELECT count(*) FROM threads").fetchone()[0] == 1
    # Repeats are stored once; a Quo retry is not a second call
    hook(db, cid, completed("AC3", ANA, "outgoing", "answered", 252, at="2026-10-12T21:20:00Z"), "msg_3")
    assert db.execute("SELECT count(*) FROM activities").fetchone()[0] == 3


def test_a_call_from_a_new_number_starts_a_thread_titled_by_outcome():
    db, cid = setup()
    hook(db, cid, completed("AC4", BEN, "incoming", "unanswered"))
    assert [(r["title"], r["detail"]) for r in inbox.queue(db, "Todo")] == [("Missed call", "CREATED")]
    assert [tuple(r) for r in db.execute("SELECT kind, value FROM identities")] == [("phone", BEN)]
    db2, cid2 = setup()
    hook(db2, cid2, completed("AC5", BEN, "outgoing", "unanswered"))  # Jeremy's call starts it: still Todo
    assert [(r["title"], r["detail"]) for r in inbox.queue(db2, "Todo")] == [("Outgoing call, no answer", "CREATED")]


def test_transcripts_append_under_the_call_without_moving_status():
    db, cid = setup()
    transcript = call_event("call.transcript.completed", None, ANA, "2026-10-12T21:30:00Z", callId="AC6",
                            duration=60, processingStatus="completed",
                            dialogue=[{"userId": "US1", "identifier": ME, "content": "Hi Ana."},
                                      {"userId": None, "identifier": ANA, "content": "The pour moved to Friday."}])
    assert hook(db, cid, transcript, "msg_t") == 503  # before its call: Quo delivers it again later
    hook(db, cid, completed("AC6", ANA, "incoming", "answered", 60))
    tid = db.execute("SELECT id FROM threads").fetchone()[0]
    inbox.act(db, tid, inbox.Done())
    assert hook(db, cid, transcript, "msg_t") == 204
    assert inbox.get_thread(db, tid).status == "DONE"
    m = inbox.thread_view(db, tid)["messages"][0]
    assert m["content"] == "Incoming call, 1m 0s\n\nYou: Hi Ana.\nThem: The pour moved to Friday."
    assert not m["edited"] and m["link"] == "https://my.quo.com/inbox/CN1"
    # A voicemail's text arrives the same way
    hook(db, cid, completed("AC7", BEN, "incoming", "unanswered", 12, voicemail=True))
    hook(db, cid, call_event("call.voicemail.completed", "VM1", BEN, callId="AC7", direction="incoming",
                             duration=12, transcript="Call me back about the bid."), "msg_v")
    vm = inbox.thread_view(db, inbox.queue(db, "Todo")[0]["id"])["messages"][0]
    assert vm["content"] == "Voicemail, 12s\n\nCall me back about the bid."


def test_conference_calls_and_unresolved_participants_are_dropped():
    con = {"id": 1, "external_id": ME}
    e = json.loads(completed("AC8", ANA, "incoming", "answered", 10))
    e["data"]["context"]["participants"]["external"] = [ANA, BEN]
    assert quo.parse(e, con) is None
    e["data"]["context"]["participants"] = {"workspace": [], "external": [], "resolution": "unavailable"}
    assert quo.parse(e, con) is None
