"""Sending a Draft as a new Thread; fake senders and fake HTTP only, never a real provider."""

import json
import sqlite3
import urllib.error

import pytest

from datetime import timedelta
from types import SimpleNamespace

from comms_terminal import clock, email, inbox, quo, serve, slack
from test_composition import AT, PEER, PHONE, SELF, db, envelope, incoming  # noqa: F401  (db is a fixture)

RESEND_ID = "0a1b2c3d-1234-4000-8000-123456789abc"
RESEND_MARKER = f"<{RESEND_ID}@resend>"


class FakeSender:
    def __init__(self, result="<sent-1@example.test>"):
        self.result, self.calls = result, []

    def start(self, to, cc, subject, body, key, files=()):
        self.calls.append((to, cc, subject, body, key))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def email_draft(db, **changes):
    return inbox.save_draft(db, envelope(connection_id="1", sender=SELF, to="Ana <ana@acme.test>, bo@acme.test",
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


def test_draft_refusal_is_the_connections_last_error_until_a_send_goes_through(db):
    d = email_draft(db)
    last_error = lambda: db.execute("SELECT last_error FROM connections WHERE id = 1").fetchone()[0]  # noqa: E731
    with pytest.raises(inbox.SendFailed):
        inbox.send_draft(db, d["id"], d["revision"], FakeSender(inbox.SendFailed("HTTP 422 bad address")))
    assert last_error() == "HTTP 422 bad address"
    inbox.send_draft(db, d["id"], d["revision"], FakeSender())
    assert last_error() is None


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
    first = inbox.ingest(db, inbox.Incoming("3", PEER, "im", "MESSAGE", "quo-1", inbox.Identity("phone", PEER), AT,
                                            "earlier text", data={"peer": PEER}))
    two = inbox.save_draft(db, envelope("sms", connection_id="3", sender=PHONE, to=f"{PEER}, +15550001003", body="x"))
    with pytest.raises(ValueError, match="one number"):
        inbox.send_draft(db, two["id"], two["revision"], FakeSender())
    d = inbox.save_draft(db, envelope("sms", connection_id="3", sender=PHONE, to=PEER, body="New topic\nmore"))
    t = inbox.send_draft(db, d["id"], d["revision"], FakeSender("quo-2"))
    assert t.id == first.id and t.status_detail == "WAITING_FOR_CUSTOMER"  # FR-33: same rule as a text from Quo
    assert db.execute("SELECT count(*) FROM threads").fetchone()[0] == 1
    bare = inbox.save_draft(db, envelope("sms", connection_id="3", sender=PHONE, to="1 (555) 000-1002", body="y"))
    sender = FakeSender("quo-3")
    inbox.send_draft(db, bare["id"], bare["revision"], sender)
    assert sender.calls[0][0] == [PEER]
    reply = inbox.Incoming("3", PEER, "im", "MESSAGE", "quo-4", inbox.Identity("phone", PEER), AT, "Thanks",
                           data={"peer": PEER})
    assert inbox.ingest(db, reply).id == first.id  # the reply joins the same Thread
    # Quo's webhook echo of the sent text is a duplicate, not a second Activity
    echo = inbox.Incoming("3", PEER, "im", "MESSAGE", "quo-2", None, AT, "New topic\nmore", data={"peer": PEER})
    assert inbox.ingest(db, echo) is None


def test_echo_that_arrives_before_recording_keeps_one_activity(db):
    d = email_draft(db)
    first = inbox.ingest(db, incoming("<sent-1@example.test>", sender=None, content="Hi Ana", title="Checklist"))
    t = inbox.send_draft(db, d["id"], d["revision"], FakeSender())
    assert t.id == first.id and db.execute("SELECT count(*) FROM activities").fetchone()[0] == 1
    assert inbox.list_drafts(db) == []


def test_resend_start_retains_receipt_without_retrieval(monkeypatch):
    monkeypatch.setattr(email, "_get", lambda *a: pytest.fail("sending-only credentials must not retrieve email"))
    posts = []
    sender = email.EmailSender("k", SELF, post=lambda *a: posts.append(a) or (200, {"id": RESEND_ID.upper()}))
    assert sender.start(["ana@acme.test"], [], "Hello", "Hi", "draft-1") == RESEND_MARKER
    assert len(posts) == 1
    url, headers, payload = posts[0]
    assert url == "https://api.resend.com/emails"
    assert headers["Idempotency-Key"] == "draft-1" and payload["subject"] == "Hello" and payload["text"] == "Hi"
    assert "In-Reply-To" not in json.dumps(payload)


def test_resend_reply_retains_receipt_not_the_requested_message_id(monkeypatch):
    monkeypatch.setattr(email, "_get", lambda *a: pytest.fail("sending-only credentials must not retrieve email"))
    posts = []
    sender = email.EmailSender("k", SELF, post=lambda *a: posts.append(a) or (200, {"id": RESEND_ID}))
    history = [{"native_id": "<parent@acme.test>", "data": {"from": "ana@acme.test"}, "content": "Original"}]
    result = sender.send(SimpleNamespace(title="Hello"), "Hi", ["ana@acme.test"], [], history)
    assert result == RESEND_MARKER and len(posts) == 1
    _, headers, payload = posts[0]
    assert payload["headers"]["In-Reply-To"] == "<parent@acme.test>"
    assert payload["headers"]["References"] == "<parent@acme.test>"
    assert payload["headers"]["Message-ID"] == headers["Idempotency-Key"] != result
    assert payload["to"] == ["ana@acme.test"] and "Original" in payload["text"]


@pytest.mark.parametrize("parent", [RESEND_MARKER, "<legacy@resend>", None])
def test_resend_reply_pending_parent_refuses_before_post(parent):
    posts = []
    sender = email.EmailSender("k", SELF, post=lambda *a: posts.append(a))
    with pytest.raises(ValueError, match="confirmation pending") as error:
        sender.send(SimpleNamespace(title="Hello"), "Hi", ["ana@acme.test"], [],
                    [{"native_id": parent, "data": {}}])
    assert str(error.value) == inbox.EMAIL_CONFIRMATION_PENDING
    assert posts == []


def test_resend_reply_omits_earlier_unresolved_references():
    posts = []
    sender = email.EmailSender("k", SELF, post=lambda *a: posts.append(a) or (200, {"id": RESEND_ID}))
    history = [{"native_id": mid, "data": {}} for mid in (None, RESEND_MARKER, "<parent@acme.test>")]
    sender.send(SimpleNamespace(title="Hello"), "Hi", ["ana@acme.test"], [], history)
    assert posts[0][2]["headers"]["References"] == "<parent@acme.test>"
    assert posts[0][2]["headers"]["In-Reply-To"] == "<parent@acme.test>"


@pytest.mark.parametrize("parent", [RESEND_MARKER, "<genuine-incoming@resend>"])
def test_resend_reply_trusts_explicit_genuine_incoming_wire_id(parent):
    posts = []
    sender = email.EmailSender("k", SELF, post=lambda *a: posts.append(a) or (200, {"id": RESEND_ID}))
    history = [{"native_id": parent, "wire_id": parent, "data": {"from": "ana@acme.test"}}]
    assert sender.send(SimpleNamespace(title="Hello"), "Hi", ["ana@acme.test"], [], history) == RESEND_MARKER
    assert len(posts) == 1
    assert posts[0][2]["headers"]["In-Reply-To"] == parent
    assert posts[0][2]["headers"]["References"] == parent


def test_resend_reply_explicit_pending_projection_refuses_without_native_id_fallback():
    posts = []
    sender = email.EmailSender("k", SELF, post=lambda *a: posts.append(a))
    history = [{"native_id": "<looks-resolved@acme.test>", "wire_id": None, "data": {}}]
    with pytest.raises(ValueError, match="confirmation pending") as error:
        sender.send(SimpleNamespace(title="Hello"), "Hi", ["ana@acme.test"], [], history)
    assert str(error.value) == inbox.EMAIL_CONFIRMATION_PENDING
    assert posts == []


def test_resend_reply_projects_references_and_keeps_genuine_resend_domain_id():
    posts = []
    sender = email.EmailSender("k", SELF, post=lambda *a: posts.append(a) or (200, {"id": RESEND_ID}))
    history = [
        {"native_id": "<genuine-incoming@resend>", "wire_id": "<genuine-incoming@resend>", "data": {}},
        {"native_id": RESEND_MARKER, "wire_id": None, "data": {}},
        {"native_id": "<legacy@resend>", "data": {}},
        {"native_id": RESEND_MARKER, "wire_id": "<parent@acme.test>", "data": {}},
    ]
    sender.send(SimpleNamespace(title="Hello"), "Hi", ["ana@acme.test"], [], history)
    assert len(posts) == 1
    assert posts[0][2]["headers"]["In-Reply-To"] == "<parent@acme.test>"
    assert posts[0][2]["headers"]["References"] == "<genuine-incoming@resend> <parent@acme.test>"


@pytest.mark.parametrize("method", ["start", "send"])
@pytest.mark.parametrize("outcome,error", [
    ((422, {"message": "Invalid `to` field"}), inbox.SendFailed),
    ((503, {}), inbox.SendUnknown),
    (urllib.error.URLError("timed out"), inbox.SendUnknown),
    ((200, {}), inbox.SendUnknown),
    ((200, {"id": None}), inbox.SendUnknown),
    ((200, {"id": 123}), inbox.SendUnknown),
    ((200, {"id": "re-1"}), inbox.SendUnknown),
    ((200, []), inbox.SendUnknown),
])
def test_resend_tells_refusal_from_unknown(outcome, error, method):
    posts = []
    def post(*a):
        posts.append(a)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome
    sender = email.EmailSender("k", SELF, post=post)
    with pytest.raises(error) as e:
        if method == "start":
            sender.start(["a@b.c"], [], "s", "b", "k1")
        else:
            sender.send(SimpleNamespace(title="s"), "b", ["a@b.c"], [], [])
    assert type(e.value) is error
    assert len(posts) == 1


def test_resend_malformed_acceptance_keeps_draft_locked_without_retry(db):
    d, posts = email_draft(db), []
    sender = email.EmailSender("k", SELF, post=lambda *a: posts.append(a) or (200, {"id": "invalid"}))
    with pytest.raises(inbox.SendUnknown):
        inbox.send_draft(db, d["id"], d["revision"], sender)
    with pytest.raises(inbox.SendUnknown):
        inbox.send_draft(db, d["id"], d["revision"], sender)
    saved = inbox.get_draft(db, d["id"])
    assert saved["sending_at"] and saved["body"] == "Hi Ana" and len(posts) == 1
    assert db.execute("SELECT count(*) FROM activities").fetchone()[0] == 0


def test_authenticated_sent_payload_is_minimal_durable_and_idempotent(db):
    body = json.dumps({"type": "email.sent", "data": {"email_id": RESEND_ID.upper(),
                      "message_id": "<Actual-ID@email.amazonses.com>", "to": ["private@example.test"],
                      "subject": "discard", "text": "discard"}}).encode()
    assert email.handle_resend_hook(db, "1", body) == (204, True)
    assert not db.in_transaction
    assert email.handle_resend_hook(db, "1", body) == (204, False)
    assert [tuple(r) for r in db.execute("SELECT * FROM resend_receipts")] == [
        (RESEND_ID, "1", "<Actual-ID@email.amazonses.com>")]
    assert all(db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0 for table in ("activities", "threads"))
    conflicting = json.dumps({"type": "email.sent", "data": {"email_id": RESEND_ID,
                              "message_id": "<different@email.amazonses.com>"}}).encode()
    assert email.handle_resend_hook(db, "1", conflicting) == (409, False)
    assert db.execute("SELECT message_id FROM resend_receipts").fetchone()[0] == "<Actual-ID@email.amazonses.com>"


@pytest.mark.parametrize("body", [
    b"not JSON", b"\xff", b"[]", b"null", b"{}", b'{"type":null}', b'{"type":1}', b'{"type":""}',
    b'{"type":"email.sent"}', b'{"type":"email.sent","data":[]}',
    b"[" * 2000 + b"0" + b"]" * 2000,
    *[json.dumps({"type": "email.sent", "data": data}).encode() for data in (
        {}, {"email_id": RESEND_ID}, {"email_id": "invalid", "message_id": "<x@test>"},
        {"email_id": RESEND_ID, "message_id": None}, {"email_id": RESEND_ID, "message_id": 1},
        {"email_id": RESEND_ID, "message_id": "<one@test> <two@test>"},
        {"email_id": RESEND_ID, "message_id": "<one@test>\r\nHeader: bad"},
    )],
])
def test_authenticated_sent_payload_rejects_malformed_without_writes(db, body):
    assert email.handle_resend_hook(db, "1", body) == (400, False)
    assert db.execute("SELECT count(*) FROM resend_receipts").fetchone()[0] == 0


def test_authenticated_unrelated_event_is_ignored_without_parsing_its_data(db):
    assert email.handle_resend_hook(db, "1", b'{"type":"email.delivered","data":"not a sent receipt"}') == (204, False)
    assert db.execute("SELECT count(*) FROM resend_receipts").fetchone()[0] == 0


def test_authenticated_sent_payload_does_not_acknowledge_storage_failure(db, monkeypatch):
    def broken(*args):
        raise sqlite3.OperationalError("storage unavailable")
    monkeypatch.setattr(inbox, "record_resend_receipt", broken)
    body = json.dumps({"type": "email.sent", "data": {"email_id": RESEND_ID, "message_id": "<x@test>"}}).encode()
    with pytest.raises(sqlite3.OperationalError):
        email.handle_resend_hook(db, "1", body)


@pytest.mark.parametrize("outcome,error", [
    ((422, {"message": "Invalid `to` field"}), inbox.SendFailed),
    ((503, {}), inbox.SendUnknown),
    (urllib.error.URLError("Connection refused"), inbox.SendUnknown),  # urllib also wraps mid-request failures
    (TimeoutError("read timed out"), inbox.SendUnknown),  # Resend may have it
])
def test_resend_reply_tells_refusal_from_unknown(outcome, error):
    def post(*a):
        if isinstance(outcome, Exception):
            raise outcome
        return outcome
    with pytest.raises(error) as e:
        email.EmailSender("k", SELF, post=post).send(SimpleNamespace(title="Checklist"), "b", ["a@b.c"], [], [])
    assert type(e.value) is error


def test_quo_start_sends_to_the_one_number():
    posts = []
    sender = quo.QuoSender("k", PHONE, lambda url, key, payload: posts.append(payload) or {"data": {"id": "q1"}})
    assert sender.start([PEER], [], None, "Hi", "k1") == "q1"
    assert posts == [{"content": "Hi", "from": PHONE, "to": [PEER]}]


def test_send_draft_route_maps_outcomes(db):
    d = email_draft(db)
    senders = serve.Senders(made={"1": FakeSender(inbox.SendUnknown("lost"))})
    body = json.dumps({"draft_id": d["id"], "revision": d["revision"]}).encode()
    assert serve.handle_send_draft(db, body, senders)[0] == 504
    senders.made["1"] = FakeSender()
    assert serve.handle_send_draft(db, body, senders)[0] == 504  # locked: no second send without `again`
    again = json.dumps({"draft_id": d["id"], "revision": d["revision"], "again": True}).encode()
    status, out = serve.handle_send_draft(db, again, senders)
    assert status == 200 and inbox.get_thread(db, out["thread_id"]).title == "Checklist"
    assert serve.handle_send_draft(db, again, senders)[0] == 404


class FakeSlackSender(FakeSender):
    def conversation(self, channel):
        return ("im" if channel.startswith("D") else "channel"), "Sam" if channel.startswith("D") else "eng"


def slack_draft(db, to, body="Deploy is out\nnotes"):
    return inbox.save_draft(db, envelope("slack", connection_id="4", sender="USELF", to=to, body=body))


def sam(native_id, at, **data):
    return inbox.Incoming("4", "D1", "im", "MESSAGE", native_id, inbox.Identity("slack_user", "USAM"), at, "hi",
                          data={"channel": "D1", "ts": native_id.split(":")[1], "kind": "im", **data})


def test_slack_draft_joins_a_dm_inside_24_hours_and_starts_one_after(db, monkeypatch):
    earlier = inbox.ingest(db, sam("D1:1.0", AT - timedelta(hours=10)))
    d = slack_draft(db, "Sam <D1>")
    t = inbox.send_draft(db, d["id"], d["revision"], FakeSlackSender("D1:2.0"))
    assert t.id == earlier.id and t.status_detail == "WAITING_FOR_CUSTOMER"
    later = AT + timedelta(hours=25)
    monkeypatch.setattr(clock, "now", lambda: later)
    d = slack_draft(db, "Sam <D1>", body="New topic")
    t = inbox.send_draft(db, d["id"], d["revision"], FakeSlackSender("D1:3.0"))
    assert t.id != earlier.id and t.title == "New topic" and t.status_detail == "WAITING_FOR_CUSTOMER"
    a = db.execute("SELECT data FROM activities WHERE native_id = 'D1:3.0'").fetchone()
    assert json.loads(a["data"]) == {"channel": "D1", "ts": "3.0", "kind": "im"}
    assert inbox.ingest(db, inbox.Incoming("4", "D1", "im", "MESSAGE", "D1:3.0", None, later, "New topic",
                                           data={"channel": "D1", "ts": "3.0"})) is None  # Slack's echo
    assert inbox.list_drafts(db) == []


def test_slack_draft_in_a_channel_follows_ordinary_channel_rules(db):
    d = slack_draft(db, "#eng <C1>")
    t = inbox.send_draft(db, d["id"], d["revision"], FakeSlackSender("C1:5.0"))
    assert t.title == "Deploy is out"
    reply = inbox.Incoming("4", "C1", "channel", "MESSAGE", "C1:6.0", inbox.Identity("slack_user", "USAM"), AT, "nice",
                           data={"channel": "C1", "ts": "6.0", "thread_ts": "5.0", "kind": "channel"})
    assert inbox.ingest(db, reply).id != t.id  # FR-4b: a reply-thread is its own Thread
    top = inbox.Incoming("4", "C1", "channel", "MESSAGE", "C1:7.0", inbox.Identity("slack_user", "USAM"),
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
    senders = serve.Senders(made={"4": sender})
    assert serve.handle_slack_conversations(db, b'{"connection_id": "4"}', senders)[1]["conversations"][0]["id"] == "G2"
    assert serve.handle_slack_conversations(db, b'{"connection_id": "3"}', senders)[0] == 503
