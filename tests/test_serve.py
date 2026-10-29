"""The service's local reply socket (POST /reply) end to end, with fake senders."""

import base64
import hashlib
import hmac
import io
import json
import sqlite3
import threading
from datetime import datetime, timezone
from http.client import HTTPMessage
from types import SimpleNamespace

import pytest

from comms_terminal import inbox, serve
import pytest

from comms_terminal.remote import ReplyError, post as _post, socket_reply


class Sender:
    def __init__(self, fail=False):
        self.fail = fail

    def send(self, thread, text, to, cc, history, files=()):
        if self.fail:
            raise inbox.SendFailed("slack send failed: invalid_auth")
        return "D0ANA:1728700999.000100"


def setup(tmp_path):
    path = str(tmp_path / "inbox.db")
    db = inbox.connect(path)
    inbox.migrate(db)
    with db:
        db.execute("INSERT INTO connections (id, platform, external_id, self_id, label, connected_at)"
                   " VALUES (2, 'slack', 'T0', 'U0JEREMY', 'Slack', '2026-10-12T00:00:00+00:00')")
    t = inbox.ingest(db, inbox.Incoming(2, "D0ANA", "im", "MESSAGE", "D0ANA:1", inbox.Identity("slack_user", "U0ANA"),
                                        datetime.now(timezone.utc), "hi", data={"channel": "D0ANA"}))
    return path, t.id


def test_handle_reply_statuses(tmp_path):
    path, tid = setup(tmp_path)
    db = inbox.connect(path)
    assert serve.handle_reply(db, b"{}", serve.Senders())[0] == 400
    assert serve.handle_reply(db, json.dumps({"thread_id": 999, "text": "x"}).encode(), serve.Senders())[0] == 404
    status, body = serve.handle_reply(db, json.dumps({"thread_id": tid, "text": "x"}).encode(),
                                      serve.Senders(made={2: Sender(True)}))
    assert status == 502 and "invalid_auth" in body["error"]
    assert db.execute("SELECT last_error FROM connections WHERE id = 2").fetchone()[0] == "slack send failed: invalid_auth"


def test_reply_timeout_is_unknown_records_nothing_and_a_sent_reply_clears_last_error(tmp_path):
    path, tid = setup(tmp_path)
    db = inbox.connect(path)

    class Lost:
        def send(self, thread, text, to, cc, history, files=()):
            raise TimeoutError("read timed out")

    body = json.dumps({"thread_id": tid, "text": "x"}).encode()
    status, out = serve.handle_reply(db, body, serve.Senders(made={2: Lost()}))
    assert status == 504 and "outcome unknown" in out["error"]
    assert db.execute("SELECT count(*) FROM activities").fetchone()[0] == 1  # only the inbound message
    assert db.execute("SELECT last_error FROM connections WHERE id = 2").fetchone()[0].startswith("Send outcome unknown")
    assert serve.handle_reply(db, body, serve.Senders(made={2: Sender()}))[0] == 200
    assert db.execute("SELECT last_error FROM connections WHERE id = 2").fetchone()[0] is None


def test_lost_response_is_504_and_an_unreachable_service_is_503(tmp_path):
    import socket
    sock = str(tmp_path / "mute.sock")
    mute = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    mute.bind(sock)
    mute.listen()  # accepts the request, never answers
    try:
        with pytest.raises(ReplyError) as lost:
            _post(sock, "/reply", {}, timeout=0.2)
        with pytest.raises(ReplyError) as down:
            _post(sock + ".missing", "/reply", {})
    finally:
        mute.close()
    assert (lost.value.status, down.value.status) == (504, 503)


def test_reply_over_unix_socket(tmp_path):
    path, tid = setup(tmp_path)
    sock = str(tmp_path / "s.sock")
    server = serve.reply_server(path, sock, serve.Senders(made={2: Sender()}))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        import os
        import stat
        assert stat.S_IMODE(os.stat(sock).st_mode) == 0o600
        out = socket_reply(sock)(tid, "on it", [], [])
        assert out == {"status": "SNOOZED", "status_detail": "WAITING_FOR_CUSTOMER"}
    finally:
        server.shutdown()
        server.server_close()
    try:
        socket_reply(sock + ".missing")(tid, "x", [], [])
    except ReplyError as e:
        assert "not reachable" in str(e)
    else:
        raise AssertionError("expected ReplyError")


def test_service_runs_with_email_only(tmp_path, monkeypatch):
    """Slack not connected yet: /hooks/slack answers 503 and email still works (runbook §6)."""
    import urllib.error
    import urllib.request
    import threading as _threading
    from comms_terminal import serve as _serve
    creds = tmp_path / "creds"
    creds.mkdir()
    (creds / "email-hook-secret").write_text("e" * 64)
    (creds / "resend-api-key").write_text("re_x")
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(creds))
    db_path = str(tmp_path / "inbox.db")
    db = inbox.connect(db_path)
    inbox.migrate(db)
    # A Connection added while the service runs gets its sender on first use, with no restart
    with db:
        db.execute("INSERT INTO connections (id, platform, external_id, self_id, label, connected_at)"
                   " VALUES (1, 'gmail', 'me@x.io', 'me@x.io', 'Gmail', '2026-10-12T00:00:00+00:00')")
        db.execute("INSERT INTO connections (id, platform, external_id, self_id, label, connected_at)"
                   " VALUES (2, 'slack', 'T0', 'U0', 'Slack', '2026-10-12T00:00:00+00:00')")
    senders, rows = _serve.Senders(), {r["id"]: r for r in db.execute("SELECT * FROM connections")}
    assert type(senders.get(rows[1])).__name__ == "EmailSender" and senders.get(rows[1]).from_address == "me@x.io"
    assert senders.get(rows[2]) is None  # no Slack token
    server = _serve.hooks_server(db_path, "127.0.0.1", 0)
    _threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{server.server_address[1]}/hooks/slack", b"{}", method="POST")
        try:
            urllib.request.urlopen(req, timeout=5)
            status = 200
        except urllib.error.HTTPError as e:
            status = e.code
        assert status == 503
    finally:
        server.shutdown()


def test_slack_hook_without_a_connection_answers_503(tmp_path, monkeypatch):
    """Slack's secret is installed but no Slack Connection exists: the intake answers 503 before verifying."""
    import urllib.error
    import urllib.request
    creds = tmp_path / "creds"
    creds.mkdir()
    (creds / "slack-signing-secret").write_text("s")
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(creds))
    db_path = str(tmp_path / "inbox.db")
    inbox.migrate(inbox.connect(db_path))
    server = serve.hooks_server(db_path, "127.0.0.1", 0, client=object())
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{server.server_address[1]}/hooks/slack", b"{}", method="POST")
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req, timeout=5)
        assert (e.value.code, e.value.read()) == (503, b"no Slack Connection")
    finally:
        server.shutdown()


def test_email_post_sends_a_user_agent_and_returns_provider_errors():
    """Resend sits behind Cloudflare, which 403s Python's default User-Agent; errors keep the provider's message."""
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from comms_terminal import email as _email
    seen = {}

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            seen["ua"] = self.headers.get("User-Agent")
            self.rfile.read(int(self.headers["Content-Length"]))
            body = b'{"message":"The agentmaple.com domain is not verified"}'
            self.send_response(403)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.handle_request, daemon=True).start()
    status, body = _email._post(f"http://127.0.0.1:{srv.server_address[1]}/emails", {}, {"x": 1})
    srv.server_close()
    assert status == 403 and body["message"] == "The agentmaple.com domain is not verified"
    assert seen["ua"].startswith("comms-terminal/")


def test_email_hook_logs_its_outcome_without_content(caplog):
    """One journal line per inbound email: outcome and Message-ID, no addresses or body (NFR-5)."""
    import hashlib as _h
    import hmac as _hm
    import logging
    from comms_terminal import email as _email
    db = inbox.connect()
    inbox.migrate(db)
    with db:
        cid = db.execute("INSERT INTO connections (platform, external_id, self_id, label, connected_at) VALUES"
                         " ('gmail', 'me@x.io', 'me@x.io', 'Gmail', '2026-10-12T00:00:00+00:00')").lastrowid
    now = datetime(2026, 10, 12, 10, tzinfo=timezone.utc)

    def post(raw):
        ts = str(int(now.timestamp()))
        sig = "sha256=" + _hm.new(b"k", ts.encode() + b"." + raw, _h.sha256).hexdigest()
        connection = db.execute("SELECT * FROM connections WHERE id = ?", (cid,)).fetchone()
        return _email.handle_hook(db, connection, {"X-Inbox-Timestamp": ts, "X-Inbox-Signature": sig}, raw, b"k", now)

    bounce = (b"From: Mail Delivery Subsystem <mailer-daemon@googlemail.com>\r\nTo: me@x.io\r\n"
              b"Subject: Delivery Status Notification (Failure)\r\nMessage-ID: <dsn1@google.com>\r\n"
              b"Auto-Submitted: auto-replied\r\n\r\nsecret body")
    with caplog.at_level(logging.INFO, logger="comms_terminal.email"):
        assert post(bounce) == 204
        assert post(bounce.replace(b"Auto-Submitted: auto-replied\r\n", b"")) == 204
    lines = [r.getMessage() for r in caplog.records]
    assert lines[0] == "email filtered as automated (auto-reply, bounce, or bulk) <dsn1@google.com>"
    assert lines[1] == "email stored <dsn1@google.com>"
    assert not any("secret body" in x or "mailer-daemon" in x for x in lines)


def test_email_signature_failure_reason_shows_on_the_connection():
    """A rejected inbound email names why on its Connection (unsigned, stale timestamp, bad signature),
    tells the caller nothing, and a signed request clears it."""
    db = inbox.connect()
    inbox.migrate(db)
    with db:
        cid = db.execute("INSERT INTO connections (platform, external_id, self_id, label, connected_at) VALUES"
                         " ('gmail', 'me@x.io', 'me@x.io', 'Gmail', '2026-10-12T00:00:00+00:00')").lastrowid
    now = datetime(2026, 10, 12, 10, tzinfo=timezone.utc)
    raw = b"From: a@b.io\r\nTo: me@x.io\r\nSubject: hi\r\nMessage-ID: <m1@b.io>\r\n\r\nhello"
    ts = str(int(now.timestamp()))

    def sign(t, key=b"k"):
        return {"X-Inbox-Timestamp": t, "X-Inbox-Signature": "sha256=" + hmac.new(key, t.encode() + b"." + raw,
                                                                                  hashlib.sha256).hexdigest()}

    def error():
        return db.execute("SELECT last_error FROM connections WHERE id = ?", (cid,)).fetchone()[0]

    for headers, why in [({}, "unsigned"), (sign(str(int(ts) - 3600)), "stale timestamp"),
                         (sign(ts, b"wrong"), "bad signature")]:
        assert serve.intake(db, "gmail", headers, raw, b"k", now) == (401, "")
        assert error() == f"inbound request failed signature check ({why})"
    assert serve.intake(db, "gmail", sign(ts), raw, b"k", now)[0] == 204
    assert error() is None


RESEND_KEY = b"whsec_" + base64.b64encode(b"receipt-test-key")
RESEND_ID = "7b8de695-b4f1-4e78-87d4-b3dbb40382e1"
RESEND_NOW = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)


def receipt_body(email_id=RESEND_ID, message_id="<wire@delivery.example>"):
    return json.dumps({"type": "email.sent", "data": {"email_id": email_id, "message_id": message_id,
                       "from": "private@example.com", "subject": "private subject"}}).encode()


def receipt_headers(body, ts=None, wid="msg_receipt"):
    ts = str(int(datetime.now(timezone.utc).timestamp()) if ts is None else ts)
    sig = base64.b64encode(hmac.new(base64.b64decode(RESEND_KEY[6:]),
                                  f"{wid}.{ts}.".encode() + body, hashlib.sha256).digest()).decode()
    headers = HTTPMessage()
    for name, value in (("svix-id", wid), ("svix-timestamp", ts), ("svix-signature", "v1," + sig)):
        headers[name] = value
    return headers


def receipt_db(tmp_path, gmail_count=1):
    path = str(tmp_path / "receipts.db")
    db = inbox.connect(path)
    inbox.migrate(db)
    with db:
        for i, platform in enumerate(["gmail"] * gmail_count + ["fastmail", "slack", "quo"], 1):
            db.execute("INSERT INTO connections (id, platform, external_id, self_id, label, connected_at)"
                       " VALUES (?, ?, ?, ?, ?, ?)",
                       (i, platform, f"me{i}@example.com", f"me{i}@example.com", platform, RESEND_NOW.isoformat()))
    db.close()
    return path


def hook_routes(monkeypatch, path, creds=None, kick=None):
    # Capture source callbacks, without a listener, real provider, credential file, or network.
    monkeypatch.setattr(serve, "credential", lambda name: (creds or {}).get(name))
    monkeypatch.setattr(serve, "ThreadingHTTPServer", lambda address, handler: handler.routes)
    return serve.hooks_server(path, "127.0.0.1", 0, client=object(), kick=kick)


def test_resend_standard_webhooks_vector_and_rotation():
    headers = HTTPMessage()
    headers["svix-id"] = "msg_p5jXN8AQM9LWM0D4loKWxJek"
    headers["svix-timestamp"] = "1614265330"
    headers["svix-signature"] = "v1,g0hM9SsE+OTPJTGt/tmIKtSyZlE3uFJELVlNIOLJ1OE="
    key = b"whsec_MfKQ9r8GKYqrTwjUPD8ILPZIo2LaLaSw"
    body, now = b'{"test": 2432232314}', datetime.fromtimestamp(1614265330, timezone.utc)
    assert serve._verify_resend(headers, body, key, now)  # published Standard Webhooks vector
    headers.replace_header("svix-signature", "v2," + base64.b64encode(b"new version").decode()
                           + " v1," + base64.b64encode(b"x" * 32).decode() + " " + headers["svix-signature"])
    assert serve._verify_resend(headers, body, key, now)


@pytest.mark.parametrize("delta,accepted", [(-301, False), (-300, True), (300, True), (301, False)])
def test_resend_signature_replay_window(delta, accepted):
    body = receipt_body()
    headers = receipt_headers(body, int(RESEND_NOW.timestamp()) + delta)
    assert serve._verify_resend(headers, body, RESEND_KEY, RESEND_NOW) is accepted


@pytest.mark.parametrize("change", ["space", "unicode", "missing-id", "missing-timestamp", "missing-signature",
    "duplicate-id", "duplicate-timestamp", "duplicate-signature", "timestamp", "huge-timestamp",
    "unicode-timestamp", "unicode-id", "signature", "unicode-signature", "forged", "unsupported", "forwarding"])
def test_resend_rejects_bad_auth_before_db_or_payload(tmp_path, monkeypatch, caplog, change):
    routes = hook_routes(monkeypatch, "must-not-open.db", {"resend-webhook-secret": RESEND_KEY})
    monkeypatch.setattr(inbox, "connect", lambda *a: pytest.fail("unauthenticated database access"))
    body = receipt_body()
    headers = receipt_headers(body)
    if change == "space":
        body += b" "
    elif change == "unicode":
        body = body.replace(b"private subject", "privaté subject".encode())
    elif change.startswith("missing-"):
        del headers["svix-" + change.removeprefix("missing-")]
    elif change.startswith("duplicate-"):
        name = "svix-" + change.removeprefix("duplicate-")
        headers[name] = headers[name]
    elif change.endswith("timestamp"):
        headers.replace_header("svix-timestamp", {"timestamp": "1.5", "huge-timestamp": "9" * 5000,
                                                "unicode-timestamp": "１２３"}[change])
    elif change == "unicode-id":
        headers.replace_header("svix-id", "msg_é")
    elif change == "signature":
        headers.replace_header("svix-signature", "v1,not-base64")
    elif change == "unicode-signature":
        headers.replace_header("svix-signature", "v1,é")
    elif change in ("forged", "unsupported"):
        version = "v1" if change == "forged" else "v2"
        headers.replace_header("svix-signature", version + "," + base64.b64encode(b"x" * 32).decode())
    else:
        headers = HTTPMessage()
        headers["X-Inbox-Timestamp"] = str(int(datetime.now(timezone.utc).timestamp()))
        headers["X-Inbox-Signature"] = "sha256=" + hmac.new(b"forwarding", body, hashlib.sha256).hexdigest()
    assert routes["/hooks/resend"](headers, body) == (401, "text/plain", b"")
    assert not caplog.records


@pytest.mark.parametrize("secret", [None, b"", b"not-a-key", b"whsec_", b"whsec_%%%%",
                                   b"whsec_Zg==extra", "whsec_wrong-type", b"whsec_Zg=", b"whsec_Zh=="])
def test_resend_config_optional_without_changing_other_hooks(tmp_path, monkeypatch, secret):
    path = receipt_db(tmp_path)
    seen = []
    email_hook = lambda db, c, *a: seen.append(("email", c["id"])) or 204  # noqa: E731
    monkeypatch.setattr(serve.email, "handle_hook", email_hook)
    monkeypatch.setattr(serve.fastmail, "handle_hook", email_hook)  # the email route's Fastmail Platform
    monkeypatch.setattr(serve.slack, "handle_hook", lambda db, c, *a: (seen.append(("slack", c["id"])) or 200, ""))
    monkeypatch.setattr(serve.quo, "handle_hook", lambda db, c, *a: seen.append(("quo", c["id"])) or 204)
    routes = hook_routes(monkeypatch, path, {"email-hook-secret": b"forward", "slack-signing-secret": b"slack",
                                           "quo-webhook-secret": b"quo", "resend-webhook-secret": secret})
    assert set(routes) == {"/hooks/email", "/hooks/slack", "/hooks/quo", "/hooks/resend"}
    assert routes["/hooks/resend"](HTTPMessage(), b"{}")[0] == 503
    assert routes["/hooks/email"](HTTPMessage(), b"raw")[0] == 204
    headers = HTTPMessage()
    headers["X-Inbox-To"] = serve.fastmail.INBOUND
    assert routes["/hooks/email"](headers, b"raw")[0] == 204
    assert routes["/hooks/slack"](HTTPMessage(), b"{}")[0] == 200
    assert routes["/hooks/quo"](HTTPMessage(), b"{}")[0] == 204
    assert seen == [("email", 1), ("email", 2), ("slack", 3), ("quo", 4)]


def test_resend_unreadable_config_does_not_break_listener(tmp_path, monkeypatch):
    routes = hook_routes(monkeypatch, "unused")
    def unreadable(name):
        if name == "resend-webhook-secret":
            raise PermissionError("private filename must not escape")
        return None
    monkeypatch.setattr(serve, "credential", unreadable)
    routes = serve.hooks_server("unused", "127.0.0.1", 0, client=object())
    assert routes["/hooks/resend"](HTTPMessage(), b"{}") == (503, "text/plain", b"")
    assert routes["/hooks/email"](HTTPMessage(), b"{}")[0] == 503


@pytest.mark.parametrize("count", [0, 2])
def test_resend_without_a_gmail_or_the_one_it_sent_from_is_503(tmp_path, monkeypatch, count):
    path = receipt_db(tmp_path, gmail_count=count)
    routes = hook_routes(monkeypatch, path, {"resend-webhook-secret": RESEND_KEY})
    body = receipt_body()
    assert routes["/hooks/resend"](receipt_headers(body), body)[0] == 503
    db = inbox.connect(path)
    assert db.execute("SELECT count(*) FROM resend_receipts").fetchone()[0] == 0
    db.close()



def test_resend_receipt_binds_to_the_gmail_connection_it_was_sent_from(tmp_path, monkeypatch):
    """Several Gmail Connections share one Resend account: the receipt's From picks among them."""
    path = receipt_db(tmp_path, gmail_count=2)
    routes = hook_routes(monkeypatch, path, {"resend-webhook-secret": RESEND_KEY})
    body = json.dumps({"type": "email.sent", "data": {"email_id": RESEND_ID, "message_id": "<w@delivery.example>",
                                                      "from": "Jeremy <ME2@example.com>"}}).encode()
    assert routes["/hooks/resend"](receipt_headers(body), body)[0] == 204
    db = inbox.connect(path)
    assert [r[0] for r in db.execute("SELECT connection_id FROM resend_receipts")] == [2]
    db.close()


@pytest.mark.parametrize("field,value", [("self_id", " "), ("external_id", " "), ("id", 0)])
def test_resend_rejects_unusable_single_gmail_binding(tmp_path, monkeypatch, field, value):
    path = receipt_db(tmp_path)
    db = inbox.connect(path)
    with db:
        db.execute(f"UPDATE connections SET {field} = ? WHERE platform = 'gmail'", (value,))
    db.close()
    routes = hook_routes(monkeypatch, path, {"resend-webhook-secret": RESEND_KEY})
    body = receipt_body()
    assert routes["/hooks/resend"](receipt_headers(body), body)[0] == 503


def test_resend_receipts_commit_before_kick_and_ack_retries_no_health_or_mail(tmp_path, monkeypatch, caplog):
    path = receipt_db(tmp_path)
    db = inbox.connect(path)
    before = [tuple(r) for r in db.execute("SELECT * FROM connections ORDER BY id")]
    db.close()
    class DurableKick(threading.Event):
        def set(self):
            check = inbox.connect(path)
            try:
                assert check.execute("SELECT count(*) FROM resend_receipts").fetchone()[0] > 0
            finally:
                check.close()
            super().set()
    kick = DurableKick()
    route = hook_routes(monkeypatch, path, {"resend-webhook-secret": RESEND_KEY}, kick)["/hooks/resend"]
    body = receipt_body()
    assert route(receipt_headers(body), body) == (204, "text/plain", b"")
    assert kick.is_set()
    kick.clear()
    assert route(receipt_headers(body), body)[0] == 204 and not kick.is_set()
    conflict = receipt_body(message_id="<different@delivery.example>")
    assert route(receipt_headers(conflict), conflict)[0] == 409 and not kick.is_set()
    # A later UUID followed by an old duplicate remains durable and never replays work.
    later = receipt_body("7b8de695-b4f1-4e78-87d4-b3dbb40382e2", "<later@delivery.example>")
    assert route(receipt_headers(later), later)[0] == 204
    kick.clear()
    assert route(receipt_headers(body), body)[0] == 204 and not kick.is_set()
    db = inbox.connect(path)
    assert [tuple(r) for r in db.execute("SELECT * FROM connections ORDER BY id")] == before
    assert tuple(db.execute("SELECT connection_id, message_id FROM resend_receipts WHERE email_id = ?",
                            (RESEND_ID,)).fetchone()) == (1, "<wire@delivery.example>")
    assert db.execute("SELECT count(*) FROM activities").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM threads").fetchone()[0] == 0
    db.close()
    assert not caplog.records


@pytest.mark.parametrize("body,status", [(b"[]", 400), (b"{", 400), (b'{"type":null}', 400),
    (b'{"type":"email.sent","data":[]}', 400), (b'{"type":"email.sent","data":{}}', 400),
    (receipt_body("invalid"), 400), (receipt_body(message_id="<a@b> <c@d>"), 400),
    (b'{"type":"email.delivered"}', 204)])
def test_resend_authenticated_payload_status_without_kick(tmp_path, monkeypatch, body, status):
    path, kick = receipt_db(tmp_path), threading.Event()
    route = hook_routes(monkeypatch, path, {"resend-webhook-secret": RESEND_KEY}, kick)["/hooks/resend"]
    assert route(receipt_headers(body), body) == (status, "text/plain", b"")
    assert not kick.is_set()
    db = inbox.connect(path)
    assert db.execute("SELECT count(*) FROM resend_receipts").fetchone()[0] == 0
    db.close()


@pytest.mark.parametrize("failure", ["connect", "uncommitted-write"])
def test_resend_storage_failure_never_acknowledged_or_kicked(tmp_path, monkeypatch, failure):
    path, kick = receipt_db(tmp_path), threading.Event()
    route = hook_routes(monkeypatch, path, {"resend-webhook-secret": RESEND_KEY}, kick)["/hooks/resend"]
    connect = inbox.connect
    def fail_connect(*args):
        raise sqlite3.OperationalError("private database path")
    def fail_write(db, cid, body):
        db.execute("INSERT INTO resend_receipts VALUES (?, ?, ?)", (RESEND_ID, cid, "<wire@delivery.example>"))
        raise sqlite3.OperationalError("commit failed: private data")
    if failure == "connect":
        monkeypatch.setattr(inbox, "connect", fail_connect)
    else:
        monkeypatch.setattr(serve.email, "handle_resend_hook", fail_write)
    body = receipt_body()
    assert route(receipt_headers(body), body) == (503, "text/plain", b"")
    assert not kick.is_set()
    db = connect(path)
    assert db.execute("SELECT count(*) FROM resend_receipts").fetchone()[0] == 0
    db.close()


def frame_request(headers, body=b"", path="/hooks/resend", reader=None, known=True):
    calls, statuses, timeouts = [], [], []
    handler = object.__new__(serve._Handler)
    handler.headers = HTTPMessage()
    for name, value in headers:
        handler.headers[name] = value
    handler.path = path
    handler.routes = {path: lambda headers, body: (calls.append(body) or 204, "text/plain", b"")} if known else {}
    handler.rfile, handler.wfile = reader or io.BytesIO(body), io.BytesIO()
    handler.connection = SimpleNamespace(gettimeout=lambda: None, settimeout=timeouts.append)
    handler.send_response = statuses.append
    handler.send_error = statuses.append
    handler.send_header = lambda *args: None
    handler.end_headers = lambda: None
    handler.do_POST()
    return statuses, calls, timeouts


@pytest.mark.parametrize("headers,body,status", [
    ([], b"", 411), ([("Content-Length", "2"), ("Content-Length", "2")], b"{}", 400),
    ([("Content-Length", "2,2")], b"{}", 400), ([("Content-Length", "-1")], b"", 400),
    ([("Content-Length", "１２")], b"", 400), ([("Content-Length", "9" * 5000)], b"", 413),
    ([("Content-Length", str(serve.RESEND_MAX + 1))], b"", 413),
    ([("Content-Length", "2"), ("Transfer-Encoding", "chunked")], b"{}", 400),
    ([("Transfer-Encoding", "identity")], b"", 400),
    ([("Content-Length", "3")], b"{}", 400),
])
def test_resend_framing_rejected_before_route(headers, body, status):
    statuses, calls, _ = frame_request(headers, body)
    assert statuses == [status] and not calls


def test_resend_body_limit_timeout_and_total_deadline(monkeypatch):
    body = b"x" * serve.RESEND_MAX
    statuses, calls, timeouts = frame_request([("Content-Length", str(len(body)))], body)
    assert statuses == [204] and calls == [body] and timeouts[-1] is None
    def timeout(length):
        raise TimeoutError("socket timed out")
    assert frame_request([("Content-Length", "1")], reader=SimpleNamespace(read1=timeout))[:2] == ([408], [])
    # Trickle data cannot renew the total deadline.
    ticks = iter([0, 1, 6])
    monkeypatch.setattr(serve.time, "monotonic", lambda: next(ticks))
    statuses, calls, timeouts = frame_request([("Content-Length", "2")],
                                            reader=SimpleNamespace(read1=lambda length: b"x"))
    assert statuses == [408] and not calls and timeouts == [4, None]


def test_resend_framing_does_not_change_old_routes():
    body = b"x" * (serve.RESEND_MAX + 1)
    for route in ("/hooks/email", "/hooks/slack", "/hooks/quo", "/reply", "/up/token"):
        statuses, calls, timeouts = frame_request([("Content-Length", str(len(body)))], body, route)
        assert statuses == [204] and calls == [body] and not timeouts


@pytest.mark.parametrize("path", ["/tana", "/attio", "/linear"])
def test_unknown_context_route_consumes_legacy_body_before_404(path):
    body = b'{"emails":["' + b"x" * serve.RESEND_MAX + b'"],"domains":[]}'
    reader = io.BytesIO(body)
    statuses, calls, timeouts = frame_request([("Content-Length", str(len(body)))], path=path,
                                            reader=reader, known=False)
    assert statuses == [404] and not calls and not timeouts
    assert reader.tell() == len(body)  # deterministic guard for the Unix client body-write/close race


def test_wake_reconciles_durable_receipts_without_kick_before_downloads(tmp_path, monkeypatch):
    path, calls = receipt_db(tmp_path), []
    monkeypatch.setattr(inbox, "wake", lambda *a: calls.append("wake"))
    monkeypatch.setattr(inbox, "reconcile_resend_receipts", lambda *a: calls.append("receipts"))
    monkeypatch.setattr(inbox, "expire_attachments", lambda *a: calls.append("expiry"))
    for _ in range(2):  # restart/lost kick still traverses durable receipt work
        stop = threading.Event()
        def downloads(*args):
            calls.append("downloads")
            stop.set()
        monkeypatch.setattr(serve, "fetch_pending", downloads)
        serve.wake_loop(path, stop, every=0)
    assert calls == ["wake", "receipts", "downloads", "expiry"] * 2


@pytest.mark.parametrize("error,status", [(inbox.SendUnknown("outcome unknown; do not retry"), 504),
                                         (ValueError("Message confirmation pending; try after receipt"), 400)])
def test_reply_unknown_and_pending_statuses(tmp_path, monkeypatch, error, status):
    path, tid = setup(tmp_path)
    def fail(*args):
        raise error
    monkeypatch.setattr(inbox, "reply", fail)
    db = inbox.connect(path)
    result = serve.handle_reply(db, json.dumps({"thread_id": tid, "text": "retain me"}).encode(),
                                serve.Senders(made={2: Sender()}))
    db.close()
    assert result == (status, {"error": str(error)})


def test_email_hook_files_mail_under_the_connection_its_forward_reached():
    """Any number of email Connections: X-Inbox-To (the Worker's envelope recipient) picks the one whose Inbound
    address it is; a recipient no Connection has, or a Worker without the header, goes to the first Gmail."""
    db = inbox.connect()
    inbox.migrate(db)
    with db:
        for address, platform, inbound in [("a@x.io", "gmail", "a@inbox.agentmaple.com"),
                                           ("a@y.ai", "gmail", "a.y.ai@inbox.agentmaple.com"),
                                           ("f@fm.com", "fastmail", "fastmail@inbox.agentmaple.com")]:
            db.execute("INSERT INTO connections (platform, external_id, self_id, label, connected_at, inbound)"
                       " VALUES (?, ?, ?, 'Email', '2026-10-20T00:00:00+00:00', ?)", (platform, address, address, inbound))
    now = datetime(2026, 10, 20, 10, tzinfo=timezone.utc)
    ts = str(int(now.timestamp()))

    def deliver(n, to):
        raw = f"From: b@c.io\r\nSubject: hi\r\nMessage-ID: <m{n}@c.io>\r\n\r\nhello".encode()
        headers = {"X-Inbox-Timestamp": ts} | ({"X-Inbox-To": to} if to is not None else {})
        signed = ts.encode() + b"." + (to.encode() + b"." if to is not None else b"") + raw
        headers["X-Inbox-Signature"] = "sha256=" + hmac.new(b"k", signed, hashlib.sha256).hexdigest()
        platform = serve.HOOKS["/hooks/email"](headers)
        assert serve.intake(db, platform, headers, raw, b"k", now)[0] == 204
        return db.execute("SELECT c.external_id FROM activities a JOIN threads t ON t.id = a.thread_id JOIN channels ch"
                          " ON ch.id = t.channel_id JOIN connections c ON c.id = ch.connection_id WHERE a.native_id = ?",
                          (f"<m{n}@c.io>",)).fetchone()[0]

    assert deliver(1, "A.Y.AI@inbox.agentmaple.com") == "a@y.ai"
    assert deliver(2, "a@inbox.agentmaple.com") == "a@x.io"
    assert deliver(3, "fastmail@inbox.agentmaple.com") == "f@fm.com"
    assert deliver(4, "nobody@inbox.agentmaple.com") == "a@x.io"
    assert deliver(5, None) == "a@x.io"


def test_init_connects_each_gmail_address_with_its_own_inbound_address(tmp_path, capsys):
    from comms_terminal import main
    path = str(tmp_path / "inbox.db")
    for _ in range(2):  # idempotent
        main.main(["--db", path, "init", "--gmail", "Jeremy@agentmaple.com", "--gmail", "jeremy@agentmaple.ai"])
    db = inbox.connect(path)
    assert [tuple(r) for r in db.execute("SELECT platform, self_id, inbound FROM connections ORDER BY id")] == [
        ("gmail", "jeremy@agentmaple.com", "jeremy.agentmaple.com@inbox.agentmaple.com"),
        ("gmail", "jeremy@agentmaple.ai", "jeremy.agentmaple.ai@inbox.agentmaple.com")]
    assert "forward its mail to jeremy.agentmaple.ai@inbox.agentmaple.com" in capsys.readouterr().out


@pytest.mark.parametrize("relay", [False, True])
def test_private_slack_reactions_socket_and_command_relay_preserve_reply_route(tmp_path, relay):
    import stat
    import sys
    from comms_terminal import remote, slack

    path, tid = setup(tmp_path)
    db = inbox.connect(path)
    t = inbox.ingest(db, inbox.Incoming(2, "D0ANA", "im", "MESSAGE", "D0ANA:100.2",
                     inbox.Identity("slack_user", "U0ANA"), datetime.now(timezone.utc), "Captured reply",
                     data={"channel": "D0ANA", "ts": "100.2", "thread_ts": "100.1", "kind": "im"}))
    message = next(m for m in inbox.thread_view(db, t.id)["messages"] if m["native_id"] == "D0ANA:100.2")
    reads, writes = [], []

    def get(url, token, params):
        reads.append((url, params))
        if url.endswith("auth.test"):
            return {"ok": True, "team_id": "T0", "user_id": "U0JEREMY"}
        return {"ok": True, "type": "message", "channel": "D0ANA",
                "message": {"type": "message", "ts": "100.2"}}

    def post(url, token, payload):
        writes.append((url, payload))
        return {"ok": True, "ts": "101.0"}

    sender = slack.SlackSender("fake", post=post, client=slack.SlackClient("fake", get=get))
    sock = str(tmp_path / "reactions.sock")
    server = serve.reply_server(path, sock, serve.Senders(made={2: sender}))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    route = [sys.executable, "-I", "-c", remote.RELAY.replace(repr(remote.SERVICE_SOCKET), repr(sock))] if relay else sock
    request = {"op": "read", "thread_id": t.id, "message_id": message["id"], "connection_id": 2}
    before = tuple(db.iterdump())
    try:
        assert stat.S_IMODE(__import__("os").stat(sock).st_mode) == 0o600
        assert _post(route, "/slack-reactions", request) == (
            200, {"outcome": "ready", **{k: request[k] for k in ("thread_id", "message_id", "connection_id")},
                  "revision": message["revision"], "groups": []})
        status, out = _post(route, "/slack-reactions", request | {
            "op": "add", "name": "eyes", "expected_revision": message["revision"]})
        assert status == 200 and out["outcome"] == "confirmed"
        assert writes == [("https://slack.com/api/reactions.add", {"channel": "D0ANA", "timestamp": "100.2", "name": "eyes"})]
        assert tuple(db.iterdump()) == before
        assert _post(route, "/slack-reactions", request | {"name": None})[0] == 400
        assert len(writes) == 1
        assert socket_reply(route)(t.id, "Reply still works", [], [])["status_detail"] == "WAITING_FOR_CUSTOMER"
        assert writes[-1][0].endswith("chat.postMessage")
        assert writes[-1][1]["thread_ts"] == "100.1"
    finally:
        server.shutdown()
        server.server_close()
        db.close()


def test_slack_reactions_is_not_a_public_hook_or_remote_ledger_call(tmp_path, monkeypatch):
    from comms_terminal import remote
    path, _ = setup(tmp_path)
    monkeypatch.setattr(serve, "credential", lambda name: None)
    public = serve.hooks_server(path, "127.0.0.1", 0, client=object())
    try:
        assert "/slack-reactions" not in public.RequestHandlerClass.routes
        assert not any("reaction" in name for name in remote.CALLS)
    finally:
        public.server_close()
