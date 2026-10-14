"""Sending a Draft as a new Thread; fake senders and fake HTTP only, never a real provider."""

import json
import urllib.error

import pytest

from universal_inbox import email, inbox, quo, serve
from test_composition import AT, PEER, PHONE, SELF, db, envelope, incoming  # noqa: F401  (db is a fixture)


class FakeSender:
    def __init__(self, result="<sent-1@example.test>"):
        self.result, self.calls = result, []

    def start(self, to, cc, subject, body, key):
        self.calls.append((to, cc, subject, body, key))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def email_draft(db, **changes):
    return inbox.save_draft(db, envelope(connection_id=1, sender=SELF, to="Ana <ana@acme.test>, bo@acme.test",
                                         cc="cy@acme.test", subject="Checklist", body="Hi Ana") | changes)


def test_sent_email_draft_becomes_waiting_thread_and_leaves_drafts(db):
    d = email_draft(db)
    sender = FakeSender()
    t = inbox.send_draft(db, d["id"], d["revision"], sender)
    assert sender.calls[0][:4] == (["ana@acme.test", "bo@acme.test"], ["cy@acme.test"], "Checklist", "Hi Ana")
    assert (t.title, t.status, t.status_detail) == ("Checklist", "SNOOZED", "WAITING_FOR_CUSTOMER")
    assert inbox.list_drafts(db) == []
    a = db.execute("SELECT * FROM activities WHERE thread_id = ?", (t.id,)).fetchone()
    assert a["native_id"] == "<sent-1@example.test>" and a["identity_id"] is None and a["content"] == "Hi Ana"
    assert json.loads(a["data"])["to"] == ["ana@acme.test", "bo@acme.test"]
    # the reply finds the Thread through the stored Message-ID
    reply = incoming("<r1@acme.test>", thread_ref=["<sent-1@example.test>"])
    assert inbox.ingest(db, reply).id == t.id


def test_refused_send_keeps_draft_editable_and_makes_nothing(db):
    d = email_draft(db)
    with pytest.raises(inbox.SendFailed):
        inbox.send_draft(db, d["id"], d["revision"], FakeSender(inbox.SendFailed("HTTP 422 bad address")))
    assert inbox.get_draft(db, d["id"])["sending_at"] is None
    assert db.execute("SELECT count(*) FROM threads").fetchone()[0] == 0


def test_unknown_outcome_locks_draft_until_sent_again_explicitly(db):
    d = email_draft(db)
    with pytest.raises(inbox.SendUnknown):
        inbox.send_draft(db, d["id"], d["revision"], FakeSender(TimeoutError("read timed out")))
    assert inbox.get_draft(db, d["id"])["sending_at"]
    again = FakeSender()
    with pytest.raises(inbox.SendUnknown):
        inbox.send_draft(db, d["id"], d["revision"], again)
    assert again.calls == []
    t = inbox.send_draft(db, d["id"], d["revision"], again, again=True)
    assert len(again.calls) == 1 and inbox.list_drafts(db) == [] and t.title == "Checklist"


@pytest.mark.parametrize("changes,problem", [
    ({"connection_id": None}, "Choose a Connection"),
    ({"to": " "}, "Add a recipient"),
    ({"subject": ""}, "Add a subject"),
    ({"body": "\n"}, "Write a message"),
])
def test_incomplete_or_stale_draft_makes_zero_provider_calls(db, changes, problem):
    d = email_draft(db, **changes)
    sender = FakeSender()
    with pytest.raises(ValueError, match=problem):
        inbox.send_draft(db, d["id"], d["revision"], sender)
    with pytest.raises(inbox.DraftConflict):
        inbox.send_draft(db, d["id"], d["revision"] + 1, sender)
    assert sender.calls == [] and inbox.get_draft(db, d["id"])["sending_at"] is None


def test_sms_draft_starts_its_own_thread_even_inside_the_grouping_window(db):
    inbox.ingest(db, inbox.Incoming(3, PEER, "im", "MESSAGE", "quo-1", inbox.Identity("phone", PEER), AT,
                                    "earlier text", data={"peer": PEER}))
    two = inbox.save_draft(db, envelope("sms", connection_id=3, sender=PHONE, to=f"{PEER}, +15550001003", body="x"))
    with pytest.raises(ValueError, match="one number"):
        inbox.send_draft(db, two["id"], two["revision"], FakeSender())
    d = inbox.save_draft(db, envelope("sms", connection_id=3, sender=PHONE, to=PEER, body="New topic\nmore"))
    t = inbox.send_draft(db, d["id"], d["revision"], FakeSender("quo-2"))
    assert t.title == "New topic" and t.status_detail == "WAITING_FOR_CUSTOMER"
    assert db.execute("SELECT count(*) FROM threads").fetchone()[0] == 2
    # Quo's webhook echo of the sent text is a duplicate, not a second Activity
    echo = inbox.Incoming(3, PEER, "im", "MESSAGE", "quo-2", None, AT, "New topic\nmore", data={"peer": PEER})
    assert inbox.ingest(db, echo) is None


def test_echo_that_arrives_before_recording_keeps_one_activity(db):
    d = email_draft(db)
    first = inbox.ingest(db, incoming("<sent-1@example.test>", sender=None, content="Hi Ana", title="Checklist"))
    t = inbox.send_draft(db, d["id"], d["revision"], FakeSender())
    assert t.id == first.id and db.execute("SELECT count(*) FROM activities").fetchone()[0] == 1
    assert inbox.list_drafts(db) == []


def test_resend_start_returns_the_ses_message_id(monkeypatch):
    monkeypatch.setattr(email.time, "sleep", lambda s: None)
    posts, gets = [], iter([(200, {"message_id": None}), (200, {"message_id": "<x@email.amazonses.com>"})])
    sender = email.EmailSender("k", SELF, post=lambda *a: posts.append(a) or (200, {"id": "re-1"}),
                               get=lambda *a: next(gets))
    assert sender.start(["ana@acme.test"], [], "Hello", "Hi", "draft-1") == "<x@email.amazonses.com>"
    url, headers, payload = posts[0]
    assert headers["Idempotency-Key"] == "draft-1" and payload["subject"] == "Hello" and payload["text"] == "Hi"
    assert "In-Reply-To" not in json.dumps(payload)


@pytest.mark.parametrize("outcome,error", [
    ((422, {"message": "Invalid `to` field"}), inbox.SendFailed),
    ((503, {}), inbox.SendUnknown),
    (urllib.error.URLError("timed out"), inbox.SendUnknown),
])
def test_resend_start_tells_refusal_from_unknown(outcome, error):
    def post(*a):
        if isinstance(outcome, Exception):
            raise outcome
        return outcome
    with pytest.raises(error) as e:
        email.EmailSender("k", SELF, post=post).start(["a@b.c"], [], "s", "b", "k1")
    assert type(e.value) is error


def test_quo_start_sends_to_the_one_number():
    posts = []
    sender = quo.QuoSender("k", PHONE, lambda url, key, payload: posts.append(payload) or {"data": {"id": "q1"}})
    assert sender.start([PEER], [], None, "Hi", "k1") == "q1"
    assert posts == [{"content": "Hi", "from": PHONE, "to": [PEER]}]


def test_send_draft_route_maps_outcomes(db):
    d = email_draft(db)
    senders = serve.Senders(made={1: FakeSender(inbox.SendUnknown("lost"))})
    body = json.dumps({"draft_id": d["id"], "revision": d["revision"]}).encode()
    assert serve.handle_send_draft(db, body, senders)[0] == 504
    senders.made[1] = FakeSender()
    assert serve.handle_send_draft(db, body, senders)[0] == 504  # locked: no second send without `again`
    again = json.dumps({"draft_id": d["id"], "revision": d["revision"], "again": True}).encode()
    status, out = serve.handle_send_draft(db, again, senders)
    assert status == 200 and inbox.get_thread(db, out["thread_id"]).title == "Checklist"
    assert serve.handle_send_draft(db, again, senders)[0] == 404
