"""Sending a Draft as a new Thread; fake senders and fake HTTP only, never a real provider."""

import json
import urllib.error

import pytest

from datetime import timedelta

from universal_inbox import email, inbox, quo, serve, slack
from test_composition import AT, PEER, PHONE, SELF, db, envelope, incoming  # noqa: F401  (db is a fixture)


class FakeSender:
    def __init__(self, result="<sent-1@example.test>"):
        self.result, self.calls = result, []

    def start(self, to, cc, subject, body, key, files=()):
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


def test_sms_draft_joins_the_conversation_inside_the_grouping_window(db):
    first = inbox.ingest(db, inbox.Incoming(3, PEER, "im", "MESSAGE", "quo-1", inbox.Identity("phone", PEER), AT,
                                            "earlier text", data={"peer": PEER}))
    two = inbox.save_draft(db, envelope("sms", connection_id=3, sender=PHONE, to=f"{PEER}, +15550001003", body="x"))
    with pytest.raises(ValueError, match="one number"):
        inbox.send_draft(db, two["id"], two["revision"], FakeSender())
    d = inbox.save_draft(db, envelope("sms", connection_id=3, sender=PHONE, to=PEER, body="New topic\nmore"))
    t = inbox.send_draft(db, d["id"], d["revision"], FakeSender("quo-2"))
    assert t.id == first.id and t.status_detail == "WAITING_FOR_CUSTOMER"  # FR-33: same rule as a text from Quo
    assert db.execute("SELECT count(*) FROM threads").fetchone()[0] == 1
    bare = inbox.save_draft(db, envelope("sms", connection_id=3, sender=PHONE, to="1 (555) 000-1002", body="y"))
    sender = FakeSender("quo-3")
    inbox.send_draft(db, bare["id"], bare["revision"], sender)
    assert sender.calls[0][0] == [PEER]
    reply = inbox.Incoming(3, PEER, "im", "MESSAGE", "quo-4", inbox.Identity("phone", PEER), AT, "Thanks",
                           data={"peer": PEER})
    assert inbox.ingest(db, reply).id == first.id  # the reply joins the same Thread
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


class FakeSlackSender(FakeSender):
    def conversation(self, channel):
        return ("im" if channel.startswith("D") else "channel"), "Sam" if channel.startswith("D") else "eng"


def slack_draft(db, to, body="Deploy is out\nnotes"):
    return inbox.save_draft(db, envelope("slack", connection_id=4, sender="USELF", to=to, body=body))


def sam(native_id, at, **data):
    return inbox.Incoming(4, "D1", "im", "MESSAGE", native_id, inbox.Identity("slack_user", "USAM"), at, "hi",
                          data={"channel": "D1", "ts": native_id.split(":")[1], "kind": "im", **data})


def test_slack_draft_joins_a_dm_inside_24_hours_and_starts_one_after(db, monkeypatch):
    earlier = inbox.ingest(db, sam("D1:1.0", AT - timedelta(hours=10)))
    d = slack_draft(db, "Sam <D1>")
    t = inbox.send_draft(db, d["id"], d["revision"], FakeSlackSender("D1:2.0"))
    assert t.id == earlier.id and t.status_detail == "WAITING_FOR_CUSTOMER"
    later = AT + timedelta(hours=25)
    monkeypatch.setattr(inbox, "_now", lambda: later)
    d = slack_draft(db, "Sam <D1>", body="New topic")
    t = inbox.send_draft(db, d["id"], d["revision"], FakeSlackSender("D1:3.0"))
    assert t.id != earlier.id and t.title == "New topic" and t.status_detail == "WAITING_FOR_CUSTOMER"
    a = db.execute("SELECT data FROM activities WHERE native_id = 'D1:3.0'").fetchone()
    assert json.loads(a["data"]) == {"channel": "D1", "ts": "3.0", "kind": "im"}
    assert inbox.ingest(db, inbox.Incoming(4, "D1", "im", "MESSAGE", "D1:3.0", None, later, "New topic",
                                           data={"channel": "D1", "ts": "3.0"})) is None  # Slack's echo
    assert inbox.list_drafts(db) == []


def test_slack_draft_in_a_channel_follows_ordinary_channel_rules(db):
    d = slack_draft(db, "#eng <C1>")
    t = inbox.send_draft(db, d["id"], d["revision"], FakeSlackSender("C1:5.0"))
    assert t.title == "Deploy is out"
    reply = inbox.Incoming(4, "C1", "channel", "MESSAGE", "C1:6.0", inbox.Identity("slack_user", "USAM"), AT, "nice",
                           data={"channel": "C1", "ts": "6.0", "thread_ts": "5.0", "kind": "channel"})
    assert inbox.ingest(db, reply).id != t.id  # FR-4b: a reply-thread is its own Thread
    top = inbox.Incoming(4, "C1", "channel", "MESSAGE", "C1:7.0", inbox.Identity("slack_user", "USAM"),
                         AT + timedelta(hours=20), "also", data={"channel": "C1", "ts": "7.0", "kind": "channel"})
    assert inbox.ingest(db, top).id == t.id  # top-level within 24h joins


def test_slack_start_posts_top_level_and_tells_refusal_from_unknown():
    posts = []
    sender = slack.SlackSender("t", lambda url, token, payload: posts.append(payload) or {"ok": True, "ts": "9.1"})
    assert sender.start(["C1"], [], None, "Hi", "k1") == "C1:9.1"
    assert posts == [{"channel": "C1", "text": "Hi"}]
    with pytest.raises(inbox.SendFailed, match="not_in_channel"):
        slack.SlackSender("t", lambda *a: {"ok": False, "error": "not_in_channel"}).start(["C1"], [], None, "Hi", "k")

    def lost(*a):
        raise urllib.error.URLError("timed out")
    with pytest.raises(inbox.SendUnknown):
        slack.SlackSender("t", lost).start(["C1"], [], None, "Hi", "k")


class FakeClient(slack.SlackClient):
    def __init__(self, responses):
        super().__init__("t", get=lambda url, token, params: responses[url.rsplit("/", 1)[1]])


def test_slack_conversations_lists_channels_dms_and_group_dms_by_name(db):
    client = FakeClient({
        "users.conversations": {"ok": True, "channels": [
            {"id": "C1", "name": "eng"}, {"id": "G1", "name": "ops", "is_private": True},
            {"id": "D1", "is_im": True, "user": "USAM"}, {"id": "D2", "is_im": True, "user": "UX", "is_user_deleted": True},
            {"id": "G2", "name": "mpdm-ana--bo--jeremy-1", "is_mpim": True, "is_private": True}]},
        "users.list": {"ok": True, "members": [{"id": "USAM", "profile": {"display_name": "Sam"}}]}})
    sender = slack.SlackSender("t", client=client)
    assert sender.conversations() == [
        {"id": "G2", "kind": "mpim", "label": "ana, bo, jeremy"}, {"id": "C1", "kind": "channel", "label": "#eng"},
        {"id": "G1", "kind": "private", "label": "#ops"}, {"id": "D1", "kind": "im", "label": "Sam"}]
    assert sender.conversation("D1") == ("im", "Sam")
    senders = serve.Senders(made={4: sender})
    assert serve.handle_slack_conversations(db, b'{"connection_id": 4}', senders)[1]["conversations"][0]["id"] == "G2"
    assert serve.handle_slack_conversations(db, b'{"connection_id": 3}', senders)[0] == 503
