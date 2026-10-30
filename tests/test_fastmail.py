"""Fastmail adapter against a fake MCP server: polling, threading, the one-time import, and replies."""

import json
from datetime import datetime, timedelta, timezone

from comms_terminal import fastmail, inbox
from comms_terminal import serve as _serve

ME = "jeremy@fastmail.com"
NOW = datetime.now(timezone.utc)


def at(minutes_ago):
    return (NOW - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


class FakeFastmail:
    """Answers the MCP tool calls the adapter makes; records send_email calls."""

    def __init__(self, emails):
        self.emails = {e["id"]: e for e in emails}
        self.sent = []

    def tool(self, name, **args):
        if name == "list_identities":
            return {"items": [{"email": ME, "name": "Jeremy"}]}
        if name == "search_email":
            box = "Sent" if args["query"] == "in:sent" else "Inbox"
            hits = [e for e in self.emails.values() if e["box"] == box]
            return {"results": sorted(hits, key=lambda e: e["receivedAt"], reverse=True), "hasMore": False}
        if name == "read_email":
            return self.emails[args["id"]]
        if name == "read_thread":
            return [e for e in self.emails.values() if e["threadId"] == args["threadId"]]
        if name == "send_email":
            self.sent.append(args)
            eid = f"S{len(self.sent)}"
            self.emails[eid] = mail(eid, "T1", "Sent", ME, "Re: Pricing", args["body"], 0, f"sent{len(self.sent)}@fastmail.com")
            return {"emailId": eid, "submissionId": "X"}
        raise AssertionError(name)


def mail(eid, thread, box, sender, subject, body, minutes_ago, mid=None):
    return {"id": eid, "threadId": thread, "box": box, "messageId": [mid or f"{eid}@acme.com"],
            "from": [{"name": sender.split("@")[0].title(), "email": sender}], "to": [{"email": ME}], "cc": None,
            "subject": subject, "bodyText": body, "receivedAt": at(minutes_ago),
            "attachments": [{"name": "deck.pdf", "type": "application/pdf", "size": 2048}] if eid == "M2" else None}


def setup(connected_minutes_ago=60 * 24 * 40):
    db = inbox.connect()
    inbox.migrate(db)
    with db:
        db.execute("INSERT INTO connections (id, platform, external_id, self_id, label, connected_at)"
                   " VALUES ((SELECT printf('%d', count(*) + 1) FROM connections), 'fastmail', ?, ?, 'Fastmail', ?)", (ME, ME, (NOW - timedelta(minutes=connected_minutes_ago)).isoformat()))
    return db, db.execute("SELECT * FROM connections").fetchone()


def test_sync_threads_by_fastmail_thread_and_skips_known_and_bounces():
    db, conn = setup()
    fm = FakeFastmail([
        mail("M1", "T1", "Inbox", "ana@acme.com", "Pricing", "Can we talk pricing?", 90),
        mail("M2", "T1", "Inbox", "ana@acme.com", "Re: Pricing", "Deck attached.", 30),
        mail("B1", "T9", "Inbox", "MAILER-DAEMON@fastmail.com", "Undeliverable", "bounce", 20),
    ])
    tids = fastmail.sync(db, fm, conn, NOW - timedelta(days=1), {ME})
    assert len(set(tids)) == 1  # both messages in one Thread; the bounce is dropped
    v = inbox.thread_view(db, tids[0])
    assert [m["content"] for m in v["messages"]] == ["Can we talk pricing?", "Deck attached."]
    assert v["messages"][1]["data"]["attachments"] == [["deck.pdf", "application/pdf", 2048]]
    assert fastmail.sync(db, fm, conn, NOW - timedelta(days=1), {ME}) == []  # nothing new the second time


def test_poll_ignores_mail_from_before_the_connection():
    db, conn = setup(connected_minutes_ago=45)
    fm = FakeFastmail([mail("M1", "T1", "Inbox", "ana@acme.com", "Old", "old", 90),
                       mail("M2", "T2", "Inbox", "bo@acme.com", "New", "new", 30)])
    tids = fastmail.sync(db, fm, conn, datetime.fromisoformat(conn["connected_at"]), {ME})
    assert [inbox.get_thread(db, t).title for t in tids] == ["New"]


def test_poll_reads_sent_only_and_joins_the_forwarded_copy_thread():
    """Incoming mail arrives as a forwarded copy (#113); a reply written in Fastmail joins its Thread."""
    db, conn = setup()
    copy = inbox.Incoming(conn["id"], ME, "mailbox", "MESSAGE", "<M1@acme.com>", inbox.Identity("email", "ana@acme.com"),
                          NOW - timedelta(minutes=30), "Can we talk pricing?", [], "Pricing", {"from": "ana@acme.com"})
    tid = inbox.ingest(db, copy).id
    fm = FakeFastmail([mail("M1", "T1", "Inbox", "ana@acme.com", "Pricing", "Can we talk pricing?", 30),
                       mail("S1", "T1", "Sent", ME, "Re: Pricing", "Thursday works.", 5, "s1@fastmail.com"),
                       mail("M9", "T9", "Inbox", "bo@acme.com", "Other", "not polled", 4)])
    assert fastmail.sync(db, fm, conn, NOW - timedelta(days=1), {ME}, queries=("in:sent",)) == [tid]
    assert inbox.get_thread(db, tid).status_detail == "WAITING_FOR_CUSTOMER"
    assert db.execute("SELECT count(*) FROM threads").fetchone()[0] == 1


def test_reply_to_a_forwarded_copy_finds_its_fastmail_id():
    db, conn = setup()
    copy = inbox.Incoming(conn["id"], ME, "mailbox", "MESSAGE", "<M1@acme.com>", inbox.Identity("email", "ana@acme.com"),
                          NOW - timedelta(minutes=30), "Hi", [], "Pricing", {"from": "ana@acme.com"})
    tid = inbox.ingest(db, copy).id
    fm = FakeFastmail([mail("M1", "T1", "Inbox", "ana@acme.com", "Pricing", "Hi", 30)])
    inbox.reply(db, tid, "Hello", fastmail.FastmailSender(fm), ["ana@acme.com"], [])
    assert fm.sent[0]["emailId"] == "M1"


def test_import_leaves_every_thread_in_todo():
    db, conn = setup()
    fm = FakeFastmail([
        mail("M1", "T1", "Inbox", "ana@acme.com", "Pricing", "Can we talk pricing?", 600),
        mail("S0", "T1", "Sent", ME, "Re: Pricing", "Sure, Thursday.", 300, "s0@fastmail.com"),  # would be Waiting
        mail("M3", "T3", "Inbox", "bo@acme.com", "Hello", "Hi", 60 * 24 * 45),  # older than 30 days
    ])
    assert fastmail.import_recent(db, fm, conn, 30) == 1
    t = db.execute("SELECT status, status_detail FROM threads").fetchall()
    assert [tuple(r) for r in t] == [("TODO", "NEW_REPLY")]


def test_reply_goes_through_send_email_and_its_copy_is_not_stored_twice():
    db, conn = setup()
    fm = FakeFastmail([mail("M1", "T1", "Inbox", "ana@acme.com", "Pricing", "Can we talk pricing?", 30)])
    tid = fastmail.sync(db, fm, conn, NOW - timedelta(days=1), {ME})[0]
    t = inbox.reply(db, tid, "Thursday works.", fastmail.FastmailSender(fm), ["ana@acme.com"], [])
    assert fm.sent == [{"mode": "reply", "emailId": "M1", "body": "Thursday works.", "format": "text",
                        "to": [{"email": "ana@acme.com"}]}]
    assert t.status_detail == "WAITING_FOR_CUSTOMER"
    assert fastmail.sync(db, fm, conn, NOW - timedelta(days=1), {ME}) == []  # the Sent copy is the stored reply


def test_reply_socket_routes_a_fastmail_thread_to_its_sender():
    db, conn = setup()
    fm = FakeFastmail([mail("M1", "T1", "Inbox", "ana@acme.com", "Pricing", "Hi", 30)])
    tid = fastmail.sync(db, fm, conn, NOW - timedelta(days=1), {ME})[0]
    status, out = _serve.handle_reply(db, json.dumps({"thread_id": tid, "text": "Yes"}).encode(),
                                      _serve.Senders({"fastmail": fm}))  # the Fastmail adapter makes its sender
    assert status == 200 and fm.sent[0]["emailId"] == "M1"
    status, out = _serve.handle_reply(db, json.dumps({"thread_id": tid, "text": "Yes"}).encode(), _serve.Senders())
    assert status == 503  # no Fastmail client: never another Platform's sender


class FakeHttp:
    def __init__(self):
        self.calls = []

    def __call__(self, url, body, headers, timeout=30):
        self.calls.append((url, headers.get("Mcp-Session-Id")))
        if url == fastmail.TOKEN:
            return 200, {}, json.dumps({"access_token": "a", "expires_in": 3600, "refresh_token": "r2"}).encode()
        msg = json.loads(body)
        if msg["method"] == "initialize":
            return 200, {"mcp-session-id": "s1"}, b'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{}}\n\n'
        if "id" not in msg:
            return 202, {}, b""
        return 200, {}, json.dumps({"jsonrpc": "2.0", "id": msg["id"],
                                    "result": {"structuredContent": {"ok": True}}}).encode()


def test_client_refreshes_initializes_and_saves_a_rotated_refresh_token(tmp_path):
    state = tmp_path / "fastmail-oauth.json"
    http = FakeHttp()
    c = fastmail.Client({"client_id": "c", "refresh_token": "r1"}, state, http=http)
    assert c.tool("list_labels") == {"ok": True}
    assert [u for u, _ in http.calls][:2] == [fastmail.TOKEN, fastmail.MCP]
    assert http.calls[-1] == (fastmail.MCP, "s1")
    assert json.loads(state.read_text()) == {"refresh_token": "r2"} and oct(state.stat().st_mode)[-3:] == "600"
    assert fastmail.Client.load(b'{"client_id": "c", "refresh_token": "r1"}', state).cred["refresh_token"] == "r2"
